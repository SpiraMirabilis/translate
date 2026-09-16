#!/usr/bin/env python3
"""Invalidate cached EPUB/AZW3 for every book that has footnote data.

Footnotes render as EPUB3 noteref/aside markup; a KF8 anchor fix changed that
markup, so every book carrying footnotes needs its cached ebooks purged. The
prewarm_ebooks.py cron regenerates them on its next tick.

    python3 invalidate_footnote_epubs.py            # dry run — list affected books
    python3 invalidate_footnote_epubs.py --apply    # purge caches
"""
import argparse

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger


def books_with_footnotes(db):
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT book_id FROM footnotes ORDER BY book_id')
        return [r[0] for r in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='Actually invalidate caches (default: dry run).')
    args = parser.parse_args()

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config), strict_writes=True)

    book_ids = books_with_footnotes(db)
    if not book_ids:
        print('No books have footnote data.')
        return

    print(f'{len(book_ids)} book(s) with footnotes: '
          + ', '.join(str(b) for b in book_ids))

    if not args.apply:
        print('\nDry run — re-run with --apply to invalidate their EPUB/AZW3 caches.')
        return

    for book_id in book_ids:
        db.invalidate_epub_cache(book_id)
        print(f'  invalidated book {book_id}')
    print(f'\nDone. Invalidated {len(book_ids)} book(s). '
          'prewarm_ebooks.py will regenerate on its next tick.')


if __name__ == '__main__':
    main()
