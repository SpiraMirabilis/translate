#!/usr/bin/env python3
"""
Report chapter-numbering gaps and duplicates for a book, across both the
translated chapters (`chapters` table) and the not-yet-translated `queue`.

Usage:
    python chapter_gaps.py -b 77               # one book
    python chapter_gaps.py -b 77 -b 90         # several
    python chapter_gaps.py --all               # every book with chapters or queue rows
    python chapter_gaps.py --all --quiet       # only books with problems
    python chapter_gaps.py -b 77 --json        # machine-readable

What is checked:
  * GAPS in the combined sequence (translated ∪ queued), from chapter 1 (or
    --start N) up to the highest number present. Each missing run is printed
    once, e.g. `ch 212-214 (3)`.
  * DUPLICATES inside the queue — two queue rows claiming the same chapter
    number. (`chapters` is UNIQUE on (book_id, chapter_number), so it cannot
    hold duplicates itself.)
  * QUEUED OVER TRANSLATED — a queue row whose number is already translated.
    That is how a retranslation looks, so it is reported separately and does
    not fail the check unless --strict. Rows carrying a retranslation_reason
    are marked as such.
  * UNNUMBERED queue rows (chapter_number NULL) — they will be numbered at
    translation time, so they can hide or fill a gap; listed so you can tell.

Exit status: 0 = clean, 1 = gaps or duplicates found (or overlaps with
--strict), 2 = usage error / unknown book.

Read-only: this script never writes.
"""

import argparse
import json
import os
import sys
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger


def collapse_runs(numbers):
    """[1,2,3,7,9,10] -> [(1,3),(7,7),(9,10)]"""
    runs = []
    for n in sorted(numbers):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return [tuple(r) for r in runs]


def format_run(lo, hi):
    return f"ch {lo}" if lo == hi else f"ch {lo}-{hi} ({hi - lo + 1})"


def fetch_book_ids(db_manager):
    with db_manager._conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT book_id FROM chapters "
            "UNION SELECT DISTINCT book_id FROM queue"
        )
        return sorted(row[0] for row in cursor.fetchall())


def fetch_numbers(db_manager, book_id):
    """Return (translated_numbers, queue_rows) for one book.

    queue_rows: list of dicts {id, chapter_number, title, position, status,
    retranslation_reason}.
    """
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT chapter_number FROM chapters WHERE book_id = ?",
            (book_id,),
        )
        translated = [row["chapter_number"] for row in cursor.fetchall()]
        cursor.execute(
            "SELECT id, chapter_number, title, position, status, "
            "retranslation_reason FROM queue WHERE book_id = ? "
            "ORDER BY position",
            (book_id,),
        )
        queue_rows = [dict(row) for row in cursor.fetchall()]
    return translated, queue_rows


def analyse(book_id, title, translated, queue_rows, start):
    translated_set = set(translated)

    by_number = defaultdict(list)
    unnumbered = []
    for row in queue_rows:
        if row["chapter_number"] is None:
            unnumbered.append(row)
        else:
            by_number[row["chapter_number"]].append(row)

    queue_dups = {n: rows for n, rows in by_number.items() if len(rows) > 1}
    overlaps = {n: rows for n, rows in by_number.items() if n in translated_set}

    combined = translated_set | set(by_number)
    gaps = []
    if combined:
        lo = start if start is not None else min(1, min(combined))
        hi = max(combined)
        gaps = collapse_runs(n for n in range(lo, hi + 1) if n not in combined)

    return {
        "book_id": book_id,
        "title": title,
        "translated_count": len(translated_set),
        "translated_range": [min(translated_set), max(translated_set)] if translated_set else None,
        "queued_count": len(queue_rows),
        "queued_range": [min(by_number), max(by_number)] if by_number else None,
        "gaps": [{"from": a, "to": b, "count": b - a + 1} for a, b in gaps],
        "missing_total": sum(b - a + 1 for a, b in gaps),
        "queue_duplicates": [
            {"chapter_number": n,
             "rows": [{"queue_id": r["id"], "position": r["position"],
                       "title": r["title"], "status": r["status"]} for r in rows]}
            for n, rows in sorted(queue_dups.items())
        ],
        "queued_over_translated": [
            {"chapter_number": n,
             "rows": [{"queue_id": r["id"], "position": r["position"],
                       "title": r["title"],
                       "retranslation_reason": r["retranslation_reason"]} for r in rows]}
            for n, rows in sorted(overlaps.items())
        ],
        "unnumbered_queue": [
            {"queue_id": r["id"], "position": r["position"], "title": r["title"]}
            for r in unnumbered
        ],
    }


