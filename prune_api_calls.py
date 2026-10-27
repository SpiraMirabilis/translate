#!/usr/bin/env python3
"""Delete API-call logs older than the retention window (default 60 days).

api_calls stores the full prompt and response of every model call — the
biggest table by far (4.6 GB on 2026-10-27, ~1.5 GB/month at the current
translation rate). It is a debugging record: the API Calls page and replaying
stored responses through the JSON/repetition checks. Nothing derives from it,
so old rows can simply go.

Cron (daily, after the 03:30 backup):
    50 3 * * * cd /home/mdm/t9 && /usr/bin/python3 prune_api_calls.py >> logs/prune_api_calls.log 2>&1

Deleting rows does not shrink an InnoDB table file — the freed pages are
reused by new rows, so a daily prune holds the file at its steady-state size.
To hand space back to the disk once (after the first big prune), run with
--optimize, which rebuilds the table (needs free space for one copy of what
remains).
"""
import argparse
import datetime
import time

DEFAULT_DAYS = 60


def main():
    ap = argparse.ArgumentParser(description="Delete API-call logs older than N days.")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"Keep this many days of logs (default {DEFAULT_DAYS})")
    ap.add_argument("--dry-run", action="store_true", help="Count, delete nothing")
    ap.add_argument("--optimize", action="store_true",
                    help="Rebuild the table afterwards to return freed space to the disk (MySQL)")
    args = ap.parse_args()
    if args.days < 1:
        ap.error("--days must be at least 1")

    from config import TranslationConfig
    from logger import Logger
    from database import DatabaseManager

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))

    started = time.monotonic()
    # The short pause between batches leaves the db host room for the
    # translators' inserts during a large first prune.
    n = db.prune_api_calls(args.days, dry_run=args.dry_run, pause=0.05)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    verb = "would delete" if args.dry_run else "deleted"
    print(f"{stamp} {verb} {n} api_calls rows older than {args.days} days "
          f"({time.monotonic() - started:.1f}s)", flush=True)

    if args.optimize and not args.dry_run:
        if config.db_backend.lower() != "mysql":
            print("--optimize is MySQL-only; skipped")
            return
        started = time.monotonic()
        with db._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("OPTIMIZE TABLE api_calls")
            result = cursor.fetchall()
        print(f"OPTIMIZE TABLE api_calls: {result} ({time.monotonic() - started:.1f}s)")


if __name__ == "__main__":
    main()
