#!/usr/bin/env python3
"""
Build (or rebuild) the per-chapter entity index that the reader's
"Terms this chapter" panel reads.

save_chapter indexes a chapter as it writes it, so every chapter translated or
edited from now on maintains itself. This script exists for the two cases that
hook cannot cover:

  * the initial fill of chapters that were saved before the index existed;
  * a book whose glossary has moved on since — entities added, renamed, or
    merged after the chapters were written. The index records which *source
    forms* occur in a chapter, so a term added at ch300 is missing from ch5's
    panel until the book is reindexed. (Translations, categories, genders and
    notes are joined live and never need a reindex.)

Reindexing a book is safe at any time and is not a DB-heavy operation: the
chapter rows are read, not written, and each chapter's index rows are replaced
wholesale.

Usage:
    python3 backfill_chapter_entities.py --book-id 90
    python3 backfill_chapter_entities.py --book-id 90 --missing-only
    python3 backfill_chapter_entities.py --all --missing-only
    python3 backfill_chapter_entities.py --all --dry-run
"""

import argparse
import time

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('-b', '--book-id', type=int, help='Book to index')
    group.add_argument('--all', action='store_true', help='Every book')
    ap.add_argument('--missing-only', action='store_true',
                    help='Skip chapters that already carry index rows (resumable)')
    ap.add_argument('--dry-run', action='store_true',
                    help='Report what would be indexed without writing')
    ap.add_argument('-q', '--quiet', action='store_true',
                    help='One line per book instead of per chapter')
    args = ap.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    if args.all:
        # minimal: the per-book chapter rollups list_books() computes are dead
        # weight here, and there are 80+ books.
        book_ids = [b['id'] for b in (db.list_books_minimal() or [])]
    else:
        if not db.get_book(book_id=args.book_id):
            raise SystemExit(f"Book {args.book_id} not found")
        book_ids = [args.book_id]

    grand_chapters = 0
    grand_rows = 0
    started = time.time()

    for book_id in book_ids:
        book = db.get_book(book_id=book_id)
        title = (book or {}).get('title', f'book {book_id}')
        index_rows = db.entity_index_rows(book_id)

        if args.dry_run:
            # Same matching, no writes — the point is the per-chapter hit count.
            chapters = db.list_chapters(book_id) or []
            todo = 0
            hits = 0
            for meta in chapters:
                ch = db.get_chapter(book_id=book_id, chapter_number=meta['chapter'])
                if not ch:
                    continue
                n = len(db._match_entity_rows(ch.get('untranslated') or [], index_rows))
                todo += 1
                hits += n
                if not args.quiet:
                    print(f"  ch{meta['chapter']}: {n} terms")
            print(f"[dry-run] {title} (book {book_id}): {todo} chapters, "
                  f"{hits} index rows from {len(index_rows)} entities")
            grand_chapters += todo
            grand_rows += hits
            continue

        def progress(chapter_number, n_rows):
            if not args.quiet:
                print(f"  ch{chapter_number}: {n_rows} terms")

        t0 = time.time()
        done, rows = db.reindex_book_chapter_entities(
            book_id, only_missing=args.missing_only, progress=progress)
        print(f"{title} (book {book_id}): {done} chapters indexed, {rows} rows, "
              f"{len(index_rows)} entities in scope, {time.time() - t0:.1f}s")
        grand_chapters += done
        grand_rows += rows

    print(f"\nTotal: {grand_chapters} chapters, {grand_rows} index rows, "
          f"{time.time() - started:.1f}s")


if __name__ == '__main__':
    main()
