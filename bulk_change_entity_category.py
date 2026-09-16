#!/usr/bin/env python3
"""
Apply category changes from a JSON file to a single book.

categories.json format: {"<untranslated>": "<new category>", ...}

Usage:
    python3 bulk_change_entity_category.py --book-id 16 --file categories.json --dry-run
    python3 bulk_change_entity_category.py --book-id 16 --file categories.json
"""

import argparse
import json
import sys

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

from change_entity_category import (
    find_entities,
    known_categories,
    update_category,
)


def main():
    parser = argparse.ArgumentParser(description="Bulk change entity categories from a JSON file.")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--file", default="categories.json",
                        help='JSON file mapping untranslated → new category, e.g. {"天靈根": "life simulator talents"}.')
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to the database.")
    parser.add_argument("--force", action="store_true",
                        help="Allow setting a category not in the book's template/in-use set.")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        changes = json.load(f)

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: a failed category update raises instead of silently no-op'ing.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    valid_cats = known_categories(db_manager, args.book_id)
    unknown = sorted({c for c in changes.values() if c not in valid_cats})
    if unknown and not args.force:
        print(f"Unknown categories for book {args.book_id}: {unknown}")
        print(f"Known categories:")
        for c in valid_cats:
            print(f"  {c}")
        print("Pass --force to use them anyway.")
        sys.exit(1)

    print(f"Book ID:  {args.book_id}")
    print(f"Entries:  {len(changes)}")
    print(f"Mode:     {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 70)

    n_updated = 0
    n_unchanged = 0
    n_missing = 0
    n_ambiguous = 0

    for untranslated, new_category in changes.items():
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

        eid, old_cat, trans = matches[0]
        if old_cat == new_category:
            print(f"  ⏭️  UNCHANGED: {untranslated!r} already in {new_category!r}")
            n_unchanged += 1
            continue

        action = "WOULD UPDATE" if args.dry_run else "UPDATE"
        print(f"  ✅ {action}: {untranslated!r}  [{old_cat}] → [{new_category}]  (translation: {trans!r})")
        if not args.dry_run:
            update_category(db_manager, eid, new_category)
        n_updated += 1

    print("=" * 70)
    print(f"Updated:    {n_updated}")
    print(f"Unchanged:  {n_unchanged}")
    print(f"Not found:  {n_missing}")
    print(f"Ambiguous:  {n_ambiguous}")


if __name__ == "__main__":
    main()
