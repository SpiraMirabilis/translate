#!/usr/bin/env python3
"""
Print the source content of a queued chapter.

Usage:
    python3 show_queue_item.py --queue-id 8219
    python3 show_queue_item.py --book 30 --chapter 101
    python3 show_queue_item.py --book 30                  # only if one item is queued
    python3 show_queue_item.py --queue-id 8219 --plain > ch85.txt
    python3 show_queue_item.py --queue-id 8219 --json

Queue rows hold the *untranslated* source (chapters that have been translated
live in the chapters table instead). Item lookup matches import_translation.py:
--queue-id wins, then --book + --chapter, then the book's sole queued item.
"""

import argparse
import json
import sys

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger
# Same lookup rules as the importer — kept in one place so they can't drift.
from import_translation import find_queue_item, resolve_book


def main():
    parser = argparse.ArgumentParser(
        description="Print the source content of a queued chapter.")
    parser.add_argument('--book', '-b', help="Book ID or exact title")
    parser.add_argument('--chapter', '-c', type=int, help="Queued chapter number")
    parser.add_argument('--queue-id', type=int, help="Target a specific queue row by id")
    parser.add_argument('--plain', action='store_true',
                        help="Print only the content lines (no header)")
    parser.add_argument('--json', action='store_true',
                        help="Print the whole queue row as JSON")
    args = parser.parse_args()

    if args.book is None and args.queue_id is None:
        raise SystemExit("Error: pass --book (and --chapter), or --queue-id.")

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    book = resolve_book(db, args.book)
    item = find_queue_item(db, book['id'] if book else None, args.chapter, args.queue_id)

    lines = item.get('content')
    if isinstance(lines, str):
        lines = lines.splitlines()
    lines = lines or []

    if args.json:
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0

    if not args.plain:
        chars = sum(len(l) for l in lines)
        print(f"Queue id:  {item['id']}  (position {item['position']}, status {item.get('status')})")
        print(f"Book:      {item.get('book_title')} (id {item['book_id']})")
        print(f"Chapter:   {item.get('chapter_number')}")
        print(f"Title:     {item.get('title') or '—'}")
        print(f"Source:    {item.get('source') or '—'}")
        print(f"Queued:    {item.get('created_date')}")
        if item.get('retranslation_reason'):
            print(f"Retranslate reason: {item['retranslation_reason']}")
        print(f"Content:   {len(lines)} lines, {chars} characters")
        print("-" * 60)

    for line in lines:
        print(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
