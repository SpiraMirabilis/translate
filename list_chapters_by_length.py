#!/usr/bin/env python3
"""
List chapters of a book ranked by total character count.

Usage:
    python list_chapters_by_length.py --book 1
    python list_chapters_by_length.py -b "Book Title" -n 10
    python list_chapters_by_length.py -b 1 -n 5 --ascending
    python list_chapters_by_length.py -b 1 --source        # rank by source-text length
"""

import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger


def resolve_book(db_manager, book_arg):
    if book_arg.isdigit():
        book = db_manager.get_book(book_id=int(book_arg))
        if book:
            return book
    return db_manager.get_book(title=book_arg)


def parse_content(raw):
    """Decode chapter content (JSON list or newline-separated string) into a list of lines."""
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        return str(parsed).split("\n")
    except (json.JSONDecodeError, TypeError):
        return str(raw).split("\n")


def first_nonempty(lines):
    for line in lines:
        if line and str(line).strip():
            return str(line).strip()
    return ""


def total_chars(lines):
    return sum(len(str(line)) for line in lines)


def main():
    parser = argparse.ArgumentParser(
        description="List chapters of a book ranked by total character count.",
    )
    parser.add_argument("--book", "-b", required=True,
                        help="Book ID (numeric) or exact title.")
    parser.add_argument("-n", type=int, default=None,
                        help="Number of chapters to display (default: all).")
    parser.add_argument("--ascending", "-a", action="store_true",
                        help="Sort smallest-to-largest (default: largest-to-smallest).")
    parser.add_argument("--source", action="store_true",
                        help="Rank by untranslated source text length instead of translated text.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    book = resolve_book(db_manager, args.book)
    if not book:
        print(f"error: book not found: {args.book!r}", file=sys.stderr)
        sys.exit(1)

    column = "untranslated_content" if args.source else "translated_content"
    conn = db_manager.backend.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT chapter_number, title, {column} FROM chapters WHERE book_id = ?",
            (book["id"],),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"No chapters found for book {book['id']} ({book.get('title')!r}).",
              file=sys.stderr)
        sys.exit(0)

    entries = []
    for chapter_number, title, content in rows:
        lines = parse_content(content)
        entries.append({
            "chapter": chapter_number,
            "title": title or "",
            "first_line": first_nonempty(lines),
            "chars": total_chars(lines),
        })

    entries.sort(key=lambda e: e["chars"], reverse=not args.ascending)
    if args.n is not None:
        entries = entries[: args.n]

    direction = "ascending" if args.ascending else "descending"
    source_label = "source" if args.source else "translated"
    print(f"# {book.get('title')} (book_id={book['id']}) — "
          f"{len(entries)} chapter(s), {direction} by {source_label} character count")
    print()
    for e in entries:
        header = f"Ch {e['chapter']}"
        if e["title"]:
            header += f": {e['title']}"
        header += f"  [{e['chars']:,} chars]"
        print(header)
        print(f"    {e['first_line']}" if e["first_line"] else "    (empty)")
        print()


if __name__ == "__main__":
    main()
