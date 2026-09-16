#!/usr/bin/env python3
"""
Delete an entity from the database for a given book.

Looks up each entity by (book_id, untranslated) and shows what would be
removed. By default this is a dry run — pass --apply to actually delete.
Multiple entities can be deleted at once by comma-separating the keys.

Usage:
    python delete_entity.py --book 5 陆青云                  # dry run (default)
    python delete_entity.py -b 5 陆青云 --apply               # actually delete
    python delete_entity.py -b 5 "陆青云,天剑宗,李白" --apply   # delete several
"""

import argparse
import sys

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger


def find_entity(db_manager: DatabaseManager, book_id: int, untranslated: str):
    """Return list of (id, category, translation) for entities matching (book_id, untranslated)."""
    conn = db_manager.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, category, translation FROM entities WHERE book_id = ? AND untranslated = ?",
        (book_id, untranslated),
    )
    rows = cursor.fetchall()
    conn.close()
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append((r["id"], r["category"], r["translation"]))
        else:
            out.append((r[0], r[1], r[2]))
    return out


def delete_entity(db_manager: DatabaseManager, entity_id: int):
    """Delete the entity row by id."""
    conn = db_manager.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Delete an entity from a book in the database.")
    parser.add_argument("--book", "-b", type=int, required=True, help="Book ID the entity belongs to.")
    parser.add_argument("untranslated",
                        help="Untranslated (Chinese) word/phrase to delete. "
                             "Comma-separate to delete several at once.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete the entity. Without this flag the script "
                             "is a dry run and writes nothing.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    keys = [k.strip() for k in args.untranslated.split(",") if k.strip()]
    if not keys:
        print("No untranslated keys given.")
        sys.exit(1)

    to_delete = []   # (entity_id, category, translation, untranslated)
    errors = 0

    for key in keys:
        matches = find_entity(db_manager, args.book, key)

        if not matches:
            print(f"❌ No entity found for book={args.book}, untranslated={key!r}.")
            errors += 1
            continue

        entity_id, category, translation = matches[0]
        print(f"Entity id={entity_id} category={category} "
              f"untranslated={key!r} translation={translation!r}")
        to_delete.append((entity_id, category, translation, key))

    if not args.apply:
        print(f"[dry-run] Would delete {len(to_delete)} entity(ies). Pass --apply to do it.")
        sys.exit(1 if errors else 0)

    for entity_id, _, _, key in to_delete:
        delete_entity(db_manager, entity_id)
        print(f"✅ Deleted {key!r} (id={entity_id}).")

    print(f"Done — {len(to_delete)} deleted, {errors} skipped.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
