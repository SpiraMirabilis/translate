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

from footnotes import (content_to_list, marker_position, prose_end_index,
                       renumber_chapter)


# ── importable library (no printing, no exits) ────────────────────────────────

def load_book_text(db, book_id, source=False):
    """Load a book's chapter text for first-mention placement.

    Returns a dict:
      chapters:          chapter numbers, ascending (None sorts last)
      content:           {chapter_number: [line, ...]} — translated, or source
                         (untranslated) text when `source` is true
      full_text:         {chapter_number: "\n".join(lines)} — for the
                         idempotency (exact-body) check
      chapter_id_by_num: {chapter_number: chapter_id}
    """
    chapter_rows = db.list_chapters(book_id)
    chapter_id_by_num = {c["chapter"]: c["id"] for c in chapter_rows}
    chapters = sorted(
        (c["chapter"] for c in chapter_rows),
        key=lambda n: (n is None, n),
    )
    content_key = "untranslated" if source else "content"
    content = {}
    for cn in chapters:
        ch = db.get_chapter(book_id=book_id, chapter_number=cn)
        content[cn] = content_to_list(ch.get(content_key) if ch else None)
    full_text = {cn: "\n".join(content[cn]) for cn in chapters}
    return {
        "chapters": chapters,
        "content": content,
        "full_text": full_text,
        "chapter_id_by_num": chapter_id_by_num,
    }


HEADING_WARNING = ("first occurrence is in the chapter heading (line 0) — the "
                   "marker will land in the title")


def plan_footnotes(footnotes, chapters, content, full_text, *, chapter=None):
    """Decide where each {term: body} footnote goes.

    Placement rules:
      - a footnote whose exact body already appears anywhere in `full_text` is
        skipped (idempotent re-runs);
      - otherwise the first prose occurrence book-wide wins: chapters in the
        order given, then earliest line; the trailing definition block is never
        searched, so a marker can't land inside an existing footnote body;
      - `chapter` forces placement into that one chapter (ValueError if it is
        not in `content`).

    Returns (plan, skipped, not_found):
      plan:      list of dicts ordered by (chapter, line_idx, insert_at), each
                 {chapter, anchor, body, line_idx, insert_at, number,
                  renumbers_existing, warning}. `number` is the previewed [n]
                 after the chapter is renumbered; `renumbers_existing` is true
                 when existing markers in that chapter shift; `warning` is a
                 string when the anchor's first occurrence is the heading line
                 (content[0]), else None.
      skipped:   terms whose body is already in the book
      not_found: terms with no prose occurrence in the searched chapters
    """
    if not isinstance(footnotes, dict) or not footnotes:
        raise ValueError("footnotes must be a non-empty {term: body} mapping.")
    if chapter is not None and chapter not in content:
        raise ValueError(f"Chapter {chapter} is not a translated chapter of this book.")

    by_ch = {}  # chapter -> list of (term, body, line_idx, insert_at)
    skipped = []
    not_found = []
    for term, body in footnotes.items():
        if any(body in t for t in full_text.values()):
            skipped.append(term)
            continue
        placed = False
        search_chapters = [chapter] if chapter is not None else chapters
        for cn in search_chapters:
            prose_end = prose_end_index(content[cn])
            for li, line in enumerate(content[cn][:prose_end]):
                idx = line.find(term)
                if idx != -1:
                    # Shared with render_footnotes so the marker the table
                    # re-renders lands exactly where this preview puts it.
                    pos = marker_position(line, idx, idx + len(term))
                    by_ch.setdefault(cn, []).append((term, body, li, pos))
                    placed = True
                    break
            if placed:
                break
        if not placed:
            not_found.append(term)

    plan = []
    for cn in sorted(by_ch, key=lambda n: (n is None, n)):
        items = sorted(by_ch[cn], key=lambda x: (x[2], x[3]))
        # Preview the resulting numbering (the table-driven render reproduces it).
        _new_lines, final_for_item, changed = renumber_chapter(content[cn], items)
        for k, (term, body, li, pos) in enumerate(items):
            plan.append({
                "chapter": cn,
                "anchor": term,
                "body": body,
                "line_idx": li,
                "insert_at": pos,
                "number": final_for_item.get(k),
                "renumbers_existing": bool(changed),
                "warning": HEADING_WARNING if li == 0 else None,
            })
    return plan, skipped, not_found


def apply_footnote_plan(db, book_id, plan, chapter_id_by_num, is_source):
    """Persist a plan from plan_footnotes to the footnotes table (the source of
    truth), then re-render each touched chapter's markers + definition block.

    Returns the number of footnotes written. Raises ValueError for a plan
    chapter with no chapter id, RuntimeError when a DB write fails.
    """
    is_source = 1 if is_source else 0
    by_ch = {}
    for item in plan:
        by_ch.setdefault(item["chapter"], []).append(item)
    for cn in by_ch:
        if chapter_id_by_num.get(cn) is None:
            raise ValueError(f"Chapter {cn} has no chapter id in this book.")

    total = 0
    for cn, items in by_ch.items():
        chapter_id = chapter_id_by_num[cn]
        for item in items:
            fid = db.add_footnote(
                book_id, chapter_id, item["anchor"], item["body"],
                source_term=(item["anchor"] if is_source else None),
                occurrence=1, is_source=is_source,
            )
            if fid is None:
                raise RuntimeError(
                    f"Failed to write footnote {item['anchor']!r} to ch{cn}.")
            total += 1
        if not db.rerender_chapter_footnotes(chapter_id):
            raise RuntimeError(f"Failed to re-render footnotes for ch{cn}.")
    return total


# ── CLI ──────────────────────────────────────────────────────────────────────

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

    from config import TranslationConfig
    from db import DatabaseManager
    from logger import Logger

    config = TranslationConfig()
    # strict_writes: a failed footnote insert / re-render raises instead of being swallowed.
    db = DatabaseManager(config, Logger(config), strict_writes=True)

    text = load_book_text(db, args.book_id, source=args.source)
    chapters, content = text["chapters"], text["content"]

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
    plan, skipped, not_found = plan_footnotes(
        footnotes, chapters, content, text["full_text"], chapter=args.chapter)

    by_ch = {}
    for item in plan:
        by_ch.setdefault(item["chapter"], []).append(item)
    for cn, items in by_ch.items():
        note = "  (renumbered existing markers)" if items[0]["renumbers_existing"] else ""
        print(f"ch{cn}: +{len(items)} footnote(s){note}")
        for item in items:
            num = item["number"] if item["number"] is not None else "?"
            print(f"    [{num}] {item['anchor']}")
            if item["warning"]:
                print(f"        WARNING: {item['warning']}")

    if not args.dry_run and plan:
        # Persist each footnote to the table (source of truth), then re-render
        # the inline markers + definition block from the table so they survive
        # any later retranslation.
        apply_footnote_plan(db, args.book_id, plan, text["chapter_id_by_num"],
                            1 if args.source else 0)

    print("=" * 70)
    print(f"Added: {len(plan)} footnote(s) across {len(by_ch)} chapter(s)")
    if skipped:
        print(f"Skipped (already footnoted): {len(skipped)} -> {', '.join(skipped)}")
    if not_found:
        print(f"NOT FOUND in any chapter: {', '.join(not_found)}")
    if args.dry_run:
        print("(dry run — nothing written)")


if __name__ == "__main__":
    main()
