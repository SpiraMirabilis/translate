"""
Redo entity substitutions to fix the case-preservation bug (e.g. "HeavenNet"
became "Heavennet" because the old match_case ran .capitalize() per word and
flattened internal capitalization).

The bug only manifests on entities whose `translation` differs from the naive
title-cased rewrite (`" ".join(w.capitalize() for w in translation.split())`)
— i.e. has internal caps, hyphenated compounds with caps after the hyphen,
acronyms, or special chars like "VitaSpirit™". Title-case-only translations
("Lu Qingyun") were never affected.

For each at-risk entity, the script searches chapter text case-insensitively
for the correct translation and rewrites every match with the corrected
match_case (which preserves new_translation's internal caps).

Usage:
    python redo_substitutions.py --dry-run                # all books
    python redo_substitutions.py --book 15 --dry-run      # one book
    python redo_substitutions.py --apply                  # commit, all books
    python redo_substitutions.py --book 15 --apply
"""

import argparse
import json
import re
from itertools import zip_longest

from db_backend import create_backend


def make_match_case(new_translation):
    """Mirror of the fixed match_case in database.py / web/api/entities.py."""
    def match_case(match):
        matched_text = match.group()
        old_words = matched_text.split()
        new_words = new_translation.split()
        transformed = []
        for old_w, new_w in zip_longest(old_words, new_words, fillvalue=""):
            if not new_w:
                continue
            if not old_w:
                transformed.append(new_w)
                continue
            if old_w.isupper() and len(old_w) > 1:
                transformed.append(new_w.upper())
            elif old_w[0].isupper():
                transformed.append(new_w[0].upper() + new_w[1:])
            elif old_w[0].islower():
                transformed.append(new_w[0].lower() + new_w[1:])
            else:
                transformed.append(new_w)
        return " ".join(transformed).strip()
    return match_case


def damaged_by_capitalize(value):
    """
    Return True if the buggy per-word .capitalize() in the old match_case could
    have damaged this translation. We require an uppercase letter past
    position 0 — that's the only kind of casing the bug could have flattened.
    Translations that are entirely lowercase ("athletic contest") or only
    capitalized at word starts ("Lu Qingyun" → only the L survives the per-word
    capitalize) are excluded; redoing them would either be a no-op or fight
    the user's intentional lowercase styling.
    """
    if not value:
        return False
    return value.capitalize() != value and any(c.isupper() for c in value[1:])


def process_book(cur, book_id, dry_run, show_samples, applied_writes):
    """Process one book; return (entities_at_risk, chapters_changed, lines_changed)."""
    cur.execute(
        "SELECT id, category, untranslated, translation, incorrect_translation "
        "FROM entities "
        "WHERE book_id = ? "
        "ORDER BY id",
        (book_id,)
    )
    rows = cur.fetchall()
    at_risk = [r for r in rows if damaged_by_capitalize(r[3])]
    if not at_risk:
        return 0, 0, 0

    cur.execute(
        "SELECT id, chapter_number, translated_content "
        "FROM chapters WHERE book_id = ? ORDER BY chapter_number",
        (book_id,)
    )
    chapters = cur.fetchall()

    compiled = []
    for eid, cat, untrans, new_t, old_t in at_risk:
        pat = re.compile(re.escape(new_t), re.IGNORECASE)
        compiled.append((eid, untrans, old_t, new_t, pat, make_match_case(new_t)))

    per_entity_counts = {eid: 0 for eid, *_ in compiled}
    per_entity_samples = {eid: [] for eid, *_ in compiled}
    chapter_updates = []
    chapters_changed = 0
    total_lines_changed = 0

    for ch_id, ch_num, raw in chapters:
        if not raw:
            continue
        try:
            lines = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(lines, list):
            continue

        chapter_changed = False
        for i, line in enumerate(lines):
            if not isinstance(line, str) or not line:
                continue
            new_line = line
            for eid, untrans, old_t, new_t, pat, mc in compiled:
                if not pat.search(new_line):
                    continue
                replaced = pat.sub(mc, new_line)
                if replaced != new_line:
                    if len(per_entity_samples[eid]) < show_samples:
                        per_entity_samples[eid].append(
                            (ch_num, line[:200], replaced[:200])
                        )
                    per_entity_counts[eid] += 1
                    new_line = replaced
            if new_line != line:
                lines[i] = new_line
                chapter_changed = True
                total_lines_changed += 1

        if chapter_changed:
            chapters_changed += 1
            chapter_updates.append((ch_id, json.dumps(lines, ensure_ascii=False)))

    nonzero = [(eid, untrans, old_t, new_t, per_entity_counts[eid])
               for eid, untrans, old_t, new_t, _, _ in compiled
               if per_entity_counts[eid] > 0]
    nonzero.sort(key=lambda x: -x[4])

    if nonzero:
        print(f"\n--- Book {book_id}: {len(at_risk)} at-risk entities, "
              f"{len(nonzero)} with matches, {chapters_changed} chapters, "
              f"{total_lines_changed} lines")
        for eid, untrans, old_t, new_t, count in nonzero:
            print(f"    id={eid:5d}  zh={untrans!r:25s}  {new_t!r:50s}  {count:4d} lines")

        if dry_run and show_samples:
            for eid, untrans, old_t, new_t, count in nonzero:
                samples = per_entity_samples[eid]
                if not samples:
                    continue
                print(f"\n    [entity {eid}] {new_t!r}")
                for ch_num, before, after in samples:
                    print(f"      ch{ch_num} BEFORE: {before}")
                    print(f"      ch{ch_num} AFTER : {after}")
    else:
        print(f"--- Book {book_id}: {len(at_risk)} at-risk entities, no matches in chapters")

    if not dry_run and chapter_updates:
        for ch_id, blob in chapter_updates:
            cur.execute(
                "UPDATE chapters SET translated_content = ? WHERE id = ?",
                (blob, ch_id),
            )
        applied_writes[0] += len(chapter_updates)

    return len(at_risk), chapters_changed, total_lines_changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", type=int, default=None,
                    help="Limit to a single book id; omit to process all books")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show-samples", type=int, default=2,
                    help="Show up to N sample diffs per entity in dry-run output")
    args = ap.parse_args()

    if args.dry_run == args.apply:
        ap.error("Pass exactly one of --dry-run or --apply")

    backend = create_backend()
    print(f"Backend: {backend.name} -> {backend.db_path}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")
    conn = backend.get_connection()
    cur = conn.cursor()

    if args.book is not None:
        book_ids = [args.book]
    else:
        cur.execute("SELECT id FROM books ORDER BY id")
        book_ids = [row[0] for row in cur.fetchall()]
        print(f"Found {len(book_ids)} books")

    applied_writes = [0]
    grand_at_risk = 0
    grand_chapters = 0
    grand_lines = 0
    for bid in book_ids:
        a, c, l = process_book(cur, bid, args.dry_run, args.show_samples, applied_writes)
        grand_at_risk += a
        grand_chapters += c
        grand_lines += l

    if not args.dry_run:
        conn.commit()

    print()
    print("=" * 78)
    print(f"Total at-risk entities scanned: {grand_at_risk}")
    print(f"Total chapters touched:         {grand_chapters}")
    print(f"Total lines rewritten:          {grand_lines}")
    if args.apply:
        print(f"Total chapter writes committed: {applied_writes[0]}")
    else:
        print("Dry-run complete; no changes written. Re-run with --apply to commit.")

    conn.close()


if __name__ == "__main__":
    main()
