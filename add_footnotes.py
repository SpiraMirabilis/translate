#!/usr/bin/env python3
"""Add first-mention footnotes to a book's TRANSLATED chapters, driven by a JSON map.

Use this for one-off cultural/meme/reference footnotes (e.g. footnoting the first
time a China-specific term, parody, or allusion appears) — the companion to
add_incantation_footnotes.py, which is specialised for Latin incantations.

Footnote convention (matches the project's manual style — no special markup):
  - inline marker hugs the term:   "...the Calabash Brothers[3] rushing in..."
  - definition at the bottom:       [3] Calabash Brothers (葫芦兄弟): a 1980s ...

Guarantees:
  1. First mention is book-wide, computed over currently-translated chapters in
     ascending order, then earliest line/column within that chapter. (This is the
     first PROSE occurrence, which can precede the term's entity origin_chapter.)
  2. Idempotent — a footnote whose exact body already appears anywhere in the
     book's translated chapters is skipped, so re-running after more chapters are
     translated only adds genuinely new first-mentions and never duplicates.
  3. Existing [n] markers are respected. After new markers are inserted, EVERY
     footnote in the chapter (existing + new) is renumbered by reading-order
     position and the definition block is rebuilt to match — so a new footnote
     placed above an existing one becomes [1] and the old [1] shifts to [2],
     rather than leaving the markers out of order. Multiple new footnotes in one
     chapter are numbered by appearance order; markers are inserted right-to-left
     so offsets hold. Idempotent for already-ordered chapters (numbers unchanged).

Input JSON ( --file ): an object mapping the EXACT English term to its footnote body.
  {
    "Evil Sword Immortal": "Evil Sword Immortal: the final boss of Chinese Paladin ...",
    "Dongfeng Express":    "Dongfeng Express: a play on PLA Rocket Force slang ..."
  }
Key order does not matter; placement is by where each term appears in the text.

By default placement is book-wide first-mention. Pass --chapter N to force every
term into chapter N instead (searching only that chapter). Use this when the
automatic first-mention would land inside an earlier footnote's body — e.g. a
previously-added footnote already mentions the term, so the book-wide search
would absurdly insert the new marker inside that footnote's definition. (The
term search always skips the trailing definition block, so a marker is never
placed inside a footnote body regardless of --chapter.)

For a single footnote, skip the JSON file and pass --term/--footnote directly.

By default footnotes are placed in the TRANSLATED text. Pass --source to place
them in the SOURCE (untranslated) text instead — terms are then matched against
the original-language source and definitions are written back to the source. All
other behaviour (first-mention, idempotency, renumbering) is identical.

Usage:
    python3 add_footnotes.py --book-id 56 --file footnotes.json --dry-run
    python3 add_footnotes.py --book-id 56 --file footnotes.json
    python3 add_footnotes.py --book-id 56 --file footnotes.json --chapter 20
    python3 add_footnotes.py --book-id 56 --file footnotes.json --source
    python3 add_footnotes.py --book-id 56 --term "Evil Sword Immortal" \
        --footnote "Evil Sword Immortal: the final boss of Chinese Paladin ..."
"""

import argparse
import json

from config import TranslationConfig
from db import DatabaseManager
from footnotes import (content_to_list, marker_position, prose_end_index,
                       renumber_chapter)
from logger import Logger


