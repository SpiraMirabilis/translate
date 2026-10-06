#!/usr/bin/env python3
"""Report whether a translation job is running, and how far a book has got.

The job state lives in the admin server's in-process job manager, not the
database, so it can only be read over the API (GET /api/translate/status —
the same endpoint the Dashboard's status badge polls). Book progress comes
straight from the DB.

This exists because review and repair work must not run while chapters are
in transit: a --substitute sweep can collide with a chapter mid-save. Checking
with `ps | grep translator.py` is unreliable — it matches your own
`translator.py --list-books` call, and it misses translations driven through
the web UI, which is most of them.

Usage:
    python3 translation_status.py                 # is anything translating?
    python3 translation_status.py -b 77           # + book 77's frontier
    python3 translation_status.py -b 77 --quiet   # exit code only

Exit codes:
    0  idle — safe to run repair/footnote sweeps
    1  a job is running, waiting, or awaiting input — do not sweep
    2  could not reach the server
"""

import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

import httpx
from dotenv import load_dotenv

import t9_client

# Any status other than these means the queue is live in some form.
IDLE_STATUSES = {"idle", "complete", "error"}


def fetch_status(url, password):
    # Mint the session cookie locally rather than POSTing /api/auth/login:
    # login is rate-limited 5/min and 20/hour per IP, and this function gets
    # called in drain loops. Burning that budget locks the admin UI out too.
    with t9_client.client(url, password, timeout=15) as client:
        r = client.get("/api/translate/status")
        if r.status_code == 401:
            print("Not authenticated — set T9_PASSWORD (in .env) or pass --password.",
                  file=sys.stderr)
            return None
        r.raise_for_status()
        return r.json()


def _db():
    from config import TranslationConfig
    from db import DatabaseManager
    from logger import Logger

    config = TranslationConfig()
    return DatabaseManager(config, Logger(config))


def processing_books():
    """Return the set of book_ids with a queue row currently being processed.

    The job-status endpoint is server-wide — it says a translation is running
    but not which book. The queue's 'processing' rows do say, which is what
    actually matters: a sweep on book A is safe while book B translates.
    """
    with _db()._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT book_id FROM queue WHERE status = 'processing'"
        )
        return {row["book_id"] for row in cursor.fetchall()}


def book_progress(book_id):
    """Return (translated_count, max_chapter, queued_count) for a book."""
    with _db()._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS n, MAX(chapter_number) AS hi FROM chapters "
            "WHERE book_id = ?",
            (book_id,),
        )
        row = cursor.fetchone()
        translated, highest = row["n"], row["hi"]

        cursor.execute(
            "SELECT COUNT(*) AS n FROM queue WHERE book_id = ?", (book_id,)
        )
        queued = cursor.fetchone()["n"]

    return translated, highest, queued


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000",
                        help="Base URL of the admin server (default: %(default)s)")
    parser.add_argument("--password", default=os.getenv("T9_PASSWORD"),
                        help="Admin password (default: T9_PASSWORD from env/.env)")
    parser.add_argument("-b", "--book-id", type=int, default=None,
                        help="Also report this book's translated/queued counts.")
    parser.add_argument("--json", action="store_true",
                        help="Emit the raw status payload as JSON.")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Print nothing; communicate via exit code only.")
    args = parser.parse_args()

    try:
        data = fetch_status(args.url, args.password)
    except httpx.HTTPError as exc:
        if not args.quiet:
            print(f"Could not reach the admin server at {args.url}: {exc}",
                  file=sys.stderr)
        return 2

    if data is None:
        return 2

    status = data.get("status", "unknown")
    running = bool(data.get("is_running")) or status not in IDLE_STATUSES
    auto = data.get("auto_process")

    # A server-wide "running" only blocks sweeps on the book actually being
    # translated. If -b names a different book, its chapters are not in
    # transit and repair work on it is safe.
    live_books = processing_books() if running else set()
    server_running = running
    if args.book_id is not None and running and live_books:
        running = args.book_id in live_books
    elsewhere = server_running and not running

    if args.json:
        payload = dict(data)
        if args.book_id is not None:
            translated, highest, queued = book_progress(args.book_id)
            payload["book"] = {
                "id": args.book_id,
                "translated": translated,
                "highest_chapter": highest,
                "queued": queued,
            }
        print(json.dumps(payload, indent=2))
        return 1 if running else 0

    if not args.quiet:
        if running:
            verdict = "BUSY — do not run repair sweeps"
        elif elsewhere:
            verdict = f"running on another book — safe to sweep book {args.book_id}"
        else:
            verdict = "idle — safe to review"
        print(f"Translation: {verdict}")
        print(f"  status:       {status}")
        print(f"  auto-process: {'on' if auto else 'off'}")
        if live_books:
            ids = ", ".join(str(b) for b in sorted(live_books))
            print(f"  translating:  book {ids}")
            if args.book_id is not None and args.book_id not in live_books:
                print(f"  (book {args.book_id} is not in transit — sweeps on it are safe)")
        for bid, job in sorted((data.get("jobs") or {}).items(),
                               key=lambda kv: int(kv[0])):
            left = job.get("auto_remaining")
            if left is not None:
                print(f"  book {bid}:      ch{job.get('chapter_number')}, "
                      f"{left} more after this one")
            opts = job.get("run_options")
            if opts:
                shown = ", ".join(f"{k}={v}" for k, v in opts.items()
                                  if v not in (None, False))
                print(f"  book {bid} run:  {shown or 'defaults'}")
        if data.get("error"):
            print(f"  error:        {data['error']}")
        for key, label in (
            ("pending_review", "awaiting entity review"),
            ("pending_json_fix", "awaiting JSON fix"),
            ("pending_chapter_conflict", "awaiting chapter-conflict decision"),
        ):
            if data.get(key):
                print(f"  ** {label} **")

        if args.book_id is not None:
            translated, highest, queued = book_progress(args.book_id)
            print(f"\nBook {args.book_id}:")
            print(f"  translated:   {translated} (through ch{highest})")
            print(f"  queued:       {queued}")

    return 1 if running else 0


if __name__ == "__main__":
    sys.exit(main())
