#!/usr/bin/env python3
"""
Change an entity's category in the database for a given book.

Use when an entity is filed under the wrong category (e.g. moving a talent
from `abilities` to `life simulator talents`). Does not touch translations
or chapter content.

Usage:
    python change_entity_category.py --book-id 16 \
        --new-category "life simulator talents" \
        --untranslated 天靈根 --untranslated 修仙四藝
"""

import argparse
import sys

from config import TranslationConfig
from db import DatabaseManager
from genres import extract_categories_from_prompt
from logger import Logger


def find_entities(db_manager, book_id, untranslated):
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, category, translation FROM entities WHERE book_id = ? AND untranslated = ?",
            (book_id, untranslated),
        )
        return [(r["id"], r["category"], r["translation"]) for r in cursor.fetchall()]


def known_categories(db_manager, book_id):
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT category FROM entities WHERE book_id = ?", (book_id,))
        used = [r["category"] for r in cursor.fetchall()]
        cursor.execute("SELECT prompt_template FROM books WHERE id = ?", (book_id,))
        row = cursor.fetchone()
    template = row["prompt_template"] if row else None
    template_cats = extract_categories_from_prompt(template) if template else []
    return sorted(set(c for c in used if c) | set(template_cats or []))


def update_category(db_manager, entity_id, new_category):
    """Update an entity's category via the repo method (single transaction)."""
    db_manager.update_entity_by_id(entity_id, category=new_category)


def main():
    parser = argparse.ArgumentParser(description="Change an entity's category in the database.")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--new-category", required=True)
    parser.add_argument("--untranslated", action="append", required=True,
                        help="Untranslated text (repeat for multiple entities).")
    parser.add_argument("--current-category",
                        help="If untranslated matches multiple categories, restrict to this one.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="Allow setting a category not in the book's template/in-use set.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: a failed category update raises loudly instead of silently no-op'ing.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    valid_cats = known_categories(db_manager, args.book_id)
    if args.new_category not in valid_cats and not args.force:
        print(f"Category {args.new_category!r} is not among known categories for book {args.book_id}:")
        for c in valid_cats:
            print(f"  {c}")
        print("Pass --force to use it anyway.")
        sys.exit(1)

    print(f"Book ID:      {args.book_id}")
    print(f"New category: {args.new_category!r}")
    print(f"Mode:         {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 70)

    n_updated = 0
    n_unchanged = 0
    n_missing = 0
    n_ambiguous = 0

    for u in args.untranslated:
        matches = find_entities(db_manager, args.book_id, u)
        if args.current_category:
            matches = [m for m in matches if m[1] == args.current_category]

        if not matches:
            print(f"  ❌ NOT FOUND: {u!r}")
            n_missing += 1
            continue
        if len(matches) > 1:
            cats = [m[1] for m in matches]
            print(f"  ⚠️  AMBIGUOUS ({len(matches)} categories: {cats}): {u!r} — pass --current-category")
            n_ambiguous += 1
            continue

        eid, old_cat, trans = matches[0]
        if old_cat == args.new_category:
            print(f"  ⏭️  UNCHANGED: {u!r} already in {args.new_category!r}")
            n_unchanged += 1
            continue

        action = "WOULD UPDATE" if args.dry_run else "UPDATE"
        print(f"  ✅ {action}: {u!r}  [{old_cat}] → [{args.new_category}]  (translation: {trans!r})")
        if not args.dry_run:
            update_category(db_manager, eid, args.new_category)
        n_updated += 1

    print("=" * 70)
    print(f"Updated:    {n_updated}")
    print(f"Unchanged:  {n_unchanged}")
    print(f"Not found:  {n_missing}")
    print(f"Ambiguous:  {n_ambiguous}")


if __name__ == "__main__":
    main()
