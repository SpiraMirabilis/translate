#!/usr/bin/env python3
"""Migrate a book's existing INLINE footnotes into the footnotes table.

Older footnotes live only as inline "[n]" markers + a "[n] body" definition block
in chapters.translated_content, so a retranslation destroys them. This one-off
script parses those inline footnotes and records each one in the footnotes table
(the new source of truth) so the save-time reapply hook can re-anchor them on every
future save.

For each "[n]" marker it derives the English ANCHOR (the term the marker hugs) by
cross-checking two candidates:
  1. the leading term of the definition body — the project convention is
     "[n] <Term> (汉字): <gloss>", so the run before " (" or ":" is the term (and the
     parenthetical CJK becomes source_term);
  2. the word(s) immediately before the marker in the prose.
If candidate 1 ends exactly at the marker it is used (high confidence). Otherwise the
hugged prose token is used (medium). If neither can be derived the row is stored with
status 'orphaned' (a best guess) rather than dropped, and surfaced by
`list_footnotes.py --orphans`.

The recorded `occurrence` is computed from the marker's actual position, so an
immediate `rerender_chapter_footnotes` reproduces the existing inline text exactly
(for anchors that resolve).

Usage:
    python3 backfill_footnotes.py --book-id 56 --dry-run
    python3 backfill_footnotes.py --book-id 56
"""

import argparse
import re

from config import TranslationConfig
from db import DatabaseManager
from footnotes import (MARKER_RE, content_to_list, occurrence_at,
                       split_prose_and_defs)
from logger import Logger

CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


def derive_from_body(body):
    """Return (term, source_term) parsed from a definition body, or (None, None).

    "Calabash Brothers (葫芦兄弟): a 1980s ..." -> ("Calabash Brothers", "葫芦兄弟")
    "Telum Odii: the incantation for ..."      -> ("Telum Odii", None)
    """
    m = re.match(r"\s*(.+?)\s*(?:\(([^)]*)\)|[:：])", body)
    if not m:
        return None, None
    term = m.group(1).strip()
    paren = (m.group(2) or "").strip()
    source_term = paren if paren and CJK_RE.search(paren) else None
    return (term or None), source_term


def hugged_token(text_before):
    """The last whitespace-delimited chunk before the marker (single-word fallback)."""
    m = re.search(r"(\S+)$", text_before.rstrip())
    return m.group(1) if m else None


def plan_chapter(chapter):
    """Return a list of footnote dicts to persist for one chapter.

    Each dict: {anchor, source_term, body, occurrence, status, confidence}.
    """
    content = content_to_list(chapter.get("content"))
    prose_lines, defs = split_prose_and_defs(content)
    if not defs:
        return []
    stripped = [MARKER_RE.sub("", ln) for ln in prose_lines]

    rows = []
    seen_numbers = set()
    for li, line in enumerate(prose_lines):
        for m in MARKER_RE.finditer(line):
            n = int(m.group(1))
            if n in seen_numbers:
                continue  # only the first marker for a given number
            seen_numbers.add(n)
            if n not in defs:
                continue  # marker with no definition — nothing to store
            body = defs[n]
            body_term, source_term = derive_from_body(body)
            # Text on this line before the marker, with any earlier markers removed
            # (stripped coordinates, so it lines up with what the renderer sees).
            text_before = MARKER_RE.sub("", line[:m.start()])
            col_end = len(text_before)

            if body_term and text_before.endswith(body_term):
                anchor, status, confidence = body_term, "active", "high"
            else:
                tok = hugged_token(text_before)
                if tok:
                    anchor, status, confidence = tok, "active", "medium"
                else:
                    anchor, status, confidence = (body_term or ""), "orphaned", "low"

            occurrence = (occurrence_at(stripped, anchor, li, col_end)
                          if status == "active" and anchor else 1)
            rows.append({
                "anchor": anchor, "source_term": source_term, "body": body,
                "occurrence": occurrence, "status": status, "confidence": confidence,
                "number": n,
            })

    # Definitions whose inline marker is missing — store as orphaned, never drop.
    for n in sorted(defs):
        if n in seen_numbers:
            continue
        body = defs[n]
        body_term, source_term = derive_from_body(body)
        rows.append({
            "anchor": (body_term or ""), "source_term": source_term, "body": body,
            "occurrence": 1, "status": "orphaned", "confidence": "low (no marker)",
            "number": n,
        })
    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Backfill existing inline footnotes into the footnotes table.")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show derived anchors + confidence without writing.")
    args = parser.parse_args()

    config = TranslationConfig()
    # strict_writes: a failed footnote insert / re-render raises instead of being swallowed.
    db = DatabaseManager(config, Logger(config), strict_writes=True)

    book = db.get_book(book_id=args.book_id)
    if not book:
        print(f"Book {args.book_id} not found.")
        return

    chapters = db.list_chapters(args.book_id)
    print(f"Book ID:   {args.book_id}")
    print(f"Chapters:  {len(chapters)}")
    print(f"Mode:      {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 70)

    total = 0
    orphaned = 0
    touched = 0
    for meta in chapters:
        chapter = db.get_chapter(chapter_id=meta["id"])
        if not chapter:
            continue
        rows = plan_chapter(chapter)
        if not rows:
            continue
        touched += 1
        n_orphan = sum(1 for r in rows if r["status"] == "orphaned")
        total += len(rows)
        orphaned += n_orphan
        print(f"ch{chapter['chapter']}: {len(rows)} footnote(s)"
              + (f", {n_orphan} orphaned" if n_orphan else ""))
        for r in rows:
            print(f"    [{r['number']}] ({r['confidence']}) "
                  f"anchor={r['anchor']!r}"
                  + (f" src={r['source_term']!r}" if r["source_term"] else "")
                  + (f"  occ={r['occurrence']}" if r["occurrence"] != 1 else ""))
        if not args.dry_run:
            for r in rows:
                db.add_footnote(
                    args.book_id, meta["id"], r["anchor"], r["body"],
                    source_term=r["source_term"], occurrence=r["occurrence"],
                    is_source=0, status=r["status"],
                )
            db.rerender_chapter_footnotes(meta["id"])

    print("=" * 70)
    print(f"Footnotes: {total} across {touched} chapter(s)"
          + (f"  ({orphaned} orphaned — see list_footnotes.py --orphans)" if orphaned else ""))
    if args.dry_run:
        print("(dry run — nothing written)")


if __name__ == "__main__":
    main()
