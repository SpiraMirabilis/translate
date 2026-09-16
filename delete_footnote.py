#!/usr/bin/env python3
"""Delete footnote(s) from a book's chapter and re-render the prose.

The companion removal tool to add_footnotes.py. The `footnotes` table is the
source of truth; the inline "[n]" marker plus the trailing "[n] body" definition
block are a derived rendering. This script deletes the table row(s) AND rebuilds
the chapter's rendered footnotes so the marker + definition disappear and the
remaining footnotes renumber 1..N in reading order.

Why it can't just call db.rerender_chapter_footnotes: that helper skips a side
with zero remaining rows (`if not rows: continue`), which would leave an orphaned
"[n]" marker + definition behind when you delete a chapter's LAST footnote. This
script re-renders unconditionally for any side that had footnotes, so the clean
prose is written back even when nothing remains.

Selection (all scoped to one --chapter for safety; pick ONE selector):
  --number M        the rendered [M] footnote in that chapter (resolved via its
                    definition body, then matched back to the table row)
  --anchor "term"   exact anchor match
  --body-contains S case-insensitive substring of the definition body
  --id ID           the footnotes table row id (from list_footnotes --orphans or
                    --format json on get_book_footnotes)
  --all             every footnote in the chapter

By default footnotes live in the TRANSLATED text; pass --source to target
source-side footnotes instead.

Dry-run by default — pass --apply to actually delete (mirrors delete_entity.py).

Usage:
    python3 delete_footnote.py --book-id 14 --chapter 37 --number 1
    python3 delete_footnote.py --book-id 14 --chapter 37 --body-contains "Hu Hansan"
    python3 delete_footnote.py --book-id 14 --chapter 37 --anchor "Hu Hansan" --apply
"""

import argparse
import json

from config import TranslationConfig
from db import DatabaseManager
from footnotes import content_to_list, render_footnotes, split_prose_and_defs
from logger import Logger


def resolve_targets(rows, chapter, args, is_source):
    """Return the subset of `rows` selected by the CLI selectors (or [] / error)."""
    if args.all:
        return list(rows)
    if args.id is not None:
        return [r for r in rows if r["id"] == args.id]
    if args.anchor is not None:
        return [r for r in rows if (r.get("anchor") or "") == args.anchor]
    if args.body_contains is not None:
        needle = args.body_contains.lower()
        return [r for r in rows if needle in (r.get("body") or "").lower()]
    if args.number is not None:
        # The rendered number is a content notion; map it back to a table row via
        # the definition body it renders to.
        key = "untranslated" if is_source else "content"
        _prose, defs = split_prose_and_defs(content_to_list(chapter.get(key)))
        body = defs.get(args.number)
        if body is None:
            return []
        return [r for r in rows if (r.get("body") or "").strip() == body.strip()]
    return []


def rerender_side(db, chapter_id, book_id, is_source):
    """Rebuild a chapter's rendered footnotes for one side (translated/source),
    stripping markers/defs for deleted rows even when NONE remain."""
    chapter = db.get_chapter(chapter_id=chapter_id)
    key = "untranslated" if is_source else "content"
    column = "untranslated_content" if is_source else "translated_content"
    base = content_to_list(chapter.get(key))
    remaining = db.get_chapter_footnotes(chapter_id, is_source=is_source)
    new_lines, _orphans = render_footnotes(base, remaining)
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE chapters SET {column} = ? WHERE id = ?",
            (json.dumps(new_lines, ensure_ascii=False), chapter_id),
        )
    db.invalidate_epub_cache(book_id)


def main():
    parser = argparse.ArgumentParser(
        description="Delete footnote(s) from a chapter and re-render the prose.")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--chapter", type=int, required=True,
                        help="Chapter number to operate on (scope safety).")
    sel = parser.add_mutually_exclusive_group(required=True)
    sel.add_argument("--number", type=int, help="Rendered [n] footnote number.")
    sel.add_argument("--anchor", help="Exact anchor term.")
    sel.add_argument("--body-contains", dest="body_contains",
                     help="Case-insensitive substring of the definition body.")
    sel.add_argument("--id", type=int, help="footnotes table row id.")
    sel.add_argument("--all", action="store_true",
                     help="Delete every footnote in the chapter.")
    parser.add_argument("--source", action="store_true",
                        help="Target SOURCE-side footnotes instead of translated.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete (default is a dry run).")
    args = parser.parse_args()

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config), strict_writes=True)

    chapter_rows = db.list_chapters(args.book_id)
    chapter_id_by_num = {c["chapter"]: c["id"] for c in chapter_rows}
    chapter_id = chapter_id_by_num.get(args.chapter)
    if chapter_id is None:
        print(f"Chapter {args.chapter} is not a chapter of book {args.book_id}.")
        return

    is_source = 1 if args.source else 0
    chapter = db.get_chapter(chapter_id=chapter_id)
    rows = db.get_chapter_footnotes(chapter_id, is_source=is_source)

    targets = resolve_targets(rows, chapter, args, is_source)

    print(f"Book ID:  {args.book_id}")
    print(f"Chapter:  {args.chapter}")
    print(f"Side:     {'SOURCE (untranslated)' if is_source else 'translated'}")
    print(f"Mode:     {'APPLY' if args.apply else 'DRY RUN'}")
    print("=" * 70)

    if not targets:
        print("No matching footnote(s) found. Nothing to do.")
        if rows:
            print(f"({len(rows)} footnote(s) exist on this side:)")
            for r in rows:
                print(f"  [id {r['id']}] anchor={r.get('anchor')!r}: "
                      f"{(r.get('body') or '')[:80]}")
        return

    for r in targets:
        print(f"  WOULD DELETE [id {r['id']}] anchor={r.get('anchor')!r}")
        print(f"      body: {(r.get('body') or '')[:120]}")

    if not args.apply:
        print("=" * 70)
        print(f"(dry run — {len(targets)} footnote(s) would be deleted; nothing written)")
        return

    deleted = 0
    for r in targets:
        if db.delete_footnote(r["id"]):
            deleted += 1
    rerender_side(db, chapter_id, args.book_id, is_source)

    print("=" * 70)
    print(f"Deleted {deleted} footnote(s) and re-rendered ch{args.chapter} "
          f"({'source' if is_source else 'translated'}).")


if __name__ == "__main__":
    main()
