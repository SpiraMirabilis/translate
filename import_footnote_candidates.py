#!/usr/bin/env python3
"""One-time import of the legacy standalone footnote_candidates.db into the
main DB (footnote_candidates / footnote_scans tables, migration 16).

Reads the old scratch SQLite file and replays it through the repo, preserving
review statuses (legacy 'kept' is mapped to 'accepted') and the scan-guard
rows that prevent re-scanning. Idempotent: each (book, chapter) is imported
as a full replace, so re-running just overwrites the previous import.

Books missing from the main DB are skipped with a warning (their rows stay in
the old file). The old file itself is never modified — it remains on disk as
an archive.

Usage:
    python3 import_footnote_candidates.py [--db footnote_candidates.db] [--dry-run]
"""
import argparse
import os
import sqlite3
import sys

DEFAULT_OLD_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "footnote_candidates.db")

# The TUI briefly wrote 'kept' before the status settled on 'accepted'.
STATUS_MAP = {"kept": "accepted"}
VALID = {"pending", "accepted", "rejected"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_OLD_DB,
                    help=f"Legacy candidates SQLite file (default {DEFAULT_OLD_DB})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be imported; write nothing")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"Legacy file not found: {args.db}", file=sys.stderr)
        return 1

    old = sqlite3.connect(args.db)
    old.row_factory = sqlite3.Row
    cands = [dict(r) for r in old.execute(
        "SELECT * FROM footnote_candidates ORDER BY book_id, chapter_number, id")]
    scans = [dict(r) for r in old.execute(
        "SELECT * FROM footnote_scans ORDER BY book_id, chapter_number")]
    old.close()

    # Group candidates by (book, chapter); every legacy candidate chapter has
    # a scan row (verified on the real file), but tolerate strays by importing
    # them under a forced-stale scan row (empty hash → next scan re-runs).
    by_chapter = {}
    for c in cands:
        by_chapter.setdefault((c["book_id"], c["chapter_number"]), []).append(c)
    scan_by_chapter = {(s["book_id"], s["chapter_number"]): s for s in scans}

    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config), strict_writes=True)

    book_ids = sorted({bid for bid, _ in by_chapter} | {bid for bid, _ in scan_by_chapter})
    n_cand = n_scan = n_remapped = 0
    for book_id in book_ids:
        book = db.get_book(book_id=book_id)
        chapters = sorted({cn for b, cn in scan_by_chapter if b == book_id}
                          | {cn for b, cn in by_chapter if b == book_id})
        b_cands = sum(len(by_chapter.get((book_id, cn), [])) for cn in chapters)
        if not book:
            print(f"book {book_id}: NOT in main DB — skipping "
                  f"{b_cands} candidate(s), {len(chapters)} scan row(s)")
            continue
        print(f"book {book_id} ({book.get('title', '')}): "
              f"{b_cands} candidate(s) across {len(chapters)} scanned chapter(s)")
        if args.dry_run:
            continue
        for cn in chapters:
            rows = by_chapter.get((book_id, cn), [])
            scan = scan_by_chapter.get((book_id, cn))
            found = []
            for c in rows:
                status = STATUS_MAP.get(c["status"], c["status"])
                if status not in VALID:
                    status = "pending"
                if status != c["status"]:
                    n_remapped += 1
                found.append({
                    "term_zh": c["term_zh"], "term_en": c["term_en"],
                    "body": c["body"], "sentence": c["sentence"],
                    "model": c["model"], "status": status,
                    "created_date": c["created_date"],
                })
            title = rows[0]["chapter_title"] if rows else None
            db.record_footnote_scan(
                book_id, cn, title,
                scan["model"] if scan else (rows[0]["model"] if rows else ""),
                scan["content_hash"] if scan else "",
                found,
                scanned_at=scan["scanned_at"] if scan else None)
            n_cand += len(found)
            n_scan += 1

    if args.dry_run:
        print("(dry run — nothing written)")
    else:
        print(f"Imported {n_cand} candidate(s) and {n_scan} scan row(s)."
              + (f" Remapped {n_remapped} legacy status value(s)." if n_remapped else ""))
        print(f"The legacy file is untouched: {args.db} (keep as archive or delete).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