def main():
    parser = argparse.ArgumentParser(
        description="Add first-mention footnotes to a book's translated chapters from a JSON map.")
    parser.add_argument("--book-id", type=int, required=True)
    parser.add_argument("--file", help="JSON: {term: footnote_body}")
    parser.add_argument("--term", help="Single term to footnote (use with --footnote "
                                       "instead of --file).")
    parser.add_argument("--footnote", help="Footnote body for --term.")
    parser.add_argument("--chapter", type=int, default=None,
                        help="Force placement into this chapter (search only it) "
                             "instead of book-wide first-mention.")
    parser.add_argument("--source", action="store_true",
                        help="Place footnotes in the SOURCE (untranslated) text "
                             "instead of the translated text. Terms are matched "
                             "against the original-language source.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing.")
    args = parser.parse_args()

    if args.term is not None or args.footnote is not None:
        if args.file:
            print("Use either --file or --term/--footnote, not both.")
            return
        if not (args.term and args.footnote):
            print("--term and --footnote must be given together.")
            return
        footnotes = {args.term: args.footnote}
    elif args.file:
        with open(args.file, encoding="utf-8") as fh:
            footnotes = json.load(fh)
        if not isinstance(footnotes, dict) or not footnotes:
            print("Input must be a non-empty JSON object {term: body}.")
            return
    else:
        print("Provide either --file or --term with --footnote.")
        return

    config = TranslationConfig()
    # strict_writes: a failed footnote insert / re-render raises instead of being swallowed.
    db = DatabaseManager(config, Logger(config), strict_writes=True)

    chapter_rows = db.list_chapters(args.book_id)
    chapter_id_by_num = {c["chapter"]: c["id"] for c in chapter_rows}
    chapters = sorted(
        (c["chapter"] for c in chapter_rows),
        key=lambda n: (n is None, n),
    )
    content_key = "untranslated" if args.source else "content"
    content = {cn: content_to_list(db.get_chapter(book_id=args.book_id, chapter_number=cn)[content_key])
               for cn in chapters}
    full_text = {cn: "\n".join(content[cn]) for cn in chapters}

    print(f"Book ID:   {args.book_id}")
    print(f"Footnotes: {len(footnotes)} in map")
    print(f"Text:      {'SOURCE (untranslated)' if args.source else 'translated'}")
    print(f"Chapters:  {len(chapters)} translated")
    if args.chapter is not None:
        print(f"Target:    ch{args.chapter} (forced placement)")
    print(f"Mode:      {'DRY RUN' if args.dry_run else 'APPLY'}")
    print("=" * 70)

    if args.chapter is not None and args.chapter not in content:
        print(f"Chapter {args.chapter} is not a translated chapter of this book.")
        return

    # Find first prose occurrence per term; skip already-footnoted (idempotent).
    plan = {}  # chapter -> list of (term, body, line_idx, insert_at)
    skipped = []
    not_found = []
    for term, body in footnotes.items():
        if any(body in t for t in full_text.values()):
            skipped.append(term)
            continue
        placed = False
        search_chapters = [args.chapter] if args.chapter is not None else chapters
        for cn in search_chapters:
            prose_end = prose_end_index(content[cn])
            for li, line in enumerate(content[cn][:prose_end]):
                idx = line.find(term)
                if idx != -1:
                    # Shared with render_footnotes so the marker the table
                    # re-renders lands exactly where this preview puts it.
                    pos = marker_position(line, idx, idx + len(term))
                    plan.setdefault(cn, []).append((term, body, li, pos))
                    placed = True
                    break
            if placed:
                break
        if not placed:
            not_found.append(term)

    is_source = 1 if args.source else 0
    total = 0
    for cn in sorted(plan):
        items = sorted(plan[cn], key=lambda x: (x[2], x[3]))
        # Preview the resulting numbering (the table-driven render reproduces it).
        new_lines, final_for_item, changed = renumber_chapter(content[cn], items)
        total += len(items)
        note = "  (renumbered existing markers)" if changed else ""
        print(f"ch{cn}: +{len(items)} footnote(s){note}")
        for k, (term, body, li, pos) in enumerate(items):
            print(f"    [{final_for_item.get(k, '?')}] {term}")
        if not args.dry_run:
            # Persist each footnote to the table (source of truth), then re-render
            # the inline markers + definition block from the table so they survive
            # any later retranslation.
            chapter_id = chapter_id_by_num.get(cn)
            for term, body, li, pos in items:
                db.add_footnote(
                    args.book_id, chapter_id, term, body,
                    source_term=(term if is_source else None),
                    occurrence=1, is_source=is_source,
                )
            db.rerender_chapter_footnotes(chapter_id)

    print("=" * 70)
    print(f"Added: {total} footnote(s) across {len(plan)} chapter(s)")
    if skipped:
        print(f"Skipped (already footnoted): {len(skipped)} -> {', '.join(skipped)}")
    if not_found:
        print(f"NOT FOUND in any chapter: {', '.join(not_found)}")
    if args.dry_run:
        print("(dry run — nothing written)")


if __name__ == "__main__":
    main()
