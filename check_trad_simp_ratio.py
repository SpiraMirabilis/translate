#!/usr/bin/env python3
"""
Measure how traditional vs. simplified a book's source text is, using OpenCC.

For every CJK character in the book's untranslated_content, converts it
through OpenCC's t2s (traditional -> simplified) table. A character that
changes under t2s is traditional; a character that is unchanged is either
already simplified or script-neutral (same glyph in both scripts). This is
the same t2s convention used by trad_simp.py for the book preprocessing step.

Usage:
    python check_trad_simp_ratio.py --book 1
    python check_trad_simp_ratio.py -b "Book Title"
    python check_trad_simp_ratio.py -b 1 --by-chapter
"""

import argparse
import json
import os
import re
import sys
import warnings
from collections import Counter

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger

try:
    from opencc import OpenCC
except ImportError:
    print("error: opencc not installed (pip install opencc-python-reimplemented "
          "or apt install the system package)", file=sys.stderr)
    sys.exit(1)

CJK_RE = re.compile(r"[一-鿿㐀-䶿]")


def resolve_book(db_manager, book_arg):
    if book_arg.isdigit():
        book = db_manager.get_book(book_id=int(book_arg))
        if book:
            return book
    return db_manager.get_book(title=book_arg)


def parse_content(raw):
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        return str(parsed).split("\n")
    except (json.JSONDecodeError, TypeError):
        return str(raw).split("\n")


def classify_chars(text, converter, cache):
    """Return (traditional_count, simplified_count) for the CJK chars in text."""
    trad = 0
    simp = 0
    for ch in CJK_RE.findall(text):
        is_trad = cache.get(ch)
        if is_trad is None:
            is_trad = converter.convert(ch) != ch
            cache[ch] = is_trad
        if is_trad:
            trad += 1
        else:
            simp += 1
    return trad, simp


def main():
    parser = argparse.ArgumentParser(
        description="Measure traditional vs. simplified character ratio in a book's source text.",
    )
    parser.add_argument("--book", "-b", required=True,
                        help="Book ID (numeric) or exact title.")
    parser.add_argument("--by-chapter", action="store_true",
                        help="Also print a per-chapter breakdown.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    book = resolve_book(db_manager, args.book)
    if not book:
        print(f"error: book not found: {args.book!r}", file=sys.stderr)
        sys.exit(1)

    conn = db_manager.backend.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT chapter_number, untranslated_content FROM chapters WHERE book_id = ? "
            "ORDER BY chapter_number",
            (book["id"],),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"No chapters found for book {book['id']} ({book.get('title')!r}).",
              file=sys.stderr)
        sys.exit(0)

    converter = OpenCC('t2s')
    char_cache = {}

    total_trad = 0
    total_simp = 0
    per_chapter = []

    for chapter_number, content in rows:
        lines = parse_content(content)
        text = "\n".join(str(line) for line in lines)
        trad, simp = classify_chars(text, converter, char_cache)
        total_trad += trad
        total_simp += simp
        per_chapter.append((chapter_number, trad, simp))

    total = total_trad + total_simp
    print(f"# {book.get('title')} (book_id={book['id']}) — "
          f"{len(rows)} chapter(s), {total:,} CJK characters")
    print()
    if total == 0:
        print("No CJK characters found.")
        return

    print(f"Traditional: {total_trad:,} ({total_trad / total * 100:.2f}%)")
    print(f"Simplified:  {total_simp:,} ({total_simp / total * 100:.2f}%)")

    if args.by_chapter:
        print()
        print("Per-chapter breakdown:")
        for chapter_number, trad, simp in per_chapter:
            ch_total = trad + simp
            if ch_total == 0:
                print(f"  Ch {chapter_number}: (no CJK text)")
                continue
            pct_trad = trad / ch_total * 100
            print(f"  Ch {chapter_number}: {pct_trad:5.1f}% traditional "
                  f"({trad:,}/{ch_total:,})")


if __name__ == "__main__":
    main()
