#!/usr/bin/env python3
"""
Plain find-and-replace across a book's chapters.

Unlike correct_entity_translation.py (which is entity-aware and case-preserving),
this is a generic substitution tool: it swaps one string for another in chapter
text. It runs on the TRANSLATED text by default; pass --source to rewrite the
untranslated (original) text instead. Scope it book-wide (default) or to a subset
of chapters with --chapters (same filter syntax as search_entities.py's
--origin-chapter, but matched against chapter_number).

Dry-run is the default — pass --apply to actually write.

Usage:
    # Book-wide, translated text (dry-run)
    python substitute_text.py -b 5 "Golden Pill" "Golden Core"

    # Apply it
    python substitute_text.py -b 5 "Golden Pill" "Golden Core" --apply

    # Only chapters 1-50
    python substitute_text.py -b 5 "Golden Pill" "Golden Core" -c 1-50 --apply

    # Source (untranslated) text, chapters after 100
    python substitute_text.py -b 5 "軟體" "软件" --source -c ">100" --apply

    # Case-insensitive / regex
    python substitute_text.py -b 5 "golden pill" "Golden Core" --ignore-case --apply
    python substitute_text.py -b 5 "Chapter (\\d+)" "Ch. \\1" --regex --apply

Chapter filter syntax for --chapters:
    N         exact chapter
    N-M       inclusive range
    >N, >=N   greater than (or equal)
    <N, <=N   less than (or equal)

------------------------------------------------------------------------
Follows the post-2026-07 migration template (see get_entities.py's header):
DatabaseManager from the `db` package, read/write SQL inside
`with db_manager._conn(dict_rows=True) as conn:`, strict_writes on the
writing manager, resolve_book/parse-filter helpers reused from get_entities.
------------------------------------------------------------------------
"""

import argparse
import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

from get_entities import parse_chapter_filter, resolve_book


def build_pattern(old, use_regex, ignore_case):
    """Compile the search pattern. Literal unless --regex; case per --ignore-case."""
    flags = re.IGNORECASE if ignore_case else 0
    expr = old if use_regex else re.escape(old)
    try:
        return re.compile(expr, flags)
    except re.error as e:
        print(f"error: invalid regex {old!r}: {e}", file=sys.stderr)
        sys.exit(2)


def build_replacement(new, use_regex):
    """Return an re.sub replacement.

    In literal mode we return a function so backslashes/group refs in `new`
    are inserted verbatim. In regex mode `new` is used as a template, so
    backreferences like \\1 work.
    """
    if use_regex:
        return new
    return lambda _m: new


def decode_content(raw):
    """Decode a stored chapter content column into (lines, was_json).

    Content is normally a JSON array of lines (ensure_ascii=False). Legacy
    rows may be plain text — those are treated as a single-line list, and
    re-stored as plain text (was_json=False) to avoid changing their shape.
    """
    if raw is None:
        return None, False
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [raw], False
    if isinstance(parsed, list):
        return parsed, True
    # A JSON scalar (e.g. a bare quoted string) — treat as one line, keep JSON.
    return [str(parsed)], True


def encode_content(lines, was_json):
    """Re-encode lines back into the stored column form."""
    if was_json:
        return json.dumps(lines, ensure_ascii=False)
    return "\n".join(lines)


def substitute(pattern, repl, lines):
    """Apply the substitution to a list of lines.

    Returns (new_lines, lines_changed, total_matches).
    """
    new_lines = []
    lines_changed = 0
    total_matches = 0
    for line in lines:
        new_line, n = pattern.subn(repl, line)
        if n:
            total_matches += n
            if new_line != line:
                lines_changed += 1
        new_lines.append(new_line)
    return new_lines, lines_changed, total_matches


def run(db_manager, book_id, column, pattern, repl, chapter_clause, chapter_params, apply):
    """Sweep the substitution over the selected chapters.

    Returns (chapters_changed, chapters_scanned, total_matches).
    """
    query = f"SELECT id, chapter_number, {column} AS content FROM chapters WHERE book_id = ?"
    params = [book_id]
    if chapter_clause:
        query += f" AND {chapter_clause}"
        params.extend(chapter_params)
    query += " ORDER BY chapter_number"

    chapters_changed = 0
    chapters_scanned = 0
    total_matches = 0

    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

        for r in rows:
            chapters_scanned += 1
            lines, was_json = decode_content(r["content"])
            if lines is None:
                continue

            new_lines, lines_changed, matches = substitute(pattern, repl, lines)
            if not lines_changed:
                continue

            chapters_changed += 1
            total_matches += matches
            print(f"  ch{r['chapter_number']}: {lines_changed} line(s), {matches} match(es)")

            if apply:
                cursor.execute(
                    f"UPDATE chapters SET {column} = ? WHERE id = ?",
                    (encode_content(new_lines, was_json), r["id"]),
                )

    return chapters_changed, chapters_scanned, total_matches


