#!/usr/bin/env python3
"""Strip the code-owned response contract out of books' frozen prompt templates.

Every book froze a copy of a genre prompt into ``books.prompt_template`` at
creation, so the response contract those copies carry is the contract as it
stood on the book's creation day. It now lives in ``prompt_contract`` and is
appended at assembly time, which makes the stored copy redundant at best and
contradictory at worst.

``generate_system_prompt`` already strips it on every run, so this script is
about the STORED text: what a person sees and edits in the book's prompt form.
Nothing here changes what the model receives.

BOOK-SPECIFIC NOTES is never touched. A couple of books have contract wording
pasted into their notes; those are reported so a human can decide, not edited.

    python3 backfill_prompt_contract.py --all --dry-run     # read this first
    python3 backfill_prompt_contract.py --all
    python3 backfill_prompt_contract.py -b 90 --diff
"""
import argparse
import difflib
import sys

from dotenv import load_dotenv

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger
from prompt_contract import (
    CORE_PATTERN_NAMES, has_legacy_contract, legacy_in_notes,
    strip_legacy_contract,
)


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("-b", "--book-id", type=int, help="one book")
    target.add_argument("--all", action="store_true", help="every book with a template")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--diff", action="store_true",
                    help="print a unified diff per changed book")
    return ap.parse_args()


def books_to_process(db, args):
    if args.book_id:
        book = db.get_book(book_id=args.book_id)
        if not book:
            sys.exit(f"No book {args.book_id}")
        return [book]
    return db.list_books()


def main():
    load_dotenv()
    args = parse_args()
    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    changed = unchanged = skipped = 0
    notes_hits, unmatched = [], []

    for book in books_to_process(db, args):
        book_id = book["id"]
        title = (book.get("title") or f"book {book_id}")[:48]
        stored = db.get_book_prompt_template(book_id)
        if not (stored or "").strip():
            skipped += 1
            continue

        report = {}
        cleaned = strip_legacy_contract(stored, report)
        # Only a core section failing to match is worth a human's time; the
        # optional ones are absent from most books by design.
        missing = [name for name, n in report.items()
                   if n == 0 and name in CORE_PATTERN_NAMES]
        in_notes = legacy_in_notes(cleaned)
        if in_notes:
            notes_hits.append((book_id, title, in_notes))

        if cleaned == stored:
            unchanged += 1
            continue

        # A pattern that found nothing is worth surfacing: it means this book
        # words that section differently, and a human should look before
        # assuming it was cleaned.
        if missing:
            unmatched.append((book_id, title, missing))

        saved = len(stored) - len(cleaned)
        print(f"book {book_id:>3} {title:<48} -{saved} chars"
              + (f"  [no match: {', '.join(missing)}]" if missing else ""))
        if args.diff:
            for line in difflib.unified_diff(
                    stored.splitlines(), cleaned.splitlines(),
                    f"book{book_id}.before", f"book{book_id}.after", lineterm="", n=1):
                print("   " + line)

        if not args.dry_run:
            if not db.set_book_prompt_template(book_id, cleaned):
                sys.exit(f"FAILED to write book {book_id} — stopping")
        changed += 1

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb}: {changed} | already clean: {unchanged} | no template: {skipped}")

    if unmatched:
        print(f"\n{len(unmatched)} book(s) where some section did not match the "
              f"stock wording — check these by hand:")
        for book_id, title, missing in unmatched:
            print(f"  book {book_id:>3} {title:<48} {', '.join(missing)}")

    if notes_hits:
        print(f"\n{len(notes_hits)} book(s) repeat contract wording inside "
              f"BOOK-SPECIFIC NOTES. That section is yours, so it was left "
              f"alone — delete the lines yourself if you want them gone:")
        for book_id, title, names in notes_hits:
            print(f"  book {book_id:>3} {title:<48} {', '.join(names)}")

    # Nothing above should be able to leave contract wording behind.
    if not args.dry_run:
        for book in books_to_process(db, args):
            stored = db.get_book_prompt_template(book["id"])
            if stored and has_legacy_contract(stored):
                sys.exit(f"book {book['id']} still carries contract wording")
        print("\nverified: no stored prompt carries the response contract")


if __name__ == "__main__":
    main()
