#!/usr/bin/env python3
"""
Apply translation corrections from entities.json to a single book,
optionally substituting across the book's translated chapters.

entities.json format: {"<untranslated>": "<new translation>", ...}
An entry's value may instead be an object naming the category, for a key that
exists in more than one category:
    {"<untranslated>": {"translation": "<new translation>", "category": "<category>"}}

Corrections apply in file order (a cascade: a later entry sees the chapters as
the earlier ones left them).

Usage:
    python3 bulk_correct_entities.py --book-id 15 --substitute --dry-run
    python3 bulk_correct_entities.py --book-id 15 --substitute
    python3 bulk_correct_entities.py --book-id 15 --safer-substitute
"""

import argparse
import json

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

from correct_entity_translation import correct_entity


def parse_correction(value):
    """Split an entities.json value into ``(translation, category_or_None)``."""
    if isinstance(value, dict):
        return value.get("translation"), value.get("category") or None
    return value, None


def iter_bulk_correct(db_manager, book_id: int, corrections: dict, *, mode: str = "none",
                      word_boundary: bool = False, dry_run: bool = False):
    """Yield correct_entity's result dict for each correction, in input order.

    Order matters when substituting: each correction sweeps the chapters as
    the previous ones left them (a cascade). Values are parsed by
    parse_correction, so an entry may name its category. Never prints. A
    generator so the CLI can report each entry as it lands.
    """
    for untranslated, value in corrections.items():
        new_translation, category = parse_correction(value)
        yield correct_entity(
            db_manager, book_id, untranslated, new_translation,
            category=category, mode=mode,
            word_boundary=word_boundary, dry_run=dry_run,
        )


def bulk_correct(db_manager, book_id: int, corrections: dict, *, mode: str = "none",
                 word_boundary: bool = False, dry_run: bool = False) -> list:
    """iter_bulk_correct, collected into a list."""
    return list(iter_bulk_correct(db_manager, book_id, corrections, mode=mode,
                                  word_boundary=word_boundary, dry_run=dry_run))


def main():
    parser = argparse.ArgumentParser(description="Bulk apply entity corrections from entities.json.")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--file", default="entities.json")
    parser.add_argument("--substitute", action="store_true",
                        help="Also replace old translation with new across translated chapters.")
    parser.add_argument("--safer-substitute", action="store_true",
                        help="Like --substitute, but restricts each substitution to chapters "
                             "whose source (untranslated) text contains that entity.")
    parser.add_argument("-w", "--word-boundary", action="store_true",
                        help="Make substitutions word-boundary safe: only replace whole-word "
                             "occurrences of each old translation (e.g. 'Dai' won't be rewritten "
                             "inside 'Daiyu'). Applies to --substitute and --safer-substitute.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to the database.")
    args = parser.parse_args()

    do_substitute = args.substitute or args.safer_substitute

    with open(args.file, "r", encoding="utf-8") as f:
        corrections = json.load(f)

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: failed entity updates / chapter substitutions raise instead
    # of silently returning — a bulk sweep should abort loudly, not skip rows.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    print(f"Book ID: {args.book_id}")
    print(f"Entries:  {len(corrections)}")
    sub_label = (' + safer-substitute' if args.safer_substitute
                 else ' + substitute' if args.substitute else '')
    if do_substitute and args.word_boundary:
        sub_label += ' (word-boundary)'
    print(f"Mode:     {'DRY RUN' if args.dry_run else 'APPLY'}{sub_label}")
    print("=" * 70)

    mode = ("safer" if args.safer_substitute
            else "substitute" if args.substitute else "none")

    not_found = []
    ambiguous = []
    no_change = []
    errors = []
    updated = []
    total_chapters_changed = 0
    total_notes_changed = 0

    for r in iter_bulk_correct(db_manager, args.book_id, corrections, mode=mode,
                               word_boundary=args.word_boundary, dry_run=args.dry_run):
        untranslated = r["untranslated"]
        new_translation = r["new_translation"]
        category = r["category"]
        status = r["status"]
        if status == "not_found":
            print(f"  ❌ NOT FOUND: {untranslated!r}"
                  + (f" in category {category!r}" if category else ""))
            not_found.append(untranslated)
            continue
        if status == "ambiguous":
            cats = [m["category"] for m in r["matches"]]
            print(f"  ⚠️  AMBIGUOUS ({len(cats)} categories: {cats}): {untranslated!r}")
            ambiguous.append(untranslated)
            continue
        if not r["ok"]:
            print(f"  ❌ ERROR: {untranslated!r}: {r['error']}")
            errors.append(untranslated)
            continue
        if status == "unchanged":
            print(f"  ⏭️  UNCHANGED [{r['category']}] {untranslated!r} → {new_translation!r}")
            no_change.append(untranslated)
            continue

        sub_count = r["chapter_substitutions"]
        note_count = r["note_substitutions"]
        action = "WOULD UPDATE" if args.dry_run else "UPDATE"
        print(f"  ✅ {action} [{r['category']}] {untranslated!r}: "
              f"{r['old_translation']!r} → {new_translation!r}"
              + (f"  (substitute in {sub_count} chapters, {note_count} notes)"
                 if do_substitute else ""))

        updated.append((untranslated, r["old_translation"], new_translation,
                        sub_count, note_count))
        if not args.dry_run:
            total_chapters_changed += sub_count
            total_notes_changed += note_count

    print("=" * 70)
    print(f"Updates:     {len(updated)}")
    print(f"Unchanged:   {len(no_change)}")
    print(f"Not found:   {len(not_found)}")
    print(f"Ambiguous:   {len(ambiguous)}")
    if errors:
        print(f"Errors:      {len(errors)}")
    if do_substitute:
        if args.dry_run:
            total = sum(c for _, _, _, c, _ in updated)
            total_notes = sum(n for _, _, _, _, n in updated)
            print(f"Chapters that would be touched (sum, may double-count): {total}")
            print(f"Entity notes that would be rewritten (sum):             {total_notes}")
        else:
            print(f"Chapter writes (sum across substitutions):              {total_chapters_changed}")
            print(f"Entity note writes (sum across substitutions):          {total_notes_changed}")


if __name__ == "__main__":
    main()
