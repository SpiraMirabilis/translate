#!/usr/bin/env python3
"""Retroactively re-run the Site Ad Stripper over stored source text.

The twkan module (``modules/twkan.py``) runs at *ingest* only, so chapters and
queue rows that were stored before a pattern was added — or before a matching
bug was fixed — keep their spam forever. This is the backfill.

It rewrites ``chapters.untranslated_content`` and ``queue.content`` in place,
using each book's own resolved module settings. Deliberately narrow: it does
NOT go through ``save_chapter``, so no other source module runs, footnotes are
not re-rendered, and ``modified_date`` is untouched. Translated text is never
modified — ``--scan-translated`` only *reports* leaks for manual repair.

Usage:
    python3 bulk_strip_ads.py --dry-run                    # every book, preview
    python3 bulk_strip_ads.py --apply                      # every book, commit
    python3 bulk_strip_ads.py --book-id 82 --dry-run
    python3 bulk_strip_ads.py --enabled-only --apply       # only auto-enabled books
    python3 bulk_strip_ads.py --scan-translated            # report English-side leaks

By default every book is swept, whether or not the module auto-enables for its
``source_url``: a book whose raws carry no spam simply reports zero changes, and
mirrors get swapped often enough that enablement is a poor proxy for exposure.
Pass ``--enabled-only`` to restrict to books the module is actually on for.
"""

import argparse
import json
import os
import sys
import unicodedata
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger
from modules import resolve_module_ids
from modules.twkan import TwkanModule

MODULE = TwkanModule()

# Markers looked for on the ENGLISH side. A hit means the spam survived ingest
# and got fed to the translator, so it needs a prose-level fix, not a re-strip.
TRANSLATED_MARKERS = ("twkan", "69shux", "shux.co", "taiwan novel network")


def _lines(content):
    """Best-effort line list from a stored content blob (for counting only)."""
    if isinstance(content, list):
        return [l for l in content if isinstance(l, str)]
    if isinstance(content, str):
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return [l for l in data if isinstance(l, str)]
        except (json.JSONDecodeError, TypeError):
            pass
        return content.split("\n")
    return []


def _delta(before, after):
    """(lines_removed, chars_removed) between two stored content blobs."""
    b, a = _lines(before), _lines(after)
    return len(b) - len(a), sum(len(l) for l in b) - sum(len(l) for l in a)


def _ctx(db, book_id):
    """Module ctx carrying just this book's stored settings."""
    try:
        settings = db.get_module_settings(book_id)
    except Exception:  # noqa: BLE001 — a settings read must not abort the sweep
        settings = {}
    return {"module_settings": settings or {}}


def strip_book(db, book, dry_run, verbose):
    """Sweep one book's chapters + queue. Returns a stats dict."""
    book_id = book["id"]
    ctx = _ctx(db, book_id)
    stats = {"ch_changed": 0, "ch_lines": 0, "ch_chars": 0,
             "q_changed": 0, "q_lines": 0, "q_chars": 0, "q_skipped": 0}

    with db._conn() as conn:
        cursor = conn.cursor()

        # --- chapters -------------------------------------------------------
        cursor.execute(
            "SELECT id, chapter_number, untranslated_content FROM chapters "
            "WHERE book_id = ? ORDER BY chapter_number ASC",
            (book_id,),
        )
        for row_id, ch_num, content in cursor.fetchall():
            if not content:
                continue
            new = MODULE.transform_source_lines(content, ctx)
            if new == content:
                continue
            dl, dc = _delta(content, new)
            stats["ch_changed"] += 1
            stats["ch_lines"] += dl
            stats["ch_chars"] += dc
            if verbose:
                print(f"    ch{ch_num}: -{dl} line(s), -{dc} char(s)")
            if not dry_run:
                cursor.execute(
                    "UPDATE chapters SET untranslated_content = ? WHERE id = ?",
                    (new, row_id),
                )

        # --- queue ----------------------------------------------------------
        cursor.execute(
            "SELECT id, chapter_number, content, status FROM queue "
            "WHERE book_id = ? ORDER BY position ASC",
            (book_id,),
        )
        for q_id, ch_num, content, status in cursor.fetchall():
            if status == "processing":
                # In flight — rewriting its source underneath the run would race.
                stats["q_skipped"] += 1
                continue
            if not content:
                continue
            new = MODULE.transform_source_lines(content, ctx)
            if new == content:
                continue
            dl, dc = _delta(content, new)
            stats["q_changed"] += 1
            stats["q_lines"] += dl
            stats["q_chars"] += dc
            if verbose:
                label = f"ch {ch_num}" if ch_num is not None else "no chapter#"
                print(f"    queue {q_id} ({label}): -{dl} line(s), -{dc} char(s)")
            if not dry_run:
                cursor.execute(
                    "UPDATE queue SET content = ? WHERE id = ?", (new, q_id))

    return stats


