#!/usr/bin/env python3
"""
Delete an entity from the database for a given book.

Looks up each entity by (book_id, untranslated) and shows what would be
removed. By default this is a dry run — pass --apply to actually delete.
Multiple entities can be deleted at once by comma-separating the keys.

A key that exists in more than one category is refused unless --category
narrows it to exactly one — the script used to delete whichever row the
database happened to return first.

Usage:
    python delete_entity.py --book 5 陆青云                  # dry run (default)
    python delete_entity.py -b 5 陆青云 --apply               # actually delete
    python delete_entity.py -b 5 "陆青云,天剑宗,李白" --apply   # delete several
    python delete_entity.py -b 5 李白 --category characters --apply
"""

import argparse
import sys

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger


def find_entity(db_manager: DatabaseManager, book_id: int, untranslated: str):
    """Return list of (id, category, translation) for entities matching (book_id, untranslated)."""
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, category, translation FROM entities WHERE book_id = ? AND untranslated = ?",
            (book_id, untranslated),
        )
        return [(r["id"], r["category"], r["translation"]) for r in cursor.fetchall()]


def plan_entity_deletes(db_manager: DatabaseManager, book_id: int, keys, category: str = None):
    """Resolve `keys` to the entity rows a delete would remove. Writes nothing.

    Returns ``(to_delete, errors)``: `to_delete` is a list of
    ``{id, category, untranslated, translation}`` dicts, one per key that
    resolved to exactly one entity; `errors` is a list of human-readable
    strings for keys that were not found or matched several categories.
    `category`, when given, restricts every key to that category.
    """
    to_delete = []
    errors = []
    for key in keys:
        matches = find_entity(db_manager, book_id, key)
        if category:
            matches = [m for m in matches if m[1] == category]

        if not matches:
            scope = f" in category {category!r}" if category else ""
            errors.append(f"No entity found for book={book_id}, untranslated={key!r}{scope}.")
            continue
        if len(matches) > 1:
            cats = [m[1] for m in matches]
            errors.append(f"Ambiguous: {key!r} exists in {len(matches)} categories {cats} "
                          f"in book {book_id} — pass a category to pick one.")
            continue

        entity_id, cat, translation = matches[0]
        to_delete.append({"id": entity_id, "category": cat,
                          "untranslated": key, "translation": translation})
    return to_delete, errors


def apply_entity_deletes(db_manager: DatabaseManager, to_delete) -> int:
    """Delete each planned entity by id; return how many rows were deleted."""
    return sum(1 for e in to_delete if db_manager.delete_entity_by_id(e["id"]))


def main():
    parser = argparse.ArgumentParser(description="Delete an entity from a book in the database.")
    parser.add_argument("--book", "-b", type=int, required=True, help="Book ID the entity belongs to.")
    parser.add_argument("untranslated",
                        help="Untranslated (Chinese) word/phrase to delete. "
                             "Comma-separate to delete several at once.")
    parser.add_argument("--category",
                        help="Only delete entities in this category — needed when a key "
                             "exists in more than one category.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete the entity. Without this flag the script "
                             "is a dry run and writes nothing.")
    args = parser.parse_args()

    keys = [k.strip() for k in args.untranslated.split(",") if k.strip()]
    if not keys:
        print("No untranslated keys given.")
        sys.exit(1)

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: a failed delete raises loudly instead of returning None.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    to_delete, errors = plan_entity_deletes(db_manager, args.book, keys, args.category)

    for msg in errors:
        print(f"❌ {msg}")
    for e in to_delete:
        print(f"Entity id={e['id']} category={e['category']} "
              f"untranslated={e['untranslated']!r} translation={e['translation']!r}")

    if not args.apply:
        print(f"[dry-run] Would delete {len(to_delete)} entity(ies). Pass --apply to do it.")
        sys.exit(1 if errors else 0)

    deleted = 0
    for e in to_delete:
        if apply_entity_deletes(db_manager, [e]):
            deleted += 1
            print(f"✅ Deleted {e['untranslated']!r} (id={e['id']}).")
        else:
            print(f"❌ {e['untranslated']!r} (id={e['id']}) was already gone.")

    print(f"Done — {deleted} deleted, {len(errors) + len(to_delete) - deleted} skipped.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
