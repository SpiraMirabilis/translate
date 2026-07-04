#!/usr/bin/env python3
"""
Clean Grammarly-paste artifacts out of stored chapter content.

Rich-text pastes into the write editor (before pasteCleanup.js existed) saved
paragraphs consisting only of U+00A0 (non-breaking space) into
translated_content, rendering as double/triple blank gaps in the Reader, plus
stray inline nbsp inside prose. This script:

  1. blanks lines that contain only whitespace (incl. nbsp) / zero-width chars,
  2. replaces inline nbsp with a regular space (disable: --keep-inline-nbsp),
  3. collapses runs of 2+ empty lines to one ("" is the canonical paragraph
     separator in the line-array storage format),
  4. trims leading/trailing empty lines (mirrors the editor's trimEmptyLines).

Idempotent — a second run is a no-op. Dry-run by default; --apply to write.

Usage:
    python3 clean_nbsp_gaps.py                    # dry-run, all original books
    python3 clean_nbsp_gaps.py --book-id 57       # dry-run, one book
    python3 clean_nbsp_gaps.py --book-id 57 --apply

Follows the script-migration template (see get_entities.py /
correct_entity_translation.py): db package, strict_writes, all SQL inside
`with db._conn(dict_rows=True)` — dual-backend (SQLite + MySQL) safe.

Note: unlike the entity-substitution scripts this one bumps translation_date.
That column doubles as the write editor's optimistic-lock token — without the
bump, an editor tab opened before the run could save afterwards and silently
reintroduce the gaps; with it, that tab gets the standard conflict banner.
"""

import argparse
import datetime
import json
import re
import sys

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

# Whitespace-only line → canonical empty separator. \s already matches U+00A0
# and the Unicode Zs set on str patterns; ZWSP/BOM are Grammarly strays Python
# doesn't class as whitespace.
BLANK_RE = re.compile(r'^[\s\u200b\ufeff]*$')
NBSP = '\u00a0'


def clean_lines(lines, fix_inline_nbsp=True):
    """Pure transform. Returns (new_lines, stats) — stats is None when
    nothing changed."""
    blanked = 0
    inline = 0
    out = []
    for line in lines:
        if line and BLANK_RE.match(line):
            if line != '':
                blanked += 1
            line = ''
        elif fix_inline_nbsp and NBSP in line:
            inline += line.count(NBSP)
            line = line.replace(NBSP, ' ')
        out.append(line)

    collapsed = []
    removed = 0
    for line in out:
        if line == '' and collapsed and collapsed[-1] == '':
            removed += 1
            continue
        collapsed.append(line)
    while collapsed and collapsed[0] == '':
        collapsed.pop(0)
        removed += 1
    while collapsed and collapsed[-1] == '':
        collapsed.pop()
        removed += 1

    if collapsed == list(lines):
        return lines, None
    return collapsed, {'blanked': blanked, 'inline': inline, 'removed': removed}


def resolve_books(db, book_id):
    if book_id is not None:
        book = db.get_book(book_id=book_id)
        if not book:
            print(f"Book {book_id} not found.")
            sys.exit(1)
        if not book.get('is_original'):
            print(f"⚠️  Book {book_id} ({book.get('title')!r}) is not an original work — "
                  f"proceeding anyway (nbsp cleanup is safe on translations too).")
        return [book]
    return [b for b in db.list_books() if b.get('is_original')]


def process_book(db, book, apply_changes, fix_inline_nbsp):
    """Scan (and optionally rewrite) one book. Returns per-book stats."""
    stats = {'scanned': 0, 'modified': 0, 'blanked': 0, 'inline': 0, 'removed': 0}
    now_iso = datetime.datetime.now().isoformat()

    with db._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, chapter_number, translated_content FROM chapters "
            "WHERE book_id = ? ORDER BY chapter_number",
            (book['id'],),
        )
        for row in cursor.fetchall():
            try:
                content = json.loads(row['translated_content'])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(content, list):
                continue
            stats['scanned'] += 1

            new_lines, ch = clean_lines(content, fix_inline_nbsp)
            if ch is None:
                continue
            stats['modified'] += 1
            for k in ('blanked', 'inline', 'removed'):
                stats[k] += ch[k]
            print(f"  ch {row['chapter_number']}: {ch['removed']} gap line(s) removed, "
                  f"{ch['blanked']} nbsp-only line(s) blanked, "
                  f"{ch['inline']} inline nbsp replaced")
            if apply_changes:
                # translation_date bump = write-editor optimistic-lock token
                # (see module docstring).
                cursor.execute(
                    "UPDATE chapters SET translated_content = ?, translation_date = ? "
                    "WHERE id = ?",
                    (json.dumps(new_lines, ensure_ascii=False), now_iso, row['id']),
                )
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Remove nbsp-gap paragraphs and inline nbsp from stored chapters.")
    parser.add_argument("--book-id", type=int,
                        help="Restrict to one book (default: all original works).")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes (default is a dry run).")
    parser.add_argument("--keep-inline-nbsp", action="store_true",
                        help="Only fix blank-gap lines; leave nbsp inside prose lines alone.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger, strict_writes=True)

    books = resolve_books(db, args.book_id)
    if not books:
        print("No original-work books found.")
        return

    totals = {'books': 0, 'scanned': 0, 'modified': 0, 'blanked': 0, 'inline': 0, 'removed': 0}
    for book in books:
        print(f"Book {book['id']}: {book.get('title')!r}")
        stats = process_book(db, book, args.apply, not args.keep_inline_nbsp)
        if stats['modified'] and args.apply:
            db.invalidate_epub_cache(book['id'])
        if not stats['modified']:
            print("  clean — nothing to do")
        totals['books'] += 1
        for k in ('scanned', 'modified', 'blanked', 'inline', 'removed'):
            totals[k] += stats[k]

    print(f"\n{'Applied' if args.apply else 'Would apply'}: "
          f"{totals['modified']}/{totals['scanned']} chapter(s) across {totals['books']} book(s) — "
          f"{totals['removed']} gap line(s) removed, {totals['blanked']} nbsp-only line(s) blanked, "
          f"{totals['inline']} inline nbsp replaced.")
    if not args.apply and totals['modified']:
        print("Dry run — re-run with --apply to write.")


if __name__ == "__main__":
    main()
