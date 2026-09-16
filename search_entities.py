#!/usr/bin/env python3
"""
Search entities in a book by untranslated or translated value.

Usage:
    python search_entities.py --book 21 "Zhang*"
    python search_entities.py -b 21 --category character "*Yu"
    python search_entities.py -b 21 --field translated --regex "^Sword.*Master$"
    python search_entities.py -b 21 --origin-chapter 1-50 --field both "phoenix"

Filter syntax for --origin-chapter:
    N         exact chapter
    N-M       inclusive range
    >N, >=N   greater than (or equal)
    <N, <=N   less than (or equal)
"""

import argparse
import fnmatch
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger

from get_entities import parse_chapter_filter, resolve_book


def fetch_entities(db_manager, book_id, category, chapter_clause, chapter_params):
    """Query entities for the given book with optional category + origin_chapter filters."""
    query = """
        SELECT category, untranslated, translation, origin_chapter,
               last_chapter, gender, note, book_id
        FROM entities
        WHERE (book_id = ? OR book_id IS NULL)
    """
    params = [book_id]

    if category:
        query += " AND category = ?"
        params.append(category)

    if chapter_clause:
        query += f" AND {chapter_clause}"
        params.extend(chapter_params)

    query += " ORDER BY category, COALESCE(origin_chapter, 0), untranslated"

    conn = db_manager.backend.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
    finally:
        conn.close()

    return rows


def build_matcher(pattern, use_regex, ignore_case):
    """Return a predicate fn(text) -> bool for the given pattern."""
    if not pattern:
        return lambda _text: True

    if use_regex:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            compiled = re.compile(pattern, flags)
        except re.error as e:
            print(f"error: invalid regex {pattern!r}: {e}", file=sys.stderr)
            sys.exit(2)
        return lambda text: bool(text) and compiled.search(text) is not None

    glob_pat = pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"
    if ignore_case:
        glob_pat_cmp = glob_pat.lower()
        return lambda text: bool(text) and fnmatch.fnmatchcase(text.lower(), glob_pat_cmp)
    return lambda text: bool(text) and fnmatch.fnmatchcase(text, glob_pat)


def filter_rows(rows, matcher, field):
    """Apply matcher to the chosen field(s); return list of matching row tuples."""
    matches = []
    for row in rows:
        _category, untranslated, translation, *_ = row
        hit = False
        if field in ("untranslated", "both"):
            if matcher(untranslated or ""):
                hit = True
        if not hit and field in ("translated", "both"):
            if matcher(translation or ""):
                hit = True
        if hit:
            matches.append(row)
    return matches


def render(matches, category_filter):
    """Render matches as text. Group by category unless --category was given."""
    if not matches:
        return "(no matches)\n"

    grouped = {}
    for row in matches:
        (category, untranslated, translation, origin_chapter,
         _last_chapter, _gender, note, _book_id) = row
        grouped.setdefault(category, []).append((untranslated, translation, origin_chapter, note))

    lines = []
    if category_filter:
        entries = []
        for cat in sorted(grouped):
            entries.extend(grouped[cat])
        _emit_entries(lines, entries)
    else:
        for category in sorted(grouped):
            entries = grouped[category]
            if lines:
                lines.append("")
            lines.append(f"== {category} ({len(entries)}) ==")
            _emit_entries(lines, entries)

    return "\n".join(lines) + "\n"


def _emit_entries(lines, entries):
    for untranslated, translation, origin_chapter, note in entries:
        origin_str = f"ch{origin_chapter}" if origin_chapter is not None else "ch?"
        # Notes are shown because the translation model may now revise them; a
        # search is often the first place a bad rewrite would be noticed.
        suffix = f"  [note={note}]" if note else ""
        lines.append(f"  {origin_str:>6}  {untranslated} : {translation or ''}{suffix}")


def main():
    parser = argparse.ArgumentParser(
        description="Search entities in a book by untranslated or translated value.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Pattern modes:\n"
            "  default (glob): * matches anything, ? matches one char, [abc] a char class\n"
            "                  a bare term with no wildcards matches as a substring\n"
            "  --regex:        Python regex syntax (re.search semantics)\n"
            "\n"
            "Chapter filter examples:\n"
            "  --origin-chapter 1-20    chapters 1 through 20\n"
            "  --origin-chapter '>15'   chapters after 15\n"
            "  --origin-chapter '<=99'  chapters up to and including 99\n"
        ),
    )
    parser.add_argument("--book", "-b", required=True,
                        help="Book ID (numeric) or exact title.")
    parser.add_argument("pattern", nargs="?", default=None,
                        help="Search pattern. Omit to list all entities (after filters).")
    parser.add_argument("--category", "-C", default=None,
                        help="Restrict to a single entity category (e.g. character, place).")
    parser.add_argument("--origin-chapter", "-c", default=None,
                        help="Origin chapter filter (e.g. '1-20', '>15', '<=99', '42').")
    parser.add_argument("--field", "-F", choices=("untranslated", "translated", "both"),
                        default="both",
                        help="Which side of the entity to match against (default: both).")
    parser.add_argument("--regex", "-r", action="store_true",
                        help="Treat pattern as a Python regex (default: glob).")
    parser.add_argument("--case-sensitive", "-s", action="store_true",
                        help="Case-sensitive match (default: case-insensitive).")

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

    rows = fetch_entities(
        db_manager, book["id"], args.category, chapter_clause, chapter_params
    )

    matcher = build_matcher(args.pattern, args.regex, ignore_case=not args.case_sensitive)
    matches = filter_rows(rows, matcher, args.field)

    sys.stdout.write(render(matches, args.category))
    print(
        f"# {len(matches)} match(es) in book {book['id']} "
        f"({book.get('title')!r})",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
