#!/usr/bin/env python3
"""
Add multiple new entities to a book in one pass, from a JSON file.

Like add_entity.py but batched: refuses to overwrite existing entities, and
validates each entity's category against the book's known/template categories
(unless --force). entities.json may be either:

  - A list of objects:
      [
        {"category": "characters", "untranslated": "陆青云",
         "translation": "Lu Qingyun", "gender": "male",
         "origin_chapter": 1, "note": "..."},
        ...
      ]

  - A flat {untranslated: translation} map, in which case --category supplies
    the category for every entry:
      {"陆青云": "Lu Qingyun", "天剑宗": "Heavenly Sword Sect"}

Usage:
    python3 bulk_add_entity.py --book-id 5 --file new_entities.json --dry-run
    python3 bulk_add_entity.py --book-id 5 --file new_entities.json
    python3 bulk_add_entity.py --book-id 5 --category characters \
        --file names.json --dry-run
"""

import argparse
import json
import sys

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

from add_entity import (
    find_existing,
    known_categories,
    book_template_categories,
)


def normalize_entries(raw, default_category):
    """Return a list of dicts with at least category/untranslated/translation.

    Accepts either a list of objects or a flat {untranslated: translation} map.
    Raises ValueError on malformed input.
    """
    entries = []
    if isinstance(raw, dict):
        if not default_category:
            raise ValueError(
                "Flat {untranslated: translation} map requires --category."
            )
        for untranslated, translation in raw.items():
            entries.append({
                "category": default_category,
                "untranslated": untranslated,
                "translation": translation,
            })
        return entries

    if isinstance(raw, list):
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"Entry {i} is not an object: {item!r}")
            entry = dict(item)
            if not entry.get("category"):
                if not default_category:
                    raise ValueError(
                        f"Entry {i} has no 'category' and --category was not given."
                    )
                entry["category"] = default_category
            if not entry.get("untranslated"):
                raise ValueError(f"Entry {i} is missing 'untranslated'.")
            if not entry.get("translation"):
                raise ValueError(f"Entry {i} is missing 'translation'.")
            entries.append(entry)
        return entries

    raise ValueError("Top-level JSON must be a list or an object.")


def main():
    parser = argparse.ArgumentParser(description="Bulk add new entities to a book from a JSON file.")
    parser.add_argument("--book-id", type=int, required=True, help="Book ID to attach the entities to.")
    parser.add_argument("--file", default="entities.json",
                        help="JSON file of entities (list of objects or flat map). Default: entities.json")
    parser.add_argument("--category",
                        help="Default category for entries that don't specify one "
                             "(required for flat {untranslated: translation} maps).")
    parser.add_argument("--force", action="store_true",
                        help="Add even if a category is not in the book's known/template categories.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be added without writing to the database.")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        raw = json.load(f)

    try:
        entries = normalize_entries(raw, args.category)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: a failed add_entity raises instead of returning False mid-batch.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    used_cats = known_categories(db_manager, args.book_id)
    template_cats = book_template_categories(db_manager, args.book_id)
    valid_cats = sorted(set(used_cats) | set(template_cats))

    print(f"Book ID: {args.book_id}")
    print(f"Entries:  {len(entries)}")
    print(f"Mode:     {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 70)

    added = []
    skipped_existing = []
    bad_category = []
    failed = []
    seen_in_file = {}

    for i, entry in enumerate(entries):
        category = entry["category"]
        untranslated = entry["untranslated"]
        translation = entry["translation"]

        # Guard against duplicates within the file itself.
        if untranslated in seen_in_file:
            print(f"  ⏭️  DUP IN FILE: {untranslated!r} (first seen with "
                  f"category {seen_in_file[untranslated]!r}); skipping entry {i}")
            skipped_existing.append(untranslated)
            continue
        seen_in_file[untranslated] = category

        existing = find_existing(db_manager, args.book_id, untranslated)
        if existing:
            descs = ", ".join(f"id={eid} [{cat}] {trans!r}" for eid, cat, trans in existing)
            print(f"  ⏭️  EXISTS: {untranslated!r} ({descs}); skipping")
            skipped_existing.append(untranslated)
            continue

        if valid_cats and category not in valid_cats and not args.force:
            print(f"  ❌ BAD CATEGORY [{category}] for {untranslated!r} "
                  f"(known: {', '.join(valid_cats)}); use --force to override")
            bad_category.append(untranslated)
            continue

        action = "WOULD ADD" if args.dry_run else "ADD"
        extra = []
        if entry.get("gender"):
            extra.append(f"gender={entry['gender']}")
        if entry.get("origin_chapter"):
            extra.append(f"origin_ch={entry['origin_chapter']}")
        if entry.get("note"):
            extra.append(f"note={entry['note']!r}")
        extra_str = f"  ({', '.join(extra)})" if extra else ""
        print(f"  ✅ {action} [{category}] {untranslated!r} → {translation!r}{extra_str}")

        if not args.dry_run:
            ok = db_manager.add_entity(
                category=category,
                untranslated=untranslated,
                translation=translation,
                book_id=args.book_id,
                last_chapter=entry.get("last_chapter"),
                gender=entry.get("gender"),
                origin_chapter=entry.get("origin_chapter"),
                note=entry.get("note"),
            )
            if not ok:
                print(f"     ⚠️  FAILED to add {untranslated!r} (see logs)")
                failed.append(untranslated)
                continue

        added.append(untranslated)

    print("=" * 70)
    print(f"Added:            {len(added)}")
    print(f"Skipped existing: {len(skipped_existing)}")
    print(f"Bad category:     {len(bad_category)}")
    print(f"Failed:           {len(failed)}")
    if args.dry_run and added:
        print("(dry run — nothing was written)")


if __name__ == "__main__":
    main()
