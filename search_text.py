#!/usr/bin/env python3
"""
Full-text search across a book's chapters — translated, untranslated, or both.

The search term is a glob pattern by default (`*` = any run of chars, `?` = one
char, `[seq]` = char class), matched as a substring of the chapter text. Pass
--regex to treat the term as a regular expression instead.

Returns the matching chapters (number + title + per-field hit counts) and,
optionally, the context around each match (+/- N characters, default 80).

Usage:
    # Find chapters whose translated OR source text contains "phoenix"
    python search_text.py --book 21 "phoenix"

    # Glob wildcard — "Sword" ... "Master" on the same logical span
    python search_text.py -b 21 "Sword*Master"

    # Search only the source (untranslated) text
    python search_text.py -b 21 --field untranslated "凤凰"

    # Search only the translated text
    python search_text.py -b 21 --field translated "Golden Core"

    # Case-insensitive, with +/- 80 char context windows around each hit
    python search_text.py -b 21 -i --context "golden core"

    # Regex search with a wider 120-char window, restricted to chapters 1-50
    python search_text.py -b 21 --regex --radius 120 --chapters 1-50 "Sword\\s+Master"

Chapter filter (--chapters):
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

# Silence provider-import FutureWarnings and force a quiet logger for a one-shot CLI.
warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger

from get_entities import resolve_book


# --- field selection ---------------------------------------------------------

FIELD_COLUMNS = {
    "translated": "translated_content",
    "untranslated": "untranslated_content",
}
# dict key -> chapter-dict key returned by get_chapter()
FIELD_DICT_KEYS = {
    "translated": "content",
    "untranslated": "untranslated",
}


def fields_for(field):
    """Expand the --field choice into the list of concrete field names."""
    if field == "both":
        return ["untranslated", "translated"]
    return [field]


# --- chapter range filter ----------------------------------------------------

def parse_chapter_filter(expr):
    """Parse a --chapters expression into (sql_fragment, params) over chapter_number.

    Returns (None, []) if expr is falsy. Raises ValueError on malformed input.
    """
    if not expr:
        return None, []
    s = expr.strip()

    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return "chapter_number BETWEEN ? AND ?", [lo, hi]

    m = re.fullmatch(r"(>=|<=|>|<|=)\s*(\d+)", s)
    if m:
        return f"chapter_number {m.group(1)} ?", [int(m.group(2))]

    m = re.fullmatch(r"\d+", s)
    if m:
        return "chapter_number = ?", [int(s)]

    raise ValueError(f"Unrecognized chapter filter: {expr!r}")


# --- candidate chapter discovery --------------------------------------------

def glob_to_like(glob):
    """Convert a glob pattern to a SQL LIKE substring pattern, or None.

    `*`->`%`, `?`->`_`. The LIKE is only a *superset* prefilter — the precise
    Python pass does the real matching — so we don't bother escaping literal
    `%`/`_` (treating them as wildcards just admits a few extra candidate
    chapters). Patterns we can't safely turn into a superset LIKE — char classes
    (`[seq]`) or anything containing a backslash (LIKE's escape char) — return
    None, signalling "no prefilter, scan all chapters".
    """
    if "[" in glob or "\\" in glob:
        return None
    return "%" + glob.replace("*", "%").replace("?", "_") + "%"


def candidate_chapter_numbers(db_manager, book_id, fields, query, regex,
                              chapter_clause, chapter_params):
    """Return ordered chapter numbers to inspect.

    For glob searches we prefilter in SQL with LIKE on the relevant column(s) so
    we only deserialize chapters that can possibly match. For regex (or globs
    with char classes) we can't push the pattern into SQL, so we scan every
    chapter (still bounded by the optional --chapters range).
    """
    conn = db_manager.backend.get_connection()
    try:
        cursor = conn.cursor()
        where = ["book_id = ?"]
        params = [book_id]

        like = None if regex else glob_to_like(query)
        if like is not None:
            cols = [FIELD_COLUMNS[f] for f in fields]
            where.append(
                "(" + " OR ".join(f"{c} LIKE ?" for c in cols) + ")"
            )
            params.extend([like] * len(cols))

        if chapter_clause:
            where.append(chapter_clause)
            params.extend(chapter_params)

        cursor.execute(
            f"""
            SELECT chapter_number
            FROM chapters
            WHERE {' AND '.join(where)}
            ORDER BY chapter_number
            """,
            params,
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


# --- matching ----------------------------------------------------------------

def text_of(chapter, field):
    """Flatten a chapter field (list of lines / JSON / str) into a single string."""
    raw = chapter.get(FIELD_DICT_KEYS[field])
    if isinstance(raw, list):
        return "\n".join(str(p) for p in raw if p is not None)
    if raw is None:
        return ""
    return str(raw)


def build_matcher(query, regex, ignore_case):
    """Compile the search term (glob by default, regex with --regex) into a
    pattern usable for substring matching via re.finditer."""
    flags = re.IGNORECASE if ignore_case else 0
    if regex:
        return re.compile(query, flags)
    # Glob: fnmatch.translate yields an anchored whole-string pattern like
    # '(?s:demon.*sect)\\Z'. Strip the trailing \Z anchor so it matches anywhere
    # in the text (substring semantics), and make `*` lazy (.* -> .*?) so a
    # wildcard span stops at the nearest match instead of swallowing the whole
    # chapter — gives tight, useful context windows.
    pattern = fnmatch.translate(query)
    if pattern.endswith(r"\Z"):
        pattern = pattern[:-2]
    pattern = pattern.replace(".*", ".*?")
    return re.compile(pattern, flags)


def find_matches(text, matcher):
    """Return a list of (start, end) spans for every match in text."""
    if not text:
        return []
    return [(m.start(), m.end()) for m in matcher.finditer(text)]


def context_window(text, start, end, radius):
    """Return the text +/- radius chars around [start, end), with ellipses."""
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    snippet = text[lo:hi].replace("\n", " ").strip()
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


# --- main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Search a book's translated/untranslated chapter text.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", help="Search term (glob by default, or regex with --regex)")
    parser.add_argument("-b", "--book", required=True,
                        help="Book id or exact title")
    parser.add_argument("-f", "--field", choices=["translated", "untranslated", "both"],
                        default="both", help="Which text to search (default: both)")
    parser.add_argument("-i", "--ignore-case", action="store_true",
                        help="Case-insensitive matching")
    parser.add_argument("--regex", action="store_true",
                        help="Treat query as a regular expression (default is glob)")
    parser.add_argument("--chapters", metavar="EXPR",
                        help="Restrict to chapter range (e.g. 1-50, >100, 42)")
    parser.add_argument("-c", "--context", action="store_true",
                        help="Show a context window around each match")
    parser.add_argument("--radius", type=int, default=80, metavar="N",
                        help="Context window size in chars on each side (default 80; "
                             "implies --context)")
    args = parser.parse_args()
    # An explicit --radius implies the user wants context.
    show_context = args.context or ("--radius" in sys.argv)

    if args.regex:
        try:
            re.compile(args.query)
        except re.error as e:
            parser.error(f"Invalid regex: {e}")

    try:
        chapter_clause, chapter_params = parse_chapter_filter(args.chapters)
    except ValueError as e:
        parser.error(str(e))

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    book = resolve_book(db_manager, args.book)
    if not book:
        print(f"Book not found: {args.book!r}", file=sys.stderr)
        sys.exit(1)
    book_id = book["id"]

    matcher = build_matcher(args.query, args.regex, args.ignore_case)

    fields = fields_for(args.field)
    chapter_numbers = candidate_chapter_numbers(
        db_manager, book_id, fields, args.query, args.regex,
        chapter_clause, chapter_params,
    )

    total_matches = 0
    matched_chapters = 0

    for chapter_number in chapter_numbers:
        chapter = db_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
        if not chapter:
            continue

        per_field = []  # (field, spans, text)
        for field in fields:
            text = text_of(chapter, field)
            spans = find_matches(text, matcher)
            if spans:
                per_field.append((field, spans, text))

        if not per_field:
            continue

        matched_chapters += 1
        chap_total = sum(len(s) for _, s, _ in per_field)
        total_matches += chap_total

        title = chapter.get("title") or ""
        counts = ", ".join(f"{field}: {len(spans)}" for field, spans, _ in per_field)
        print(f"\nCh {chapter_number}: {title}".rstrip())
        print(f"  matches — {counts}")

        if show_context:
            for field, spans, text in per_field:
                for start, end in spans:
                    window = context_window(text, start, end, args.radius)
                    print(f"    [{field}] {window}")

    print(
        f"\n{total_matches} match(es) across {matched_chapters} chapter(s) "
        f"in '{book.get('title', book_id)}'."
    )


if __name__ == "__main__":
    main()