def scan_translated(db, books):
    """Report ad markers that leaked into TRANSLATED text (never rewrites)."""
    print("\n" + "=" * 70)
    print("TRANSLATED-SIDE LEAK SCAN (report only — these need a prose fix)")
    print("=" * 70)
    total = 0
    for book in books:
        book_id = book["id"]
        hits = []
        with db._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT chapter_number, translated_content FROM chapters "
                "WHERE book_id = ? ORDER BY chapter_number ASC",
                (book_id,),
            )
            for ch_num, content in cursor.fetchall():
                for line in _lines(content):
                    low = unicodedata.normalize("NFKD", line).casefold()
                    if any(m in low for m in TRANSLATED_MARKERS):
                        hits.append((ch_num, line.strip()[:110]))
        if hits:
            total += len(hits)
            print(f"\n  Book {book_id} — {book.get('title', '?')}: {len(hits)} line(s)")
            for ch_num, line in hits[:10]:
                print(f"     ch{ch_num}: {line}")
            if len(hits) > 10:
                print(f"     … and {len(hits) - 10} more")
    print(f"\n  Total leaked lines across all books: {total}")
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--book-id", type=int, help="Limit to one book.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Preview without writing (the default).")
    mode.add_argument("--apply", action="store_true",
                      help="Commit the changes.")
    parser.add_argument("--enabled-only", action="store_true",
                        help="Only sweep books the module auto-enables for.")
    parser.add_argument("--scan-translated", action="store_true",
                        help="Also report ad markers that leaked into English text.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every changed chapter / queue row.")
    args = parser.parse_args()

    dry_run = not args.apply

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger, strict_writes=True)

    books = db.list_books()
    if args.book_id:
        books = [b for b in books if b["id"] == args.book_id]
        if not books:
            print(f"No book with id {args.book_id}.")
            return 1

    if args.enabled_only:
        books = [b for b in books if "twkan" in resolve_module_ids(b)]

    print(f"Books to sweep: {len(books)}   Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print("=" * 70)

    totals = {"ch_changed": 0, "ch_lines": 0, "ch_chars": 0,
              "q_changed": 0, "q_lines": 0, "q_chars": 0, "q_skipped": 0}
    touched = 0
    for book in books:
        stats = strip_book(db, book, dry_run, args.verbose)
        if stats["ch_changed"] or stats["q_changed"]:
            touched += 1
            print(f"  Book {book['id']:>3} — {str(book.get('title', '?'))[:44]:<44} "
                  f"chapters {stats['ch_changed']:>4} (-{stats['ch_chars']} chars) | "
                  f"queue {stats['q_changed']:>4} (-{stats['q_chars']} chars)")
        for k in totals:
            totals[k] += stats[k]

    print("=" * 70)
    print(f"Books with changes        : {touched} / {len(books)}")
    print(f"Chapters rewritten        : {totals['ch_changed']} "
          f"({totals['ch_lines']} whole lines, {totals['ch_chars']} chars removed)")
    print(f"Queue rows rewritten      : {totals['q_changed']} "
          f"({totals['q_lines']} whole lines, {totals['q_chars']} chars removed)")
    if totals["q_skipped"]:
        print(f"Queue rows skipped        : {totals['q_skipped']} (status=processing)")
    if dry_run:
        print("\nDRY RUN — nothing was written. Re-run with --apply to commit.")

    if args.scan_translated:
        scan_translated(db, books)
    return 0


if __name__ == "__main__":
    sys.exit(main())
