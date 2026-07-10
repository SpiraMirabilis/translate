#!/usr/bin/env python3
"""
Scan a book's chapters for content that isn't double-spaced (empty line
between every paragraph) and fix them. Both the translated text and the
source (untranslated) text are checked and fixed independently.

Usage:
    python fix_chapter_spacing.py --book 16             # dry-run (default)
    python fix_chapter_spacing.py --book all            # dry-run across all books
    python fix_chapter_spacing.py --book 16 --apply     # actually save changes
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from db_backend import create_backend


# A Markdown table row: a line that both starts and ends with a pipe. Table
# rows are the one Markdown construct that BREAKS when a blank line is inserted
# between them (a blank terminates the table), so they're treated as a single
# block and blank-separated rows are healed back together. Lists/blockquotes
# tolerate blank lines, and dash-prefixed prose (e.g. Russian dialogue "- …")
# must not be mistaken for a list — so only tables get special handling.
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")

# Sentinel (rich) tables: a whole-line ⟦TABLE⟧ … ⟦/TABLE⟧ run (see
# chapterMarkdown.js / output_formatter.py). The entire span is copied
# VERBATIM — inserting blanks between marker lines makes _parse_table_run
# fail (the table renders as literal ⟦TR⟧ text and the write editor opens
# read-only), while blank lines INSIDE the run are meaningful (paragraph
# separators within a cell) and must not be collapsed either.
_SENT_OPEN_RE = re.compile(r"^\s*⟦TABLE⟧\s*$")
_SENT_CLOSE_RE = re.compile(r"^\s*⟦/TABLE⟧\s*$")


def _is_blank(ln):
    """A line counts as blank if it's empty or only whitespace.

    Some imports leave whitespace-only separator lines (e.g. ``'  '``) between
    paragraphs alongside true empty lines; treating those as content would
    re-space *around* them and inflate the gaps, so they're blanks here.
    """
    return (not isinstance(ln, str)) or ln.strip() == ""


def _is_table_row(ln):
    return isinstance(ln, str) and bool(_TABLE_RE.match(ln))


def double_space(lines):
    """Re-space to exactly one blank line between paragraphs.

    Blank lines (including whitespace-only ones) and runs of multiple blanks are
    collapsed to a single separator. A run of consecutive Markdown table rows is
    kept together as one block, and table rows separated only by blank lines are
    healed back into a contiguous table (a stray blank would break the render).
    A sentinel ⟦TABLE⟧…⟦/TABLE⟧ run is one opaque unit copied verbatim,
    interior blank lines included (they separate paragraphs inside cells).
    """
    if not isinstance(lines, list):
        return lines

    units = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if _is_blank(ln):
            i += 1
            continue

        if isinstance(ln, str) and _SENT_OPEN_RE.match(ln):
            # Copy the whole terminated run verbatim. An unterminated open
            # marker is malformed content (renders as literal text anyway);
            # fall through and treat the line as an ordinary paragraph.
            j = i + 1
            while j < n and not (isinstance(lines[j], str) and _SENT_CLOSE_RE.match(lines[j])):
                j += 1
            if j < n:
                units.append(list(lines[i:j + 1]))
                i = j + 1
                continue

        if _is_table_row(ln):
            # Consecutive pipe rows form one unit; rows separated only by
            # blanks are healed back together (dropping the blanks).
            unit = [ln]
            i += 1
            while i < n:
                if _is_table_row(lines[i]):
                    unit.append(lines[i])
                    i += 1
                    continue
                if _is_blank(lines[i]):
                    j = i
                    while j < n and _is_blank(lines[j]):
                        j += 1
                    if j < n and _is_table_row(lines[j]):
                        i = j
                        continue
                break
            units.append(unit)
            continue

        units.append([ln])
        i += 1

    out = []
    for u_i, unit in enumerate(units):
        out.extend(unit)
        if u_i != len(units) - 1:
            out.append("")
    return out


def needs_fix(lines):
    """
    True when the chapter isn't already uniformly double-spaced — i.e. when
    re-spacing would change anything. This covers adjacent paragraphs with no
    blank between them, runs of multiple blank lines, whitespace-only separator
    lines that should have been empty, and tables broken apart by stray blanks.
    """
    if not isinstance(lines, list):
        return False
    paragraphs = sum(1 for ln in lines if not _is_blank(ln))
    if paragraphs < 2:
        return False
    return double_space(lines) != lines


# Columns to scan/fix, each labelled for the dry-run output.
COLUMNS = (
    ("translated_content", "translated"),
    ("untranslated_content", "source"),
)


def fix_column(raw):
    """
    Decide whether a single content column needs fixing.

    Returns (status, new_raw) where status is one of:
      "fixed"        -> new_raw is the re-spaced JSON to save
      "ok"           -> already double-spaced, nothing to do
      "empty"        -> no content to process
      "unparseable"  -> not valid JSON / not a list of lines
    """
    if raw is None or raw == "":
        return "empty", None

    try:
        lines = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "unparseable", None

    if not isinstance(lines, list):
        return "unparseable", None

    if not needs_fix(lines):
        return "ok", None

    new_lines = double_space(lines)
    return "fixed", (json.dumps(new_lines, ensure_ascii=False), len(lines), len(new_lines))


def process_book(cursor, book_id, dry_run):
    cursor.execute(
        "SELECT id, chapter_number, title, translated_content, untranslated_content "
        "FROM chapters WHERE book_id = ? ORDER BY chapter_number",
        (book_id,),
    )
    rows = cursor.fetchall()

    fixed = 0
    skipped_unparseable = 0
    already_ok = 0

    for chap_id, chap_num, title, translated_raw, source_raw in rows:
        raws = {"translated_content": translated_raw, "untranslated_content": source_raw}

        for column, label in COLUMNS:
            status, payload = fix_column(raws[column])

            if status == "empty":
                continue
            if status == "unparseable":
                skipped_unparseable += 1
                continue
            if status == "ok":
                already_ok += 1
                continue

            new_raw, old_count, new_count = payload
            print(f"  book={book_id} ch={chap_num} (id={chap_id}) [{label}] "
                  f"{old_count} -> {new_count} lines  '{title}'")

            if not dry_run:
                cursor.execute(
                    f"UPDATE chapters SET {column} = ? WHERE id = ?",
                    (new_raw, chap_id),
                )
            fixed += 1

    return fixed, already_ok, skipped_unparseable, len(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Fix chapters that aren't double-spaced (re-interleave empty lines).",
    )
    parser.add_argument(
        "--book",
        required=True,
        help="Book ID to process, or 'all' for every book.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Save changes to the database. Without this, runs as a dry-run preview.",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    backend = create_backend()
    conn = backend.get_connection()
    cursor = conn.cursor()

    if args.book.lower() == "all":
        cursor.execute("SELECT id, title FROM books ORDER BY id")
        books = cursor.fetchall()
    else:
        try:
            bid = int(args.book)
        except ValueError:
            print(f"--book must be an integer or 'all', got {args.book!r}")
            sys.exit(1)
        cursor.execute("SELECT id, title FROM books WHERE id = ?", (bid,))
        books = cursor.fetchall()
        if not books:
            print(f"No book with id={bid}")
            sys.exit(1)

    grand_fixed = grand_ok = grand_bad = grand_total = 0

    for book_id, book_title in books:
        print(f"\n=== Book {book_id}: {book_title} ===")
        fixed, ok, bad, total = process_book(cursor, book_id, dry_run)
        print(f"  -> {fixed} fixed, {ok} already ok, {bad} unparseable, {total} total")
        grand_fixed += fixed
        grand_ok += ok
        grand_bad += bad
        grand_total += total

    if not dry_run:
        conn.commit()
    conn.close()

    prefix = "[DRY RUN] " if dry_run else ""
    print(
        f"\n{prefix}Done. Fixed: {grand_fixed}, "
        f"Already ok: {grand_ok}, Unparseable: {grand_bad}, "
        f"Total scanned: {grand_total}"
    )


if __name__ == "__main__":
    main()
