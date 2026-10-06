#!/usr/bin/env python3
"""
Print the stored summaries of a book's chapters.

Usage:
    python get_chapter_summaries.py -b 106                     # whole book
    python get_chapter_summaries.py -b 106 --chapters 42
    python get_chapter_summaries.py -b "Book Title" --chapters 1-20
    python get_chapter_summaries.py -b 106 --chapters '>=400' --format json

Filter syntax for --chapters (same as grep_book.py / get_entities.py):
    N         exact chapter
    N-M       inclusive range
    >N, >=N   greater than (or equal)
    <N, <=N   less than (or equal)
    a,b,c     comma-separated list of any of the above

Summaries are the ones the translation model wrote when the chapter was
translated (`chapters.summary`). Queued, not-yet-translated chapters have none.
Read-only: this script never writes.
"""

import argparse
import json
import os
import sys
import warnings

# Silence any FutureWarnings emitted at provider import time.
warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger
from get_entities import resolve_book
from grep_book import parse_chapter_filter


def fetch_summaries(db_manager, book_id, chapter_filter=None):
    """[{chapter, title, summary}, ...] in chapter order, filtered by predicate."""
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT chapter_number, title, summary FROM chapters "
            "WHERE book_id = ? ORDER BY chapter_number",
            (book_id,),
        )
        rows = cursor.fetchall()
    return [
        {"chapter": r["chapter_number"], "title": r["title"], "summary": r["summary"]}
        for r in rows
        if chapter_filter is None or chapter_filter(r["chapter_number"])
    ]


def render_text(book, chapters):
    lines = [f"# {book.get('title')} (id={book['id']})"]
    for c in chapters:
        lines.append("")
        lines.append(f"== ch{c['chapter']}: {c['title'] or ''} ==".rstrip())
        lines.append(c["summary"] or "(no summary)")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Print the stored summaries of a book's chapters.",
    )
    parser.add_argument("--book", "-b", required=True,
                        help="Book ID (numeric) or exact title.")
    parser.add_argument("--chapters", "-c", default=None,
                        help="Restrict to chapters: N, N-M, >N, <=N, or a comma list.")
    parser.add_argument("--format", "-f", choices=("text", "json"), default="text",
                        help="Output format (default: text).")
    parser.add_argument("--output", "-o", default=None,
                        help="Output file path. Defaults to stdout.")
    args = parser.parse_args()

    try:
        chapter_filter = parse_chapter_filter(args.chapters)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    book = resolve_book(db_manager, args.book)
    if not book:
        print(f"error: book not found: {args.book!r}", file=sys.stderr)
        sys.exit(1)

    chapters = fetch_summaries(db_manager, book["id"], chapter_filter)
    if not chapters:
        print(f"error: no chapters matched in book {book['id']}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        rendered = json.dumps(
            {"book": {"id": book["id"], "title": book.get("title")},
             "filter": args.chapters, "chapters": chapters},
            ensure_ascii=False, indent=2) + "\n"
    else:
        rendered = render_text(book, chapters)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(rendered)
        print(f"Wrote {len(chapters)} summaries to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
