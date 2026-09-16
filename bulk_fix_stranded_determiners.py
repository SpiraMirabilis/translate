#!/usr/bin/env python3
"""Repair determiners stranded in front of a converted time unit.

`unit_converter.py` used to replace only the unit phrase, leaving the emphasis
determiner that preceded it to collide with the replacement's own article:

    "a full half-shichen"   -> "a full an hour"          (should be "a full hour")
    "another half-shichen"  -> "another an hour"          -> "another hour"
    "the half-shichen"      -> "the an hour"              -> "the hour"
    "another half-ke"       -> "another about ten minutes" -> "about another ten minutes"

The converter no longer produces these (it consumes the determiner into the
match and re-places it), but text translated before that fix is still stored
with the collision. The source unit is gone from those lines, so re-running the
converter cannot repair them — this script rewrites the damaged shapes directly.

Only the six forms actually present in the corpus are rewritten; anything else
is left for a human. Casing of the leading word is preserved.

Usage:
    python3 bulk_fix_stranded_determiners.py --all --dry-run
    python3 bulk_fix_stranded_determiners.py --all
    python3 bulk_fix_stranded_determiners.py --book-id 23
"""

import argparse
import re
import sys

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger


_DET = r"(?:a|an|the|another)"
_ADJ = r"(?:full|whole|entire|good|solid|mere|complete)"

# "a full an hour" / "another an hour" / "the an hour": the replacement brought
# its own article, so drop it and keep the determiner (+ emphasis word).
_DOUBLED_ARTICLE = re.compile(
    r"\b(?P<det>" + _DET + r")(?P<adj>\s+" + _ADJ + r")?\s+an\s+(?P<unit>hour)\b",
    re.IGNORECASE,
)

# "another about ten minutes": the rounding hedge landed behind the determiner.
# It belongs in front of the whole phrase.
_TRAILING_HEDGE = re.compile(
    r"\b(?P<det>" + _DET + r")(?P<adj>\s+" + _ADJ + r")?\s+about\s+"
    r"(?P<rest>(?:\w+[\s-]+){0,3}?(?:hour|minute)s?)\b",
    re.IGNORECASE,
)


def _match_case(template: str, text: str) -> str:
    """Mirror the leading casing of `template` onto `text`."""
    if not text or not template:
        return text
    if len(template) > 1 and template.isupper():
        return text.upper()
    if template[:1].isupper():
        return text[:1].upper() + text[1:]
    return text


def _fix_doubled_article(match: re.Match) -> str:
    det = match.group("det")
    adj = (match.group("adj") or "").strip()
    unit = match.group("unit")
    body = f"{det.lower()} {adj.lower()} {unit}" if adj else f"{det.lower()} {unit}"
    return _match_case(match.group(0), body)


def _fix_trailing_hedge(match: re.Match) -> str:
    det = match.group("det")
    adj = (match.group("adj") or "").strip()
    rest = match.group("rest")
    body = f"about {det.lower()} {adj.lower()} {rest}" if adj else f"about {det.lower()} {rest}"
    return _match_case(match.group(0), body)


def fix_line(line: str) -> tuple:
    """Return (new_line, num_fixes)."""
    out, n1 = _DOUBLED_ARTICLE.subn(_fix_doubled_article, line)
    out, n2 = _TRAILING_HEDGE.subn(_fix_trailing_hedge, out)
    return out, n1 + n2


def process_book(db: DatabaseManager, book_id: int, dry_run: bool) -> tuple:
    book = db.get_book(book_id=book_id)
    if not book:
        print(f"Book {book_id} not found.", file=sys.stderr)
        return (0, 0)

    changed_chapters = 0
    total_fixes = 0
    header_shown = False

    for meta in (db.list_chapters(book_id) or []):
        ch_num = meta["chapter"]
        full = db.get_chapter(book_id=book_id, chapter_number=ch_num)
        if not full:
            continue
        content = full["content"] or []
        new_lines = []
        ch_fixes = 0
        for line in content:
            new_line, n = fix_line(line)
            new_lines.append(new_line)
            if n:
                ch_fixes += n
                print(f"    - {line.strip()[:150]}")
                print(f"    + {new_line.strip()[:150]}")

        if ch_fixes == 0:
            continue

        if not header_shown:
            print(f"Book {book_id}: {book['title']!r}")
            header_shown = True
        changed_chapters += 1
        total_fixes += ch_fixes
        print(f"  Chapter {ch_num}: {ch_fixes} fix(es)")

        if not dry_run:
            db.save_chapter(
                book_id,
                ch_num,
                full["title"],
                full["untranslated"],
                new_lines,
                summary=full.get("summary"),
                translation_model=full.get("model"),
            )

    return (changed_chapters, total_fixes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book-id", type=int, help="Process a single book by ID.")
    group.add_argument("--all", action="store_true", help="Process every book.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing.")
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger)

    print(f"Mode: {'DRY RUN' if args.dry_run else 'WRITE'}\n")

    if args.all:
        book_ids = [b["id"] for b in (db.list_books() or [])]
    else:
        book_ids = [args.book_id]

    grand_chapters = 0
    grand_fixes = 0
    for bid in book_ids:
        c, f = process_book(db, bid, args.dry_run)
        grand_chapters += c
        grand_fixes += f

    print(f"\nTotal: {grand_chapters} chapter(s), {grand_fixes} fix(es).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
