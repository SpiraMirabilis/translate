#!/usr/bin/env python3
"""Restart auto-processing of the translation queue.

CLI counterpart of stop_auto_process.py, and of the Queue page's "Process
queue" button: claims the next queued item for a book and keeps pulling
further items until the queue is empty (or --max-chapters is reached).

The server does not remember the run options of a job that has stopped, so
they have to be given again here. The defaults match the Queue page's own
defaults (entity review ON, single pass, cleaning ON, streaming ON) — pass
the flags your run actually uses.

Usage:
    python3 start_auto_process.py -b 93 --no-review
    python3 start_auto_process.py -b 93 --no-review --max-chapters 20
    python3 start_auto_process.py --all --no-review     # one worker per queued book
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
                        help="Book to process. Omitted = the earliest-queued book "
                             "that isn't already running.")
    parser.add_argument("--all", action="store_true",
                        help="Start one worker per queued book, up to the "
                             "max_concurrent_translations cap.")
    parser.add_argument("--max-chapters", type=int, default=None,
                        help="Stop after N chapters (default: unlimited).")

    parser.add_argument("--model", dest="translation_model", default=None,
                        help="Translation model override, e.g. deepseek:deepseek-v4-pro")
    parser.add_argument("--advice-model", default=None)
    parser.add_argument("--cleaning-model", default=None)

    parser.add_argument("--no-review", action="store_true",
                        help="Skip the interactive entity-review handshake.")
    parser.add_argument("--two-pass", action="store_true",
                        help="Entity-extraction pass, then translation pass.")
    parser.add_argument("--no-clean", action="store_true")
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--save-as-draft", action="store_true",
                        help="Save new chapters unpublished.")

    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Print the request that would be sent and exit.")
    args = parser.parse_args()

    if args.all and args.book_id is not None:
        parser.error("--all processes every queued book; drop -b/--book-id.")
    # two_pass is mutually exclusive with no_review server-side; say so here
    # rather than letting the backend silently drop it.
    if args.two_pass and args.no_review:
        parser.error("--two-pass and --no-review are mutually exclusive.")

    payload = {
        "translation_model": args.translation_model,
        "advice_model": args.advice_model,
        "cleaning_model": args.cleaning_model,
        "no_review": args.no_review,
        "two_pass": args.two_pass,
        "no_clean": args.no_clean,
        "no_stream": args.no_stream,
        "save_as_draft": args.save_as_draft,
        "auto_process": True,
        "max_chapters": args.max_chapters,
    }
    if not args.all:
        payload["book_id"] = args.book_id

    endpoint = "/api/queue/process-all" if args.all else "/api/queue/process-next"

    if args.dry_run:
        import json
        print(f"POST {endpoint}")
        print(json.dumps(payload, indent=2))
        return 0

    # Mint the session cookie locally instead of POSTing /api/auth/login — see
    # t9_client. Login is rate-limited 5/min and 20/hour per IP.
    with t9_client.client(args.url, args.password, timeout=30) as client:
        r = client.post(endpoint, json=payload)
        if r.status_code == 401:
            print("Not authenticated — set T9_PASSWORD (in .env) or pass --password.",
                  file=sys.stderr)
            return 1
        if r.status_code == 404:
            print("Nothing to do — the queue is empty.", file=sys.stderr)
            return 1
        if r.status_code == 409:
            # Already running, or over the concurrency cap.
            print(f"Refused: {r.json().get('detail', r.text)}", file=sys.stderr)
            return 1
        r.raise_for_status()

        data = r.json()
        if args.all:
            started = data.get("started", [])
            for entry in started:
                item = entry.get("item") or {}
                print(f"Started book {entry['book_id']} — chapter "
                      f"{item.get('chapter_number', '?')}")
            for entry in data.get("skipped", []):
                print(f"Skipped book {entry['book_id']}: {entry['reason']}")
            if not started:
                print("Nothing started.")
        else:
            item = data.get("item") or {}
            print(f"Auto-process started on book {item.get('book_id', args.book_id)} "
                  f"— chapter {item.get('chapter_number', '?')}"
                  f"{f', {args.max_chapters} chapter(s) max' if args.max_chapters else ''}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
