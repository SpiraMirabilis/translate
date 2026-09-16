#!/usr/bin/env python3
"""
CLI wrapper around pronoun_repair.repair_pronouns_for_entity.

Originally written as a one-off for Kuang Tianqing in book 15 (where it
corrected 97 paragraphs across 39 chapters). The bones are now generic; this
script is just a CLI that drives the pronoun_repair module for ad-hoc /
scripted use.

Usage:
    # Dry run: write a unified diff to <out>.diff, do not touch the DB
    python entity_gender_repair.py --book-id 15 --untranslated 匡天卿 --target-gender female

    # Actually commit the corrections
    python entity_gender_repair.py --book-id 15 --untranslated 匡天卿 --target-gender female --commit

    # Limit to a specific chapter range
    python entity_gender_repair.py --book-id 15 --untranslated 匡天卿 --target-gender female \\
        --chapters 631-857

    # Add extra name forms to scan for (e.g. nicknames)
    python entity_gender_repair.py --book-id 15 --untranslated 匡天卿 --target-gender female \\
        --extra-name "Tianqing"
"""

import argparse
import difflib
import sys
from pathlib import Path

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger
from pronoun_repair import repair_pronouns_for_entity, VALID_GENDERS


def parse_chapter_spec(spec: str) -> list[int]:
    """Parse '631-857' or '631,640,650' or mixes into a sorted unique list."""
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def resolve_entity_id(db: DatabaseManager, book_id: int, untranslated: str) -> int:
    """Look up entity ID from (book_id, untranslated) pair."""
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM entities WHERE untranslated = ? AND book_id = ?",
        (untranslated, book_id),
    )
    row = cursor.fetchone()
    if not row:
        raise SystemExit(
            f"No entity found with untranslated={untranslated!r} in book_id={book_id}"
        )
    return row["id"] if hasattr(row, "keys") else row[0]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--book-id", type=int, required=True, help="Book ID containing the entity")
    p.add_argument("--untranslated", required=True,
                   help="Untranslated key of the entity (e.g. the original Chinese name)")
    p.add_argument("--target-gender", required=True, choices=list(VALID_GENDERS),
                   help="Gender to enforce on pronouns referring to the entity")
    p.add_argument("--chapters", default="",
                   help="Optional chapter range/list (e.g. '631-857' or '5,10,42'); "
                        "default = all chapters in the book that mention the entity")
    p.add_argument("--extra-name", action="append", default=[],
                   help="Additional name form(s) to scan paragraphs for (repeatable)")
    p.add_argument("--commit", action="store_true",
                   help="Actually write corrections to the database (default: dry run -> diff file)")
    p.add_argument("--diff-out", default="pronoun_repair_preview.diff",
                   help="Where to write the unified diff in dry-run mode")
    args = p.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    entity_id = resolve_entity_id(db, args.book_id, args.untranslated)
    print(f"Resolved entity_id={entity_id} for {args.untranslated!r} in book {args.book_id}", file=sys.stderr)

    chapters = parse_chapter_spec(args.chapters) if args.chapters else None

    def progress(i, total, cn):
        if cn is not None and (i % 10 == 0 or i == total):
            print(f"  [{i}/{total}] chapter {cn}", file=sys.stderr)

    summary = repair_pronouns_for_entity(
        db,
        entity_id,
        args.target_gender,
        chapter_numbers=chapters,
        extra_names=args.extra_name or None,
        progress_cb=progress,
        dry_run=not args.commit,
    )

    print(f"\nentity:           {summary['character_name']} (id={summary['entity_id']}, book_id={summary['book_id']})")
    print(f"target_gender:    {summary['target_gender']}")
    print(f"chapters_scanned: {summary['chapters_scanned']}")
    print(f"windows_examined: {summary['windows_examined']}")
    print(f"chapters_changed: {summary['chapters_changed']}")
    print(f"paragraphs_changed: {summary['paragraphs_changed']}")
    print(f"errors:           {len(summary['errors'])}")

    if not args.commit and summary.get("diffs"):
        diffs_text = []
        for d in summary["diffs"]:
            diffs_text.append("\n".join(difflib.unified_diff(
                d["before"], d["after"],
                fromfile=f"chapter_{d['chapter_number']}_before",
                tofile=f"chapter_{d['chapter_number']}_after",
                lineterm="", n=1,
            )))
        Path(args.diff_out).write_text("\n\n".join(diffs_text))
        print(f"\nDry run only. Diff written to {args.diff_out}. Re-run with --commit to apply.")
    elif args.commit:
        print("\nCOMMITTED to database.")
    else:
        print("\nNo changes needed.")


if __name__ == "__main__":
    main()
