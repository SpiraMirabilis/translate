#!/usr/bin/env python3
"""Print the BOOK-SPECIFIC NOTES section of a book's system prompt.

That trailing section of a book's per-book prompt template holds the
established conventions (genre, setting, protagonist, agreed renderings).
This is a thin CLI wrapper around footnote_scan_core.book_notes().

Usage:
    python3 get_book_notes.py <book_id | title>
"""

import argparse
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger
from footnote_scan_core import book_notes


def resolve_book(db_manager, book_arg):
    """Resolve a book argument (numeric id or title) to a book dict."""
    if book_arg.isdigit():
        book = db_manager.get_book(book_id=int(book_arg))
        if book:
            return book
    return db_manager.get_book(title=book_arg)


def main():
    parser = argparse.ArgumentParser(
        description="Print a book's BOOK-SPECIFIC NOTES prompt section."
    )
    parser.add_argument("book", help="Book id (numeric) or exact title")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    book = resolve_book(db_manager, args.book)
    if not book:
        print(f"error: book not found: {args.book!r}", file=sys.stderr)
        sys.exit(1)

    notes = book_notes(db_manager, book["id"])
    if not notes:
        print(
            f"error: no book-specific notes for {book.get('title')!r} "
            f"(id={book['id']})",
            file=sys.stderr,
        )
        sys.exit(1)

    print(notes)


if __name__ == "__main__":
    main()
