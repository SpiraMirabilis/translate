#!/usr/bin/env python3
"""Run the character-count corrector on every chapter in a book (or all books).

Chinese prose describes written names/phrases by their 字 (character) count;
English readers count words. This pass finds "<number> characters|words|..."
mentions, asks a capable model to recount in English words (and to skip false
positives like people or the 八字/BaZi astrology term), and rewrites them.

The work happens in two phases so the (token-spending) model pass runs once:

    1. DRY RUN  — compute corrections, print the diff, and save a plan JSON to
                  /tmp. Nothing is written to the DB.
    2. --apply  — load that saved plan and write it to the DB verbatim. No
                  re-running of the model, so what you reviewed is exactly what
                  gets applied.

The model is required (the correction is entirely model-driven). It defaults to
the `character_fix_model` setting; override with --model.

A persistent decision cache (the `character_fix_cache` table) means re-running
over a book only queries the model for new or changed text — already-decided
matches are free. Disable with --no-cache.

A book_id (or "all") is required.

Usage:
    python run_character_fix_book.py <book_id>            # dry run, default model, cached
    python run_character_fix_book.py all                  # process every book, dry run
    python run_character_fix_book.py <book_id> --model claude:claude-opus-4-8  # override model
    python run_character_fix_book.py <book_id> --no-cache # ignore the decision cache
    python run_character_fix_book.py <book_id> --apply    # apply the saved plan
"""

import hashlib
import json
import os
import sys

import settings_store
from character_count_fixer import correct_character_counts
from character_fix_cache import CharacterFixCache
from db_backend import create_backend


def _plan_path(book_arg):
    """Deterministic plan-file path so --apply can find the matching dry run."""
    return os.path.join("/tmp", f"t9_charfix_{book_arg}.json")


