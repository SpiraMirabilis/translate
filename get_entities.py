#!/usr/bin/env python3
"""
Dump entities for a book, optionally filtered by origin_chapter.

Usage:
    python get_entities.py --book 1
    python get_entities.py --book "Book Title" --origin-chapter 1-20
    python get_entities.py --book 1 --origin-chapter ">15" --output entities.json
    python get_entities.py --book 1 --origin-chapter "<100"
    python get_entities.py --book 1 --origin-chapter 42

Filter syntax for --origin-chapter:
    N         exact chapter
    N-M       inclusive range
    >N, >=N   greater than (or equal)
    <N, <=N   less than (or equal)

------------------------------------------------------------------------
MIGRATION TEMPLATE — this script (and correct_entity_translation.py) are
the reference pattern for porting the other root utility scripts onto the
post-2026-07 architecture:

  1. Import DatabaseManager from the `db` package (the root `database`
     module is a compatibility shim re-exporting the same names — both
     work, `db` is canonical).
  2. Read-only custom SQL goes inside `with db_manager._conn() as conn:`
     — connections are committed/rolled back/closed by the scope, never
     leaked on an exception. Pass `dict_rows=True` to get dict-like rows
     on BOTH backends (kills the isinstance(row, dict) SQLite-vs-MySQL
     dance old scripts carry).
  3. Prefer an existing repo method (db/*_repo.py) over hand-rolled SQL
     when one exists — e.g. update_entity_by_id, get_chapters_bulk.
  4. Pure text transforms live in chapter_text_ops.py — never re-implement
     the case-preserving substitution logic.
  5. Scripts that WRITE should construct
     DatabaseManager(config, logger, strict_writes=True) so failures raise
     instead of returning None (read-only scripts don't need it).
------------------------------------------------------------------------
"""

import argparse
import json
import os
import re
import sys
import warnings

# Silence any FutureWarnings emitted at provider import time.
warnings.filterwarnings("ignore", category=FutureWarning)
# Force quiet logger regardless of DEBUG env var — this is a one-shot CLI.
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger


def parse_chapter_filter(expr):
    """Parse an origin_chapter filter expression into (sql_fragment, params).

    Returns (None, []) if expr is falsy.
    Raises ValueError on malformed input.
    """
    if not expr:
        return None, []

    s = expr.strip()

    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return "origin_chapter BETWEEN ? AND ?", [lo, hi]

    m = re.fullmatch(r"(>=|<=|>|<|=)\s*(\d+)", s)
    if m:
        op, n = m.group(1), int(m.group(2))
        if op == "=":
            op = "="
        return f"origin_chapter {op} ?", [n]

    m = re.fullmatch(r"\d+", s)
    if m:
        return "origin_chapter = ?", [int(s)]

    raise ValueError(f"Unrecognized chapter filter: {expr!r}")


def resolve_book(db_manager, book_arg):
    """Resolve a book argument (numeric id or title) to a book dict."""
    if book_arg.isdigit():
        book = db_manager.get_book(book_id=int(book_arg))
        if book:
            return book
    return db_manager.get_book(title=book_arg)


def fetch_entities(db_manager, book_id, chapter_clause, chapter_params):
    """Query entities for the given book with optional origin_chapter filter."""
    query = """
        SELECT category, untranslated, translation, last_chapter,
               incorrect_translation, gender, book_id, origin_chapter, note
        FROM entities
        WHERE (book_id = ? OR book_id IS NULL)
    """
    params = [book_id]

    if chapter_clause:
        query += f" AND {chapter_clause}"
        params.extend(chapter_params)

    query += " ORDER BY category, COALESCE(origin_chapter, 0), untranslated"

    # dict_rows=True: dict-like rows on both SQLite and MySQL; the scope
    # commits/rolls back/closes the connection.
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]

    grouped = {}
    for row in rows:
        entry = {
            "untranslated": row["untranslated"],
            "translation": row["translation"],
            "origin_chapter": row["origin_chapter"],
            "last_chapter": row["last_chapter"],
        }
        if row["incorrect_translation"]:
            entry["incorrect_translation"] = row["incorrect_translation"]
        if row["gender"]:
            entry["gender"] = row["gender"]
        if row["note"]:
            entry["note"] = row["note"]
        if row["book_id"] is None:
            entry["global"] = True

        grouped.setdefault(row["category"], []).append(entry)

    return grouped


def main():
    parser = argparse.ArgumentParser(
        description="Dump entities for a book, optionally filtered by origin_chapter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Filter examples:\n"
            "  --origin-chapter 1-20    chapters 1 through 20\n"
            "  --origin-chapter '>15'   chapters after 15\n"
            "  --origin-chapter '<=99'  chapters up to and including 99\n"
            "  --origin-chapter 42      exactly chapter 42\n"
        ),
    )
    parser.add_argument(
        "--book", "-b", required=True,
        help="Book ID (numeric) or exact title."
    )
    parser.add_argument(
        "--origin-chapter", "-c", default=None,
        help="Origin chapter filter (e.g. '1-20', '>15', '<100', '42')."
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output file path. Defaults to stdout."
    )
    parser.add_argument(
        "--format", "-f", choices=("json", "text"), default="json",
        help="Output format (default: json)."
    )

    args = parser.parse_args()

    try:
        chapter_clause, chapter_params = parse_chapter_filter(args.origin_chapter)
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

    grouped = fetch_entities(db_manager, book["id"], chapter_clause, chapter_params)

    if args.format == "json":
        payload = {
            "book": {"id": book["id"], "title": book.get("title")},
            "filter": args.origin_chapter,
            "entities": grouped,
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        lines = [f"# {book.get('title')} (id={book['id']})"]
        if args.origin_chapter:
            lines.append(f"# origin_chapter filter: {args.origin_chapter}")
        for category in sorted(grouped):
            entries = grouped[category]
            lines.append("")
            lines.append(f"== {category} ({len(entries)}) ==")
            for e in entries:
                origin = e.get("origin_chapter")
                origin_str = f"ch{origin}" if origin is not None else "ch?"
                extra = []
                if e.get("gender"):
                    extra.append(e["gender"])
                if e.get("global"):
                    extra.append("global")
                if e.get("note"):
                    extra.append(f"note={e['note']}")
                suffix = f"  [{', '.join(extra)}]" if extra else ""
                lines.append(f"  {origin_str:>6}  {e['untranslated']} -> {e['translation']}{suffix}")
        rendered = "\n".join(lines) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(rendered)
        total = sum(len(v) for v in grouped.values())
        print(f"Wrote {total} entities to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
        if not rendered.endswith("\n"):
            sys.stdout.write("\n")


if __name__ == "__main__":
    main()
