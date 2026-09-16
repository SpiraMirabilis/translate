#!/usr/bin/env python3
"""
Apply translation corrections from entities.json to a single book,
optionally substituting across the book's translated chapters.

entities.json format: {"<untranslated>": "<new translation>", ...}

Usage:
    python3 bulk_correct_entities.py --book-id 15 --substitute --dry-run
    python3 bulk_correct_entities.py --book-id 15 --substitute
    python3 bulk_correct_entities.py --book-id 15 --safer-substitute
"""

import argparse
import json
import sys

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

from correct_entity_translation import (
    find_entity,
    update_entity_translation,
    substitute_in_chapters,
    find_chapters_with_untranslated,
    count_substitutions,
    count_note_substitutions,
)


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

    not_found = []
    ambiguous = []
    no_change = []
    updated = []
    total_chapters_changed = 0
    total_notes_changed = 0

    for untranslated, new_translation in corrections.items():
        matches = find_entity(db_manager, args.book_id, untranslated)
        if not matches:
            print(f"  ❌ NOT FOUND: {untranslated!r}")
            not_found.append(untranslated)
            continue
        if len(matches) > 1:
            cats = [m[1] for m in matches]
            print(f"  ⚠️  AMBIGUOUS ({len(matches)} categories: {cats}): {untranslated!r}")
            ambiguous.append(untranslated)
            continue

        entity_id, category, old_translation = matches[0]
        if old_translation == new_translation:
            print(f"  ⏭️  UNCHANGED [{category}] {untranslated!r} → {new_translation!r}")
            no_change.append(untranslated)
            continue

        chapter_ids = None
        sub_count = 0
        note_count = 0
        if do_substitute:
            if args.safer_substitute:
                chapter_ids = find_chapters_with_untranslated(
                    db_manager, args.book_id, untranslated
                )
            sub_count = count_substitutions(
                db_manager, args.book_id, old_translation, new_translation,
                chapter_ids, args.word_boundary
            )
            note_count = count_note_substitutions(
                db_manager, args.book_id, old_translation, new_translation,
                chapter_ids, args.word_boundary
            )

        action = "WOULD UPDATE" if args.dry_run else "UPDATE"
        print(f"  ✅ {action} [{category}] {untranslated!r}: "
              f"{old_translation!r} → {new_translation!r}"
              + (f"  (substitute in {sub_count} chapters, {note_count} notes)"
                 if do_substitute else ""))

        updated.append((untranslated, old_translation, new_translation, sub_count, note_count))

        if not args.dry_run:
            update_entity_translation(db_manager, entity_id, new_translation, old_translation)
            if do_substitute:
                affected, notes_affected = substitute_in_chapters(
                    db_manager, args.book_id, old_translation, new_translation,
                    chapter_ids, args.word_boundary
                )
                total_chapters_changed += affected
                total_notes_changed += notes_affected

    print("=" * 70)
    print(f"Updates:     {len(updated)}")
    print(f"Unchanged:   {len(no_change)}")
    print(f"Not found:   {len(not_found)}")
    print(f"Ambiguous:   {len(ambiguous)}")
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