def _hash(text):
    """Hash a chapter's stored content so we can detect drift before applying."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_lines(raw_content):
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError:
        return raw_content.split("\n")


def build_plan(cursor, book_ids, model, cache=None):
    """Run the corrector for the given books and return a plan dict (no DB writes).

    Returns (chapters_plan, total_changed_lines) where chapters_plan maps
    str(chapter_id) -> {book_id, chapter_number, corrected, source_hash}.
    """
    chapters_plan = {}
    total_changes = 0

    for book_id in book_ids:
        cursor.execute(
            "SELECT id, chapter_number, translated_content FROM chapters WHERE book_id = ? ORDER BY chapter_number",
            (book_id,),
        )
        rows = cursor.fetchall()

        if not rows:
            print(f"No chapters found for book {book_id}.")
            continue

        print(f"Processing {len(rows)} chapters in book {book_id}  [DRY RUN]...\n")

        for chapter_id, chapter_number, raw_content in rows:
            lines = _load_lines(raw_content)
            corrected = correct_character_counts(lines, model=model, cache=cache)

            changed = sum(1 for a, b in zip(lines, corrected) if a != b)
            if changed:
                total_changes += changed
                print(f"  Ch {chapter_number}: {changed} line(s) updated")
                for orig, conv in zip(lines, corrected):
                    if orig != conv:
                        print(f"    - {orig}")
                        print(f"    + {conv}")
                chapters_plan[str(chapter_id)] = {
                    "book_id": book_id,
                    "chapter_number": chapter_number,
                    "corrected": corrected,
                    "source_hash": _hash(raw_content),
                }
            else:
                print(f"  Ch {chapter_number}: no changes")

        print()

    return chapters_plan, total_changes


def apply_plan(cursor, plan):
    """Write a saved plan to the DB. Skips chapters whose source drifted.

    Returns (applied, skipped)."""
    chapters = plan.get("chapters", {})
    applied = 0
    skipped = 0

    for chapter_id, entry in chapters.items():
        cursor.execute(
            "SELECT translated_content FROM chapters WHERE id = ?", (int(chapter_id),)
        )
        row = cursor.fetchone()
        ch_label = entry.get("chapter_number", f"id {chapter_id}")

        if row is None:
            print(f"  Ch {ch_label}: chapter no longer exists, skipping")
            skipped += 1
            continue

        if _hash(row[0]) != entry.get("source_hash"):
            print(f"  Ch {ch_label}: source changed since dry run, skipping (re-run dry run)")
            skipped += 1
            continue

        cursor.execute(
            "UPDATE chapters SET translated_content = ? WHERE id = ?",
            (json.dumps(entry["corrected"], ensure_ascii=False), int(chapter_id)),
        )
        applied += 1
        print(f"  Ch {ch_label}: applied")

    return applied, skipped


def main():
    apply_changes = "--apply" in sys.argv
    no_cache = "--no-cache" in sys.argv

    # Extract an explicit --model override, if given.
    model_override = None
    argv = list(sys.argv[1:])
    for i, arg in enumerate(argv):
        if arg == "--model" and i + 1 < len(argv):
            model_override = argv[i + 1]
            break
    args = [a for a in argv if not a.startswith("--") and (not model_override or a != model_override)]

    # Resolve the model: explicit override > settings default.
    model = model_override or settings_store.get("character_fix_model")

    if not args:
        print("Error: a book_id (or 'all') is required.")
        print("Usage: python run_character_fix_book.py <book_id|all> [--apply] [--model SPEC]")
        sys.exit(1)

    if args[0].lower() == "all":
        book_arg = "all"
    else:
        try:
            book_arg = int(args[0])
        except ValueError:
            print(f"Error: invalid book_id '{args[0]}'. Expected an integer or 'all'.")
            sys.exit(1)

    plan_path = _plan_path(book_arg)

    backend = create_backend()
    conn = backend.get_connection()
    cursor = conn.cursor()

    # ── Apply phase: load the saved plan, write it verbatim ──────────────
    if apply_changes:
        if model_override:
            print("Note: --model is ignored with --apply (the saved plan is applied as-is).\n")
        if not os.path.exists(plan_path):
            print(f"No saved plan found at {plan_path}.")
            print(f"Run a dry run first:  python run_character_fix_book.py {book_arg}")
            conn.close()
            return

        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)

        n_chapters = len(plan.get("chapters", {}))
        print(f"Applying saved plan from {plan_path} ({n_chapters} chapter(s))...\n")
        applied, skipped = apply_plan(cursor, plan)
        conn.commit()
        conn.close()

        suffix = f", skipped {skipped} (stale/missing)" if skipped else ""
        print(f"\nDone. Applied {applied} chapter(s){suffix}.")
        return

    # ── Dry-run phase: compute corrections, save plan, print diff ────────
    if not model:
        print("Error: no model configured. Set the `character_fix_model` setting or pass --model SPEC.")
        conn.close()
        sys.exit(1)

    if book_arg == "all":
        cursor.execute("SELECT DISTINCT book_id FROM chapters WHERE book_id IS NOT NULL ORDER BY book_id")
        book_ids = [row[0] for row in cursor.fetchall()]
        if not book_ids:
            print("No books with chapters found.")
            conn.close()
            return
        print(f"Found {len(book_ids)} book(s) to process: {book_ids}\n")
    else:
        book_ids = [book_arg]

    cache = None if no_cache else CharacterFixCache()
    print(f"Model: {model}")
    print(f"Cache: {'disabled (--no-cache)' if no_cache else 'on (character_fix_cache table)'}\n")

    chapters_plan, total_changes = build_plan(cursor, book_ids, model, cache=cache)
    if cache:
        cache.close()
    conn.close()

    plan = {
        "book_arg": book_arg,
        "book_ids": book_ids,
        "model": model,
        "chapters": chapters_plan,
    }
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"Done. {total_changes} line(s) across {len(chapters_plan)} chapter(s) would be updated.")
    print(f"Plan saved to {plan_path}")
    if chapters_plan:
        print(f"Review the diff above, then apply with:")
        print(f"  python run_character_fix_book.py {book_arg} --apply")


if __name__ == "__main__":
    main()