def has_problems(report, strict):
    if report["gaps"] or report["queue_duplicates"]:
        return True
    return strict and bool(report["queued_over_translated"])


def print_report(report, strict):
    def rng(r):
        return f"{r[0]}-{r[1]}" if r else "none"

    print(f"Book {report['book_id']}: {report['title']}")
    print(f"  translated: {report['translated_count']} (ch {rng(report['translated_range'])})"
          f"   queued: {report['queued_count']} (ch {rng(report['queued_range'])})")

    if report["gaps"]:
        print(f"  GAPS — {report['missing_total']} chapter(s) missing:")
        for g in report["gaps"]:
            print(f"    {format_run(g['from'], g['to'])}")
    if report["queue_duplicates"]:
        print(f"  DUPLICATES in queue — {len(report['queue_duplicates'])} chapter number(s):")
        for d in report["queue_duplicates"]:
            print(f"    ch {d['chapter_number']}:")
            for r in d["rows"]:
                print(f"      queue id {r['queue_id']} pos {r['position']} "
                      f"[{r['status']}] {r['title']}")
    if report["queued_over_translated"]:
        label = "QUEUED OVER TRANSLATED" if strict else "queued over translated (retranslations?)"
        print(f"  {label} — {len(report['queued_over_translated'])} chapter(s):")
        for o in report["queued_over_translated"]:
            for r in o["rows"]:
                reason = r["retranslation_reason"]
                tag = f" — reason: {reason}" if reason else " — no retranslation_reason"
                print(f"    ch {o['chapter_number']}: queue id {r['queue_id']} "
                      f"pos {r['position']}{tag}")
    if report["unnumbered_queue"]:
        print(f"  unnumbered queue rows — {len(report['unnumbered_queue'])}:")
        for r in report["unnumbered_queue"]:
            print(f"    queue id {r['queue_id']} pos {r['position']}: {r['title']}")

    if not has_problems(report, strict):
        print("  OK — no gaps or duplicates.")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Report chapter-number gaps and duplicates in a book "
                    "(translated chapters and queue).")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("-b", "--book-id", type=int, action="append",
                        help="Book ID (repeatable).")
    target.add_argument("--all", action="store_true",
                        help="Check every book that has chapters or queue rows.")
    parser.add_argument("--start", type=int, default=None,
                        help="First expected chapter number (default: 1, or "
                             "lower if the book has a ch 0 / negative prologue).")
    parser.add_argument("--strict", action="store_true",
                        help="Also fail on queue rows that duplicate an "
                             "already-translated chapter.")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Only print books with problems.")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    book_ids = fetch_book_ids(db_manager) if args.all else args.book_id

    reports = []
    for book_id in book_ids:
        book = db_manager.get_book(book_id=book_id)
        if not book:
            print(f"Book {book_id} not found.", file=sys.stderr)
            return 2
        translated, queue_rows = fetch_numbers(db_manager, book_id)
        reports.append(analyse(book_id, book.get("title"), translated,
                               queue_rows, args.start))

    failing = [r for r in reports if has_problems(r, args.strict)]
    shown = failing if args.quiet else reports

    if args.json:
        print(json.dumps(shown, ensure_ascii=False, indent=2))
    else:
        for report in shown:
            print_report(report, args.strict)
        if len(reports) > 1:
            print(f"{len(failing)} of {len(reports)} book(s) with problems.")

    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
