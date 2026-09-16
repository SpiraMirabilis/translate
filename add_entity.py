#!/usr/bin/env python3
"""
Add a new entity to the database for a given book.

Refuses to overwrite an existing entity (use correct_entity_translation.py
for that). Category is required; if the book has any existing entities,
known categories are listed on error to help disambiguate.

Usage:
    python add_entity.py --book-id 5 --category characters \
        --untranslated "陆青云" --translation "Lu Qingyun"

    python add_entity.py --book-id 5 --category locations \
        --untranslated "天剑宗" --translation "Heavenly Sword Sect" \
        --origin-chapter 12 --note "Main protagonist's sect"
"""

import argparse
import os
import sys

from config import TranslationConfig
from db import DatabaseManager
from genres import extract_categories_from_prompt
from logger import Logger


def find_existing(db_manager: DatabaseManager, book_id: int, untranslated: str):
    """Return list of (id, category, translation) for entities matching (book_id, untranslated)."""
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, category, translation FROM entities WHERE book_id = ? AND untranslated = ?",
            (book_id, untranslated),
        )
        return [(r["id"], r["category"], r["translation"]) for r in cursor.fetchall()]


def known_categories(db_manager: DatabaseManager, book_id: int):
    """Return sorted list of categories already in use for this book."""
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT category FROM entities WHERE book_id = ?",
            (book_id,),
        )
        cats = [r["category"] for r in cursor.fetchall()]
    return sorted(c for c in cats if c)


def book_template_categories(db_manager: DatabaseManager, book_id: int):
    """Return categories defined by the book's prompt template, or [] if unavailable."""
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT prompt_template FROM books WHERE id = ?", (book_id,))
        row = cursor.fetchone()
    if not row:
        return []
    template = row["prompt_template"]
    if not template:
        return []
    cats = extract_categories_from_prompt(template)
    return cats or []


def main():
    parser = argparse.ArgumentParser(description="Add a new entity to a book in the database.")
    parser.add_argument("--book-id", type=int, required=True, help="Book ID to attach the entity to.")
    parser.add_argument("--category", required=True, help="Entity category (e.g. characters, locations).")
    parser.add_argument("--untranslated", required=True, help="Untranslated (source-language) text.")
    parser.add_argument("--translation", required=True, help="Translated text.")
    parser.add_argument("--gender", help="Optional gender (typically for character entities).")
    parser.add_argument("--origin-chapter", type=int, help="Chapter number where this entity first appears.")
    parser.add_argument("--last-chapter", type=int, help="Most recent chapter where this entity appears.")
    parser.add_argument("--note", help="Optional freeform note.")
    parser.add_argument("--list-categories", action="store_true",
                        help="List categories known for this book and exit.")
    parser.add_argument("--force", action="store_true",
                        help="Add even if --category is not in the book's known/template categories.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: a failed add_entity raises loudly instead of returning False.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    used_cats = known_categories(db_manager, args.book_id)
    template_cats = book_template_categories(db_manager, args.book_id)
    valid_cats = sorted(set(used_cats) | set(template_cats))

    if args.list_categories:
        if valid_cats:
            print(f"Categories for book {args.book_id}:")
            for c in valid_cats:
                origin = []
                if c in template_cats:
                    origin.append("template")
                if c in used_cats:
                    origin.append("in-use")
                print(f"  {c}  ({', '.join(origin)})")
        else:
            print(f"No categories found for book {args.book_id} (no entities, no prompt template).")
        return

    existing = find_existing(db_manager, args.book_id, args.untranslated)
    if existing:
        print(f"Entity {args.untranslated!r} already exists for book {args.book_id}:")
        for eid, cat, trans in existing:
            print(f"  id={eid}  category={cat}  translation={trans!r}")
        print("Use correct_entity_translation.py to update an existing entity.")
        sys.exit(1)

    if valid_cats and args.category not in valid_cats and not args.force:
        print(f"Category {args.category!r} is not among known categories for book {args.book_id}:")
        for c in valid_cats:
            print(f"  {c}")
        print("Pass --force to add it anyway, or --list-categories to see details.")
        sys.exit(1)

    ok = db_manager.add_entity(
        category=args.category,
        untranslated=args.untranslated,
        translation=args.translation,
        book_id=args.book_id,
        last_chapter=args.last_chapter,
        gender=args.gender,
        origin_chapter=args.origin_chapter,
        note=args.note,
    )

    if not ok:
        print("Failed to add entity (see logs).")
        sys.exit(1)

    # Re-read to confirm and show the assigned id.
    added = find_existing(db_manager, args.book_id, args.untranslated)
    if added:
        eid, cat, trans = added[0]
        print(f"Added entity id={eid} category={cat} untranslated={args.untranslated!r} translation={trans!r}")
    else:
        print("Entity added.")


if __name__ == "__main__":
    main()
