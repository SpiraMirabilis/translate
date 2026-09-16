#!/usr/bin/env python3
"""
Set (or clear) entity notes from a JSON file for a single book.

notes.json format: {"<untranslated>": "<note text>", ...}
Use an empty string or null as the note to clear it.

Usage:
    python3 bulk_set_entity_note.py --book-id 42 --file notes.json --dry-run
    python3 bulk_set_entity_note.py --book-id 42 --file notes.json
"""

import argparse
import json

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

from change_entity_category import find_entities


def get_note(db_manager, entity_id):
    row = db_manager.get_entity_by_id(entity_id)
    return row["note"] if row else None


def set_note(db_manager, entity_id, note):
    """Set (or clear, with None) an entity's note via the repo method.

    Recorded in entity_note_revisions as a 'script' change, so a bulk sweep is
    revertible and shows up in the note history like any other write.
    """
    db_manager.update_entity_by_id(entity_id, note=note, note_author='script')


def main():
    parser = argparse.ArgumentParser(description="Bulk set entity notes from a JSON file.")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--file", default="notes.json",
                        help='JSON file mapping untranslated → note, e.g. {"블랙 실드": "Incantation; ..."}. '
                             'Empty string or null clears the note.')
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to the database.")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        changes = json.load(f)

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: a failed note update raises instead of silently no-op'ing.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    print(f"Book ID:  {args.book_id}")
    print(f"Entries:  {len(changes)}")
    print(f"Mode:     {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 70)

    n_updated = 0
    n_unchanged = 0
    n_missing = 0
    n_ambiguous = 0

    for untranslated, new_note in changes.items():
        new_note = new_note or None
        matches = find_entities(db_manager, args.book_id, untranslated)
        if not matches:
            print(f"  ❌ NOT FOUND: {untranslated!r}")
            n_missing += 1
            continue
        if len(matches) > 1:
            cats = [m[1] for m in matches]
            print(f"  ⚠️  AMBIGUOUS ({len(matches)} categories: {cats}): {untranslated!r}")
            n_ambiguous += 1
            continue

        eid, cat, trans = matches[0]
        old_note = get_note(db_manager, eid)
        if old_note == new_note:
            print(f"  ⏭️  UNCHANGED: {untranslated!r}")
            n_unchanged += 1
            continue

        action = "WOULD SET" if args.dry_run else "SET"
        verb = "clear" if new_note is None else new_note
        print(f"  ✅ {action} [{cat}] {untranslated!r} ({trans!r}): {verb!r}")
        if not args.dry_run:
            set_note(db_manager, eid, new_note)
        n_updated += 1

    print("=" * 70)
    print(f"Updated:    {n_updated}")
    print(f"Unchanged:  {n_unchanged}")
    print(f"Not found:  {n_missing}")
    print(f"Ambiguous:  {n_ambiguous}")


if __name__ == "__main__":
    main()
