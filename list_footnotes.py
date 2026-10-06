#!/usr/bin/env python3
"""List every footnote in a book's translated chapters.

For each footnote it prints the chapter it lives in, the footnote number and
its definition body, and the sentence in the prose that the marker is attached
to (so you can see the footnote in context).

Footnote convention (matches add_footnotes.py / the project's manual style):
  - inline marker hugs the term:   "...the Calabash Brothers[3] rushing in..."
  - definition at the bottom:       [3] Calabash Brothers (葫芦兄弟): a 1980s ...

Usage:
    python3 list_footnotes.py --book-id 56
    python3 list_footnotes.py --book-id 56 --chapters 5,7,9
    python3 list_footnotes.py --book-id 56 --chapters 1-20
    python3 list_footnotes.py --book-id 56 --chapters '>20'
    python3 list_footnotes.py --book-id 56 --format json
"""

import argparse
import json
import re
import sys

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

# A definition line at the bottom of a chapter: "[n] body text".
DEF_RE = re.compile(r"^\[(\d+)\]\s?(.*)$")
# An inline marker "[n]" anywhere in prose.
MARKER_RE = re.compile(r"\[(\d+)\]")


def parse_chapter_filter(raw):
    """Parse the --chapters argument into a predicate fn(chapter_number)->bool.

    Returns None if `raw` is empty (no filtering). Supports a comma-separated
    list whose pieces may be:
      - a single number:        5
      - an inclusive range:     1-20
      - a comparison:           >20, >=20, <20, <=20, =20
    A chapter matches if it satisfies any piece (pieces are OR-ed together).
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    predicates = []
    for piece in raw.split(","):
        s = piece.strip()
        if not s:
            continue
        try:
            if s.startswith(">="):
                n = int(s[2:]); predicates.append(lambda c, n=n: c >= n)
            elif s.startswith("<="):
                n = int(s[2:]); predicates.append(lambda c, n=n: c <= n)
            elif s.startswith(">"):
                n = int(s[1:]); predicates.append(lambda c, n=n: c > n)
            elif s.startswith("<"):
                n = int(s[1:]); predicates.append(lambda c, n=n: c < n)
            elif s.startswith("="):
                n = int(s[1:]); predicates.append(lambda c, n=n: c == n)
            elif "-" in s:
                lo_s, hi_s = s.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
                if lo > hi:
                    lo, hi = hi, lo
                predicates.append(lambda c, lo=lo, hi=hi: lo <= c <= hi)
            else:
                n = int(s); predicates.append(lambda c, n=n: c == n)
        except ValueError:
            print(f"error: invalid --chapters value {s!r}", file=sys.stderr)
            sys.exit(1)

    if not predicates:
        return None
    return lambda c: any(p(c) for p in predicates)


def split_prose_and_defs(lines):
    """Return (prose_lines, defs) where defs maps footnote number -> body.

    The trailing definition block is the run of blank / "[n] body" lines at the
    very end of the chapter (same rule add_footnotes.py uses)."""
    i = len(lines) - 1
    while i >= 0 and (lines[i].strip() == "" or DEF_RE.match(lines[i])):
        i -= 1
    prose_end = i + 1

    defs = {}
    for line in lines[prose_end:]:
        m = DEF_RE.match(line)
        if m:
            defs[int(m.group(1))] = m.group(2).strip()
    return lines[:prose_end], defs


def sentence_for_marker(prose_text, marker_start, marker_end):
    """Extract the sentence that contains the marker at [marker_start:marker_end]."""
    # Walk backward to the start of the sentence.
    start = marker_start
    while start > 0 and prose_text[start - 1] not in ".!?\n":
        start -= 1
    # Walk forward past the marker to the sentence's end punctuation.
    end = marker_end
    while end < len(prose_text) and prose_text[end] not in ".!?\n":
        end += 1
    # Include the closing punctuation and any trailing quote/bracket.
    while end < len(prose_text) and prose_text[end] in ".!?\"'’”)]":
        end += 1
    return prose_text[start:end].strip()


def collect_footnotes(chapter):
    """Return a list of footnote dicts for one chapter."""
    content = chapter.get("content") or []
    if isinstance(content, str):
        content = content.split("\n")
    prose_lines, defs = split_prose_and_defs(content)
    if not defs:
        return []

    prose_text = "\n".join(prose_lines)

    # Map each footnote number to the sentence of its inline marker.
    sentences = {}
    for m in MARKER_RE.finditer(prose_text):
        num = int(m.group(1))
        if num in sentences:
            continue  # keep first occurrence of the marker
        sentences[num] = sentence_for_marker(prose_text, m.start(), m.end())

    results = []
    for num in sorted(defs):
        results.append({
            "chapter": chapter.get("chapter"),
            "chapter_title": chapter.get("title"),
            "number": num,
            "body": defs[num],
            "sentence": sentences.get(num),  # None if marker missing in prose
        })
    return results


def list_orphans(db, book_id, fmt):
    """Print footnotes whose anchor was not found at the last render."""
    orphans = db.get_book_footnotes(book_id, status="orphaned")
    if fmt == "json":
        print(json.dumps(orphans, ensure_ascii=False, indent=2))
        return
    print(f"Orphaned footnotes in book {book_id}")
    print("=" * 70)
    if not orphans:
        print("None — every footnote is anchored.")
        return
    for fn in orphans:
        where = "source" if fn.get("is_source") else "translated"
        print(f"\n[id {fn['id']}] ch{fn['chapter_number']} ({where})")
        print(f"  anchor: {fn['anchor']!r}"
              + (f"   source_term: {fn['source_term']!r}" if fn.get("source_term") else ""))
        print(f"  body:   {fn['body']}")
    print(f"\nTotal: {len(orphans)} orphaned footnote(s)")
    print("Fix with:  python3 list_footnotes.py --book-id "
          f"{book_id} --reanchor <id> --anchor \"<term as it now appears>\"")


def reanchor_footnote(db, book_id, footnote_id, new_anchor):
    """Point an orphaned footnote at a new anchor term and re-render its chapter.

    Returns a dict:
      ok          True when the anchor was updated and the chapter re-rendered
      footnote_id the id asked for
      chapter     the footnote's chapter number (None if not found)
      old_anchor  anchor before the change (None if not found)
      new_anchor  the anchor asked for
      status      the row's status after re-render ('active' | 'orphaned'),
                  None when nothing was changed
      error       message when ok is False, else None
    """
    result = {"ok": False, "footnote_id": footnote_id, "chapter": None,
              "old_anchor": None, "new_anchor": new_anchor, "status": None,
              "error": None}
    if not new_anchor:
        result["error"] = "--reanchor requires --anchor \"<new term>\"."
        return result
    rows = db.get_book_footnotes(book_id)
    row = next((r for r in rows if r["id"] == footnote_id), None)
    if not row:
        result["error"] = f"Footnote {footnote_id} not found in book {book_id}."
        return result
    result["chapter"] = row["chapter_number"]
    result["old_anchor"] = row.get("anchor")
    if not db.update_footnote(footnote_id, anchor=new_anchor):
        result["error"] = f"Failed to update footnote {footnote_id}."
        return result
    if not db.rerender_chapter_footnotes(row["chapter_id"]):
        result["error"] = (f"Footnote {footnote_id} updated but ch"
                           f"{row['chapter_number']} failed to re-render.")
        return result
    updated = next((r for r in db.get_book_footnotes(book_id) if r["id"] == footnote_id), None)
    result["status"] = updated["status"] if updated else "?"
    result["ok"] = True
    return result


def print_reanchor_result(res):
    """CLI rendering of a reanchor_footnote() result."""
    if not res["ok"]:
        print(res["error"])
        return
    print(f"Footnote {res['footnote_id']} re-anchored to {res['new_anchor']!r} "
          f"(ch{res['chapter']}) — status now '{res['status']}'.")
    if res["status"] == "orphaned":
        print("  Still orphaned: the new term wasn't found in the chapter either.")


def main():
    parser = argparse.ArgumentParser(description="List all footnotes in a book.")
    parser.add_argument("--book-id", type=int, required=True, help="Book ID")
    parser.add_argument("--chapters", default=None,
                        help="Restrict to specific chapters. Accepts a "
                             "comma-separated list of numbers, inclusive ranges "
                             "(1-20), and comparisons (>20, <=20). Default: all.")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--orphans", action="store_true",
                        help="List only orphaned footnotes (anchor not found after a "
                             "retranslation) from the footnotes table.")
    parser.add_argument("--reanchor", type=int, metavar="FOOTNOTE_ID",
                        help="Re-anchor an orphaned footnote to a new term (use with "
                             "--anchor) and re-render its chapter.")
    parser.add_argument("--anchor", help="New anchor term for --reanchor.")
    args = parser.parse_args()

    config = TranslationConfig()
    # strict_writes: the --reanchor write path fails loudly instead of silently.
    db = DatabaseManager(config, Logger(config), strict_writes=True)

    book = db.get_book(book_id=args.book_id)
    if not book:
        print(f"Book {args.book_id} not found.")
        return

    if args.reanchor is not None:
        print_reanchor_result(
            reanchor_footnote(db, args.book_id, args.reanchor, args.anchor))
        return

    if args.orphans:
        list_orphans(db, args.book_id, args.format)
        return

    chapter_filter = parse_chapter_filter(args.chapters)
    chapters = db.list_chapters(args.book_id)
    if chapter_filter is not None:
        chapters = [c for c in chapters if chapter_filter(c["chapter"])]

    all_footnotes = []
    for meta in chapters:
        chapter = db.get_chapter(chapter_id=meta["id"])
        if not chapter:
            continue
        all_footnotes.extend(collect_footnotes(chapter))

    if args.format == "json":
        print(json.dumps(all_footnotes, ensure_ascii=False, indent=2))
        return

    title = book.get("title")
    print(f"Footnotes in book {args.book_id}" + (f" — {title}" if title else ""))
    print("=" * 70)
    if not all_footnotes:
        print("No footnotes found.")
        return

    current_chapter = None
    for fn in all_footnotes:
        if fn["chapter"] != current_chapter:
            current_chapter = fn["chapter"]
            ct = fn["chapter_title"] or ""
            print(f"\nChapter {current_chapter}{(': ' + ct) if ct else ''}")
            print("-" * 70)
        print(f"  [{fn['number']}] {fn['body']}")
        if fn["sentence"]:
            print(f"      ↳ {fn['sentence']}")
        else:
            print("      ↳ (marker not found in prose)")

    print(f"\nTotal: {len(all_footnotes)} footnote(s)")


if __name__ == "__main__":
    main()
