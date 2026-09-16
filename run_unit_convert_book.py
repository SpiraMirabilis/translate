#!/usr/bin/env python3
"""Run unit_converter on every chapter in a book (or all books).

The work happens in two phases so the (potentially expensive, token-spending)
conversion runs exactly once:

    1. DRY RUN  — compute conversions, print the diff, and save a plan JSON to
                  /tmp. Nothing is written to the DB.
    2. --apply  — load that saved plan and write it to the DB verbatim. No
                  re-running of unit_converter / the cleaning model, so what you
                  reviewed is exactly what gets applied.

The cleaning model (AI false-positive filtering) is ON by default, using the
`unit_cleaning_model` setting. Override it with --cleaning-model, or turn it off
entirely with --no-cleaning-model for a pure-regex run.

A book_id (or "all") is required.

Usage:
    python run_unit_convert_book.py <book_id>           # dry run, default cleaning model
    python run_unit_convert_book.py all                 # process every book, dry run
    python run_unit_convert_book.py <book_id> --cleaning-model gemini:gemini-2.0-flash  # override cleaning model
    python run_unit_convert_book.py <book_id> --no-cleaning-model # pure regex, no AI filtering
    python run_unit_convert_book.py <book_id> --apply   # apply the saved plan
"""

import hashlib
import json
import os
import sys

import settings_store
from db_backend import create_backend
from unit_converter import convert_units


def _plan_path(book_arg):
    """Deterministic plan-file path so --apply can find the matching dry run."""
    return os.path.join("/tmp", f"t9_unit_convert_{book_arg}.json")


def _hash(text):
    """Hash a chapter's stored content so we can detect drift before applying."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_lines(raw_content):
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError:
        return raw_content.split("\n")


def build_plan(cursor, book_ids, cleaning_model):
    """Run conversions for the given books and return a plan dict (no DB writes).

    Returns (chapters_plan, total_changed_lines) where chapters_plan maps
    str(chapter_id) -> {book_id, chapter_number, converted, source_hash}.
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
            converted = convert_units(lines, cleaning_model=cleaning_model)

            changed = sum(1 for a, b in zip(lines, converted) if a != b)
            if changed:
                total_changes += changed
                print(f"  Ch {chapter_number}: {changed} line(s) updated")
                for orig, conv in zip(lines, converted):
                    if orig != conv:
                        print(f"    - {orig}")
                        print(f"    + {conv}")
                chapters_plan[str(chapter_id)] = {
                    "book_id": book_id,
                    "chapter_number": chapter_number,
                    "converted": converted,
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
            (json.dumps(entry["converted"], ensure_ascii=False), int(chapter_id)),
        )
        applied += 1
        print(f"  Ch {ch_label}: applied")

    return applied, skipped


def main():
    apply_changes = "--apply" in sys.argv
    no_cleaning = "--no-cleaning-model" in sys.argv

    # Extract an explicit --cleaning-model override, if given.
    cleaning_override = None
    argv = list(sys.argv[1:])
    for i, arg in enumerate(argv):
        if arg == "--cleaning-model" and i + 1 < len(argv):
            cleaning_override = argv[i + 1]
            break
    args = [a for a in argv if not a.startswith("--") and (not cleaning_override or a != cleaning_override)]

    # Resolve the cleaning model: explicit override > settings default,
    # unless the user opted out with --no-cleaning-model.
    if no_cleaning:
        cleaning_model = None
    elif cleaning_override:
        cleaning_model = cleaning_override
    else:
        cleaning_model = settings_store.get("unit_cleaning_model") or None

    if not args:
        print("Error: a book_id (or 'all') is required.")
        print("Usage: python run_unit_convert_book.py <book_id|all> [--apply] [--cleaning-model SPEC | --no-cleaning-model]")
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
        if cleaning_override or no_cleaning:
            print("Note: cleaning-model flags are ignored with --apply (the saved plan is applied as-is).\n")
        if not os.path.exists(plan_path):
            print(f"No saved plan found at {plan_path}.")
            print(f"Run a dry run first:  python run_unit_convert_book.py {book_arg}")
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

    # ── Dry-run phase: compute conversions, save plan, print diff ────────
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

    if cleaning_model:
        print(f"Cleaning model: {cleaning_model} (AI false-positive filtering on)\n")
    else:
        print("Cleaning model: none (pure regex)\n")

    chapters_plan, total_changes = build_plan(cursor, book_ids, cleaning_model)
    conn.close()

    plan = {
        "book_arg": book_arg,
        "book_ids": book_ids,
        "cleaning_model": cleaning_model,
        "chapters": chapters_plan,
    }
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"Done. {total_changes} line(s) across {len(chapters_plan)} chapter(s) would be updated.")
    print(f"Plan saved to {plan_path}")
    if chapters_plan:
        print(f"Review the diff above, then apply with:")
        print(f"  python run_unit_convert_book.py {book_arg} --apply")


if __name__ == "__main__":
    main()
