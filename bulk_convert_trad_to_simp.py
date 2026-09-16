#!/usr/bin/env python3
"""Retroactively convert traditional Chinese characters to simplified in stored source text.

This is a one-shot admin tool. The runtime trad_to_simp toggle (global flag or
books.trad_to_simp override) only affects chapters saved *after* the toggle is on;
this script rewrites existing chapters' untranslated_content in place, and does the
same for the book's pending queue items (which were serialized at add_to_queue time,
before the toggle was flipped).

Usage:
    python3 bulk_convert_trad_to_simp.py --book-id 15 --dry-run
    python3 bulk_convert_trad_to_simp.py --book-id 15
    python3 bulk_convert_trad_to_simp.py --book-id 15 --queue-only
    python3 bulk_convert_trad_to_simp.py --book-id 15 --chapters-only
"""

import argparse
import json
import sys

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger
from trad_simp import convert_text


def count_diff_chars(original, converted):
    """Rough count of characters that changed between two line lists / strings."""
    if isinstance(original, str):
        original, converted = [original], [converted]
    diff = 0
    for o_line, c_line in zip(original, converted):
        if not isinstance(o_line, str) or not isinstance(c_line, str):
            continue
        for a, b in zip(o_line, c_line):
            if a != b:
                diff += 1
        diff += abs(len(o_line) - len(c_line))
    return diff


def convert_queue_content(content):
    """Return (new_content, diff_chars) preserving the stored shape.

    Queue content is a JSON-serialized list of lines (how add_to_queue stores
    it), but older/hand-written rows can be a raw list or a plain string. We
    always hand back the same shape so the queue row stays valid.
    """
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, list):
            converted = convert_text(decoded)
            if converted == decoded:
                return content, 0
            return (json.dumps(converted, ensure_ascii=False),
                    count_diff_chars(decoded, converted))

        converted = convert_text(content)
        return converted, count_diff_chars(content, converted)

    if isinstance(content, list):
        converted = convert_text(content)
        return converted, count_diff_chars(content, converted)

    return content, 0


def convert_chapters(db, book_id, dry_run):
    """Rewrite untranslated_content for every stored chapter of the book."""
    chapters = db.list_chapters(book_id)
    if not chapters:
        print("Chapters: none found.")
        return

    changed_items = 0
    changed_chars = 0

    for meta in chapters:
        ch_num = meta["chapter"]
        full = db.get_chapter(book_id=book_id, chapter_number=ch_num)
        if not full:
            continue
        original = full["untranslated"]
        converted = convert_text(original)
        if converted == original:
            continue

        diff_chars = count_diff_chars(original, converted)
        changed_items += 1
        changed_chars += diff_chars
        print(f"  Chapter {ch_num}: {diff_chars} characters converted")

        if not dry_run:
            db.save_chapter(
                book_id,
                ch_num,
                full["title"],
                converted,
                full["content"],
                summary=full.get("summary"),
                translation_model=full.get("model"),
            )

    print(f"Chapters with changes: {changed_items} / {len(chapters)}")
    print(f"Characters converted in chapters: {changed_chars}")


def convert_queue(db, book_id, dry_run):
    """Rewrite content (and source title) for the book's pending queue items.

    Rows claimed by a worker (status='processing') are skipped — that chapter is
    already in flight and rewriting its source underneath the run would race.
    """
    with db._conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, chapter_number, title, content, status FROM queue "
            "WHERE book_id = ? ORDER BY position ASC",
            (book_id,),
        )
        rows = cursor.fetchall()

        if not rows:
            print("Queue: no items found.")
            return

        changed_items = 0
        changed_chars = 0
        skipped = 0

        for queue_id, ch_num, title, content, status in rows:
            if status == "processing":
                skipped += 1
                continue

            new_content, diff_chars = convert_queue_content(content)
            new_title = convert_text(title) if isinstance(title, str) else title
            title_diff = count_diff_chars(title, new_title) if isinstance(title, str) else 0
            if diff_chars == 0 and title_diff == 0:
                continue

            changed_items += 1
            changed_chars += diff_chars + title_diff
            ch_label = f"ch {ch_num}" if ch_num is not None else "no chapter#"
            title_note = f", title {title!r} -> {new_title!r}" if title_diff else ""
            print(f"  Queue {queue_id} ({ch_label}): "
                  f"{diff_chars} characters converted{title_note}")

            if not dry_run:
                cursor.execute(
                    "UPDATE queue SET content = ?, title = ? WHERE id = ?",
                    (new_content, new_title, queue_id),
                )

    print(f"Queue items with changes: {changed_items} / {len(rows)}"
          f"{f' ({skipped} in-flight item(s) skipped)' if skipped else ''}")
    print(f"Characters converted in queue: {changed_chars}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to the database.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--chapters-only", action="store_true",
                       help="Only convert stored chapters, leave the queue alone.")
    scope.add_argument("--queue-only", action="store_true",
                       help="Only convert pending queue items, leave chapters alone.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    book = db.get_book(book_id=args.book_id)
    if not book:
        print(f"Book {args.book_id} not found.", file=sys.stderr)
        return 1

    scope_label = ("chapters only" if args.chapters_only
                   else "queue only" if args.queue_only
                   else "chapters + queue")
    print(f"Book: {book['title']} (id={args.book_id})")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'WRITE'}  ({scope_label})")
    print()

    if not args.queue_only:
        convert_chapters(db, args.book_id, args.dry_run)
        print()

    if not args.chapters_only:
        convert_queue(db, args.book_id, args.dry_run)
        print()

    if args.dry_run:
        print("(Dry run — no changes written. Re-run without --dry-run to apply.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
