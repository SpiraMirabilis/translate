#!/usr/bin/env python3
"""
Re-derive chapter numbers for a book's queued chapters from their titles.

Reuses the EPUB chapter-number parser (`chapter_number_from_title` in
epub_processor.py), which understands both Arabic ('Chapter 12', '第12章')
and Chinese-numeral ('第三百三十七章') forms. For each queued item it parses
the number out of the stored title and rewrites queue.chapter_number to match.

Usage:
    python3 renumber_queued_chapters.py --book-id 27 --dry-run
    python3 renumber_queued_chapters.py --book-id 27
"""

import argparse
import sys

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger

from epub_processor import chapter_number_from_title


def main():
    parser = argparse.ArgumentParser(
        description="Renumber a book's queued chapters by parsing the title."
    )
    parser.add_argument("--book-id", type=int, required=True,
                        help="Book ID whose queued chapters should be renumbered.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to the database.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    book = db_manager.get_book(book_id=args.book_id)
    if not book:
        print(f"Book with ID {args.book_id} not found.")
        sys.exit(1)

    items = db_manager.list_queue(book_id=args.book_id)
    if not items:
        print(f"No queued chapters for book {args.book_id} ('{book['title']}').")
        return

    print(f"Book {args.book_id}: {book['title']}")
    print(f"{len(items)} queued chapter(s).\n")

    changes = []      # (item, new_number)
    unparsed = []     # items whose title yielded no number

    for item in items:
        title = item.get("title") or ""
        parsed = chapter_number_from_title(title, default=None)
        if parsed is None:
            unparsed.append(item)
            continue
        if parsed != item.get("chapter_number"):
            changes.append((item, parsed))

    # Preview table
    if changes:
        print("Chapters to renumber (old -> new | title):")
        for item, new_number in changes:
            old = item.get("chapter_number")
            print(f"  pos {item['position']:>4}  "
                  f"{str(old):>6} -> {new_number:<6}  {item['title']}")
    else:
        print("No chapter numbers need changing.")

    if unparsed:
        print("\nCould not parse a number from these titles (left unchanged):")
        for item in unparsed:
            print(f"  pos {item['position']:>4}  "
                  f"chapter_number={item.get('chapter_number')}  {item['title']}")

    # Warn about duplicate target numbers after the proposed renumber
    final_numbers = {}
    for item in items:
        num = item.get("chapter_number")
        final_numbers[item["id"]] = num
    for item, new_number in changes:
        final_numbers[item["id"]] = new_number
    seen = {}
    for item_id, num in final_numbers.items():
        if num is not None:
            seen.setdefault(num, []).append(item_id)
    dups = {num: ids for num, ids in seen.items() if len(ids) > 1}
    if dups:
        print("\nWARNING: the result would have duplicate chapter numbers:")
        for num in sorted(dups):
            print(f"  chapter {num}: queue ids {dups[num]}")

    if not changes:
        return

    if args.dry_run:
        print(f"\n[dry-run] Would update {len(changes)} chapter(s). No changes written.")
        return

    updated = 0
    for item, new_number in changes:
        if db_manager.update_queue_chapter_number(item["id"], new_number):
            updated += 1
        else:
            print(f"  FAILED to update queue id {item['id']} "
                  f"(title: {item['title']})")

    print(f"\nUpdated {updated} of {len(changes)} chapter(s).")


if __name__ == "__main__":
    main()
