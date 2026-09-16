#!/usr/bin/env python3
"""Turn off auto-processing for the translation queue.

CLI equivalent of the "Stop after current" button on the Queue page: the queue
finishes the chapter it's translating right now, then stops instead of pulling
the next item. A no-op if auto-process isn't running.

⚠️ With several books translating at once, an unscoped stop halts ALL of them —
the endpoint treats a missing book_id as "every running job". Pass -b to stop
only the book you are about to sweep.

Usage:
    python3 stop_auto_process.py -b 93      # stop just this book
    python3 stop_auto_process.py            # stop EVERY running book
    python3 stop_auto_process.py --url http://127.0.0.1:8000
"""
import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

import t9_client


def main() -> int:
    load_dotenv()  # pick up T9_PASSWORD from .env

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000",
                        help="Base URL of the admin server (default: %(default)s)")
    parser.add_argument("--password", default=os.getenv("T9_PASSWORD"),
                        help="Admin password (default: T9_PASSWORD from env/.env)")
    parser.add_argument("-b", "--book-id", type=int, default=None,
                        help="Stop only this book's run. Omitted, EVERY running "
                             "book is stopped.")
    args = parser.parse_args()

    # Mint the session cookie locally instead of POSTing /api/auth/login — see
    # t9_client. Login is rate-limited, and a stop that fails on a 429 turns the
    # "drain before sweeping" safeguard into a silent no-op.
    with t9_client.client(args.url, args.password, timeout=15) as client:
        payload = {} if args.book_id is None else {"book_id": args.book_id}
        r = client.post("/api/queue/stop-auto", json=payload)
        if r.status_code == 401:
            print("Not authenticated — set T9_PASSWORD (in .env) or pass --password.",
                  file=sys.stderr)
            return 1
        r.raise_for_status()

        body = r.json()
        status = body.get("status")
        if status == "stopping":
            stopped = body.get("stopped") or []
            which = ", ".join(f"book {b}" for b in stopped) or "the running book"
            print(f"Auto-process will stop after the current chapter ({which}).")
        elif status == "not_running":
            scope = "" if args.book_id is None else f" for book {args.book_id}"
            print(f"Auto-process is not running{scope} — nothing to stop.")
        else:
            print(f"Server responded: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