def main():
    parser = argparse.ArgumentParser(
        description="Plain find-and-replace across a book's chapters.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Chapter filter examples (--chapters, matched on chapter_number):\n"
            "  -c 1-20     chapters 1 through 20\n"
            "  -c '>15'    chapters after 15\n"
            "  -c '<=99'   chapters up to and including 99\n"
            "  -c 42       exactly chapter 42\n"
        ),
    )
    parser.add_argument("--book-id", "-b", required=True,
                        help="Book ID (numeric) or exact title.")
    parser.add_argument("old", nargs="?", default=None,
                        help="Text (or regex with --regex) to find. If it begins with "
                             "'-', use --old=... or put both values after a '--'.")
    parser.add_argument("new", nargs="?", default=None,
                        help="Replacement text.")
    parser.add_argument("--old", dest="old_flag", metavar="TEXT", default=None,
                        help="Alternative to the positional 'old'; use for values that "
                             "start with '-' (e.g. --old=---xxx---).")
    parser.add_argument("--new", dest="new_flag", metavar="TEXT", default=None,
                        help="Alternative to the positional 'new' (e.g. --new=---).")
    parser.add_argument("--chapters", "-c", default=None,
                        help="Restrict to these chapters (e.g. '1-20', '>15', '42'). "
                             "Default: whole book.")
    parser.add_argument("--source", action="store_true",
                        help="Operate on the untranslated (source) text instead of "
                             "the translated text.")
    parser.add_argument("--regex", "-r", action="store_true",
                        help="Treat 'old' as a Python regex and 'new' as a template "
                             "(backreferences like \\1 work).")
    parser.add_argument("--ignore-case", "-i", action="store_true",
                        help="Case-insensitive match (default: case-sensitive).")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write changes. Without this it's a dry-run.")
    args = parser.parse_args()

    # Resolve old/new from either the positional or the --old/--new flags. The
    # flags exist because argparse treats a positional value beginning with '-'
    # (e.g. "---") as an option; --old=... / --new=... sidestep that.
    old = args.old_flag if args.old_flag is not None else args.old
    new = args.new_flag if args.new_flag is not None else args.new
    if old is None or new is None:
        parser.error("need a search string and a replacement — pass them as positional "
                     "old/new, or as --old=... --new=... when they start with '-'.")

    try:
        chapter_clause, chapter_params = parse_chapter_filter(args.chapters)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    # parse_chapter_filter targets the entities table's origin_chapter column;
    # here the same expressions apply to chapters.chapter_number.
    if chapter_clause:
        chapter_clause = chapter_clause.replace("origin_chapter", "chapter_number")

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    book = resolve_book(db_manager, args.book_id)
    if not book:
        print(f"error: book not found: {args.book_id!r}", file=sys.stderr)
        sys.exit(1)

    column = "untranslated_content" if args.source else "translated_content"
    pattern = build_pattern(old, args.regex, args.ignore_case)
    repl = build_replacement(new, args.regex)

    scope = args.chapters or "whole book"
    mode = "source" if args.source else "translated"
    print(f"Book {book['id']} ({book.get('title')!r}) — {mode} text, chapters: {scope}")
    print(f"  {old!r} -> {new!r}"
          f"{' [regex]' if args.regex else ''}"
          f"{' [ignore-case]' if args.ignore_case else ''}")
    print("  " + ("APPLYING" if args.apply else "DRY-RUN (pass --apply to write)"))

    changed, scanned, matches = run(
        db_manager, book["id"], column, pattern, repl,
        chapter_clause, chapter_params, args.apply,
    )

    verb = "Changed" if args.apply else "Would change"
    print(f"\n{verb} {changed} of {scanned} chapter(s), {matches} match(es) total.")
    if changed and not args.apply:
        print("Re-run with --apply to write these changes.")


if __name__ == "__main__":
    main()
