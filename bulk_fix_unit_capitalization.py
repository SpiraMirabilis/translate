#!/usr/bin/env python3
"""Repair sentence-initial lowercase left behind by the unit converter.

When `unit_converter.py` replaces a Chinese time expression like "Half a shichen"
with "an hour" / "about an hour" / "two hours" / "twenty minutes", it emits the
replacement in lowercase regardless of the original casing. If the original
phrase began a sentence, the result is a paragraph or sentence that starts with
a lowercase letter (e.g. "an hour passed, and …").

This script scans translated chapter content and capitalises the first letter
of any sentence-start whose opening phrase looks like one of those converter
outputs. To stay conservative, we only fix sentence-starts whose first ~6 words
contain an `hour` or `minute` token — that's what the converter's `replace`
path actually emits (see units.json: shichen / double-hour / ke).

Sentence-start = start of a line, or following `.`/`!`/`?` + whitespace, or
after an opening quote/bracket that is itself preceded by whitespace.

Usage:
    python3 bulk_fix_unit_capitalization.py --book-id 23 --dry-run
    python3 bulk_fix_unit_capitalization.py --book-id 23
    python3 bulk_fix_unit_capitalization.py --all --dry-run
"""

import argparse
import re
import sys

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger


# A lowercase phrase that begins with one of the unit-converter's known output
# words (article, number-word, or "about" + same) and resolves to an hour/minute
# unit within a few words. Anchored at the candidate position via re.match.
_CONVERTER_OUTPUT_RE = re.compile(
    r"(?:about\s+)?"
    r"(?:"
    r"an?"                                                                      # a / an
    r"|half|three[-\s]quarters"                                                 # standalone fractions
    r"|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    r"|hundred|thousand"
    r")"
    r"(?:[-\s]+(?:and\s+)?(?:a\s+)?"
    r"(?:half|quarter|three-quarters"
    r"|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    r"|hundred|thousand))*"
    r"[-\s]+(?:hour|minute)s?\b",
    re.IGNORECASE,
)


def _is_sentence_start(text: str, idx: int) -> bool:
    """True if `text[idx]` begins a new sentence (paragraph start, or after `.!?` + WS).

    Deliberately conservative: we do NOT treat opening quotes/brackets as
    sentence-starts (quoted nouns and parenthetical labels produce too many
    false positives), and we reject ellipsis (`..` or `...`) which is a
    thought-pause, not a terminator.
    """
    if idx == 0:
        return True
    j = idx - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    if j < 0:
        return True
    if text[j] == "\n":
        return True
    if text[j] not in ".!?":
        return False
    # `?` / `!` are unambiguous sentence-enders.
    if text[j] != ".":
        return True
    # Reject ellipsis: dot preceded by another dot.
    if j >= 1 and text[j - 1] == ".":
        return False
    # Reject single-letter abbreviation: `p.`, `a.`, etc. (so `5 p.m., …` doesn't
    # match — though the converter-word filter would catch this too, belt-and-braces).
    if j >= 1 and text[j - 1].isalpha():
        if j == 1 or not text[j - 2].isalpha():
            return False
    return True


def _looks_like_converter_output(text: str, idx: int) -> bool:
    """Does the phrase starting at `text[idx]` match a unit-converter `replace` output?"""
    return _CONVERTER_OUTPUT_RE.match(text, idx) is not None


def fix_line(line: str) -> tuple[str, int, list[tuple[str, str]]]:
    """Return (new_line, num_fixes, [(before_ctx, after_ctx), ...])."""
    chars = list(line)
    fixes = 0
    examples: list[tuple[str, str]] = []

    for i, ch in enumerate(line):
        if not (ch.isalpha() and ch.islower()):
            continue
        if not _is_sentence_start(line, i):
            continue
        if not _looks_like_converter_output(line, i):
            continue
        chars[i] = ch.upper()
        fixes += 1
        # Capture a short before/after snippet for the dry-run log
        ctx_start = max(0, i - 20)
        ctx_end = min(len(line), i + 40)
        before = line[ctx_start:ctx_end]
        after = before[:i - ctx_start] + ch.upper() + before[i - ctx_start + 1:]
        examples.append((before, after))

    return "".join(chars), fixes, examples


def process_book(db: DatabaseManager, book_id: int, dry_run: bool) -> tuple[int, int]:
    book = db.get_book(book_id=book_id)
    if not book:
        print(f"Book {book_id} not found.", file=sys.stderr)
        return (0, 0)

    chapters = db.list_chapters(book_id) or []
    print(f"Book {book_id}: {book['title']!r} ({len(chapters)} chapters)")

    changed_chapters = 0
    total_fixes = 0

    for meta in chapters:
        ch_num = meta["chapter"]
        full = db.get_chapter(book_id=book_id, chapter_number=ch_num)
        if not full:
            continue
        content = full["content"] or []
        new_lines = []
        ch_fixes = 0
        ch_examples: list[tuple[str, str]] = []
        for line in content:
            new_line, n, examples = fix_line(line)
            new_lines.append(new_line)
            if n:
                ch_fixes += n
                # Keep the first couple of examples per chapter for log brevity
                for ex in examples:
                    if len(ch_examples) < 2:
                        ch_examples.append(ex)

        if ch_fixes == 0:
            continue

        changed_chapters += 1
        total_fixes += ch_fixes
        print(f"  Chapter {ch_num}: {ch_fixes} fix(es)")
        for before, after in ch_examples:
            print(f"     - {before.strip()}")
            print(f"     + {after.strip()}")

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

    print(f"  → {changed_chapters} chapter(s) changed, {total_fixes} fix(es).")
    return (changed_chapters, total_fixes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book-id", type=int, help="Process a single book by ID.")
    group.add_argument("--all", action="store_true", help="Process every book in the database.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing to the database.")
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
        print()

    print(f"Total chapters changed: {grand_chapters}")
    print(f"Total fixes:           {grand_fixes}")
    if args.dry_run:
        print("(Dry run — no changes written. Re-run without --dry-run to apply.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
