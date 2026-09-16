#!/usr/bin/env python3
"""Retroactively convert entity `untranslated` keys from traditional to simplified Chinese.

Companion to bulk_convert_trad_to_simp.py (which converts stored chapter source).
After flipping a book/global trad_to_simp toggle and converting chapter text,
existing entity rows can still have traditional-character keys — which then fail
to match the now-simplified chapter text. This script rewrites the keys in place.

Usage:
    python3 bulk_convert_entities_trad_to_simp.py --book-id 19 --dry-run
    python3 bulk_convert_entities_trad_to_simp.py --book-id 19
    python3 bulk_convert_entities_trad_to_simp.py --all-books --dry-run

On collision (target simplified key already exists for the same book), the
source row is left alone and reported — caller should manually merge or delete
the duplicate before re-running.
"""

import argparse
import sys

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger
from trad_simp import convert_text


def _find_existing(entities, new_key):
    """Return (category, data) of the first entity whose untranslated == new_key, else None."""
    for cat, items in entities.items():
        if new_key in items:
            return cat, items[new_key]
    return None


def convert_book(db, book_id, dry_run, on_conflict):
    book = db.get_book(book_id=book_id)
    if not book:
        print(f"Book {book_id} not found.", file=sys.stderr)
        return None, []

    print(f"\n== Book: {book['title']} (id={book_id}) ==")

    db._load_entities(book_id=book_id)
    entities = db.entities

    plans = []
    for category, items in entities.items():
        for untranslated, data in items.items():
            entity_book_id = data.get("book_id")
            if entity_book_id != book_id:
                continue
            converted = convert_text(untranslated)
            if converted == untranslated:
                continue
            plans.append((category, untranslated, converted, data))

    if not plans:
        print("  No entity keys need conversion.")
        return {"renamed": 0, "conflict": 0, "merged": 0, "not_found": 0, "error": 0, "total": 0}, []

    stats = {"renamed": 0, "conflict": 0, "merged": 0, "not_found": 0, "error": 0, "total": len(plans)}
    conflict_details = []

    for category, old_key, new_key, old_data in plans:
        existing = _find_existing(entities, new_key)
        is_conflict = existing is not None

        if is_conflict:
            existing_cat, existing_data = existing
            detail = {
                "book_id": book_id,
                "category": category,
                "trad_key": old_key,
                "simp_key": new_key,
                "trad_translation": old_data.get("translation"),
                "trad_last": old_data.get("last_chapter"),
                "simp_category": existing_cat,
                "simp_translation": existing_data.get("translation"),
                "simp_last": existing_data.get("last_chapter"),
            }
            conflict_details.append(detail)

            print(f"  ⚠️  CONFLICT [{category}] '{old_key}' → '{new_key}'")
            print(f"      trad: translation='{old_data.get('translation')}'  last_chapter={old_data.get('last_chapter')}")
            print(f"      simp: translation='{existing_data.get('translation')}'  last_chapter={existing_data.get('last_chapter')}"
                  + (f"  (cat={existing_cat})" if existing_cat != category else ""))

            if dry_run:
                stats["conflict"] += 1
                continue

            if on_conflict == "skip":
                stats["conflict"] += 1
                continue
            elif on_conflict == "delete-trad":
                if db.delete_entity(category, old_key):
                    print(f"      → deleted traditional row, kept simplified")
                    stats["merged"] += 1
                else:
                    print(f"      → ❌ failed to delete traditional row")
                    stats["error"] += 1
                continue
            elif on_conflict == "delete-simp":
                if db.delete_entity(existing_cat, new_key):
                    result = db.rename_entity_untranslated(category, old_key, new_key, book_id=book_id)
                    if result == "renamed":
                        print(f"      → deleted simplified row, renamed traditional")
                        stats["merged"] += 1
                    else:
                        print(f"      → ❌ delete-simp succeeded but rename failed: {result}")
                        stats["error"] += 1
                else:
                    print(f"      → ❌ failed to delete simplified row")
                    stats["error"] += 1
                continue

        if dry_run:
            print(f"  [{category}] '{old_key}' → '{new_key}'")
            stats["renamed"] += 1
            continue

        result = db.rename_entity_untranslated(category, old_key, new_key, book_id=book_id)
        marker = {"renamed": "✅", "conflict": "⚠️ ", "not_found": "❓", "error": "❌", "unchanged": "•"}.get(result, "?")
        print(f"  {marker} [{category}] '{old_key}' → '{new_key}' [{result}]")
        stats[result] = stats.get(result, 0) + 1

    return stats, conflict_details


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book-id", type=int, help="Convert entities for a single book")
    group.add_argument("--all-books", action="store_true", help="Convert across every book")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing.")
    parser.add_argument("--on-conflict", choices=["skip", "delete-trad", "delete-simp"],
                        default="skip",
                        help="When the simplified key already exists: skip (default, leaves both rows), "
                             "delete-trad (drop the traditional row, keep the existing simplified one), "
                             "or delete-simp (drop the existing simplified row, then rename traditional).")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    if args.all_books:
        books = db.list_books()
        book_ids = [b["id"] for b in books]
    else:
        book_ids = [args.book_id]

    print(f"Mode: {'DRY RUN' if args.dry_run else 'WRITE'}")

    grand = {"renamed": 0, "conflict": 0, "merged": 0, "not_found": 0, "error": 0, "total": 0}
    all_conflicts = []
    for bid in book_ids:
        stats, conflicts = convert_book(db, bid, args.dry_run, args.on_conflict)
        if stats is None:
            continue
        for k, v in stats.items():
            grand[k] = grand.get(k, 0) + v
        all_conflicts.extend(conflicts)

    print()
    print("=" * 60)
    print(f"Total keys flagged:    {grand['total']}")
    print(f"  Renamed:             {grand['renamed']}")
    if grand["merged"]:
        print(f"  Conflicts merged:    {grand['merged']}")
    print(f"  Conflicts unresolved:{grand['conflict']}")
    if grand["not_found"]:
        print(f"  Not found:           {grand['not_found']}")
    if grand["error"]:
        print(f"  Errors:              {grand['error']}")

    if args.dry_run:
        print("(Dry run — no changes written. Re-run without --dry-run to apply.)")
        if all_conflicts:
            print(f"\n{len(all_conflicts)} conflict(s) need resolution. Re-run with one of:")
            print("  --on-conflict=delete-trad   (recommended after trad→simp chapter conversion:")
            print("                               drops trad rows, keeps the existing simp rows)")
            print("  --on-conflict=delete-simp   (drops simp rows, renames trad rows into them)")
            print("  --on-conflict=skip          (default: leave both rows untouched)")

    return 0 if grand["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
