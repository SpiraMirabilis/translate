#!/usr/bin/env python3
"""
Renumber a book's queued chapters when several *volumes* were uploaded back to
back, each with its own internal numbering that restarts at 1.

The uploader stores each volume's chapters with the volume-local chapter number
(Глава 1, Глава 2, ... then the next volume starts again at Глава 1). This makes
the whole-book numbering collide. This script walks the queue in position order,
splits it into volume blocks wherever the chapter number resets (current <=
previous), and renumbers every item sequentially across the whole book.

The running count is *seeded* from the highest already-translated chapter number
(rows in the chapters table), so a partially-translated first volume keeps its
existing numbers and the queued tail continues from there. That makes the script
safe to run while background translation is draining the queue: blocks are sized
from their original lengths, so consumed-mid-run items still reserve their slot.

Usage:
    python3 offset_queued_volumes.py --book-id 55 --dry-run
    python3 offset_queued_volumes.py --book-id 55
"""

import argparse
import sys

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger


def main():
    parser = argparse.ArgumentParser(
        description="Renumber multi-volume queued chapters into one continuous sequence."
    )
    parser.add_argument("--book-id", type=int, required=True,
                        help="Book ID whose queued chapters should be renumbered.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to the database.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    book = db.get_book(book_id=args.book_id)
    if not book:
        print(f"Book with ID {args.book_id} not found.")
        sys.exit(1)

    items = db.list_queue(book_id=args.book_id)
    if not items:
        print(f"No queued chapters for book {args.book_id} ('{book['title']}').")
        return

    # Seed from the highest already-translated chapter so an in-progress first
    # volume keeps its numbering and the queue continues after it.
    translated = db.list_chapters(book_id=args.book_id)
    seed = max((c["chapter"] for c in translated), default=0)

    print(f"Book {args.book_id}: {book['title']}")
    print(f"{len(translated)} translated chapter(s) (highest = {seed}); "
          f"{len(items)} queued item(s).\n")

    # Split into volume blocks at every reset point.
    blocks = []
    prev = None
    for it in items:
        cn = it.get("chapter_number")
        if prev is None or (cn is not None and cn <= prev):
            blocks.append([])
        blocks[-1].append(it)
        prev = cn

    print(f"Detected {len(blocks)} volume block(s):")
    cumulative = seed
    changes = []  # (item, new_number)
    for i, block in enumerate(blocks, 1):
        old_lo = block[0].get("chapter_number")
        old_hi = block[-1].get("chapter_number")
        new_lo = cumulative + 1
        new_hi = cumulative + len(block)
        flag = "" if (new_lo, new_hi) != (old_lo, old_hi) else "  (unchanged)"
        print(f"  block {i}: pos {block[0]['position']}-{block[-1]['position']}  "
              f"ch {old_lo}..{old_hi} -> {new_lo}..{new_hi}  ({len(block)} items){flag}")
        for offset, it in enumerate(block, start=1):
            new_number = cumulative + offset
            if new_number != it.get("chapter_number"):
                changes.append((it, new_number))
        cumulative = new_hi

    if not changes:
        print("\nNothing to renumber.")
        return

    print(f"\n{len(changes)} item(s) need renumbering "
          f"(final range {seed + 1}..{cumulative}).")

    if args.dry_run:
        print("[dry-run] No changes written.")
        return

    updated = 0
    for it, new_number in changes:
        if db.update_queue_chapter_number(it["id"], new_number):
            updated += 1
        else:
            print(f"  FAILED to update queue id {it['id']} (title: {it.get('title')})")
    print(f"\nUpdated {updated} of {len(changes)} item(s).")


if __name__ == "__main__":
    main()
