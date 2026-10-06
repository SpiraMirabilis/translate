#!/usr/bin/env python3
"""
Dump entities for a book, optionally filtered by origin_chapter.

Usage:
    python get_entities.py --book 1
    python get_entities.py --book "Book Title" --origin-chapter 1-20
    python get_entities.py --book 1 --origin-chapter ">15" --output entities.json
    python get_entities.py --book 1 --origin-chapter "<100"
    python get_entities.py --book 1 --origin-chapter 42
    python get_entities.py --book 1 --as-of-chapter 34
    python get_entities.py --book 1 --origin-chapter 1-20 --current-notes

Filter syntax for --origin-chapter:
    N         exact chapter
    N-M       inclusive range
    >N, >=N   greater than (or equal)
    <N, <=N   less than (or equal)

NOTES ARE SHOWN AS OF A POINT IN TIME.
Entity notes accumulate as a book advances, so today's note on the protagonist
describes the protagonist at the latest chapter. Asking for an early stretch of
the book and being handed late-book notes is misleading, so an --origin-chapter
filter with an upper bound (N, N-M, <N, <=N) also winds the notes back to that
chapter; --as-of-chapter N sets the point explicitly, and --current-notes turns
the rewind off. Translations are never wound back — a name's rendering is
supposed to be the same in every chapter.

Where a note's history is incomplete (notes written before the revision log
existed have no creation record) you get the closest thing known in time rather
than a guess, and entities that did not exist yet report no note at all.

THE CHAPTER FILTER FOLLOWS NOTES TOO.
origin_chapter alone answers "what was introduced here", which misses an entity
that has been around since chapter 1 but whose note was rewritten during the
range — exactly the entity a review of that range cares about. So --origin-chapter
also matches entities whose note changed inside it, tagged "note updated chN" in
the output. --origin-only restores the old origin_chapter-only behaviour.

------------------------------------------------------------------------
MIGRATION TEMPLATE — this script (and correct_entity_translation.py) are
the reference pattern for porting the other root utility scripts onto the
post-2026-07 architecture:

  1. Import DatabaseManager from the `db` package (the root `database`
     module is a compatibility shim re-exporting the same names — both
     work, `db` is canonical).
  2. Read-only custom SQL goes inside `with db_manager._conn() as conn:`
     — connections are committed/rolled back/closed by the scope, never
     leaked on an exception. Pass `dict_rows=True` to get dict-like rows
     on BOTH backends (kills the isinstance(row, dict) SQLite-vs-MySQL
     dance old scripts carry).
  3. Prefer an existing repo method (db/*_repo.py) over hand-rolled SQL
     when one exists — e.g. update_entity_by_id, get_chapters_bulk.
  4. Pure text transforms live in chapter_text_ops.py — never re-implement
     the case-preserving substitution logic.
  5. Scripts that WRITE should construct
     DatabaseManager(config, logger, strict_writes=True) so failures raise
     instead of returning None (read-only scripts don't need it).
------------------------------------------------------------------------
"""

import argparse
import json
import os
import re
import sys
import warnings

# Silence any FutureWarnings emitted at provider import time.
warnings.filterwarnings("ignore", category=FutureWarning)
# Force quiet logger regardless of DEBUG env var — this is a one-shot CLI.
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger


def parse_chapter_filter(expr, column="origin_chapter"):
    """Parse an origin_chapter filter expression into (sql_fragment, params).

    Returns (None, []) if expr is falsy.
    Raises ValueError on malformed input.
    """
    if not expr:
        return None, []

    s = expr.strip()

    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return f"{column} BETWEEN ? AND ?", [lo, hi]

    m = re.fullmatch(r"(>=|<=|>|<|=)\s*(\d+)", s)
    if m:
        op, n = m.group(1), int(m.group(2))
        if op == "=":
            op = "="
        return f"{column} {op} ?", [n]

    m = re.fullmatch(r"\d+", s)
    if m:
        return f"{column} = ?", [int(s)]

    raise ValueError(f"Unrecognized chapter filter: {expr!r}")


def filter_upper_bound(expr):
    """The latest chapter an --origin-chapter filter can match, or None.

    This is the point the notes are wound back to when the caller doesn't name
    one: asking for chapters 1-20 means asking about the book as it stood at 20.
    An open-ended filter ('>15') has no upper bound, so notes stay current.
    """
    if not expr:
        return None
    s = expr.strip()

    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", s)
    if m:
        return max(int(m.group(1)), int(m.group(2)))

    m = re.fullmatch(r"(<=|<|=)\s*(\d+)", s)
    if m:
        n = int(m.group(2))
        return n - 1 if m.group(1) == "<" else n

    m = re.fullmatch(r"\d+", s)
    if m:
        return int(s)

    return None


def resolve_book(db_manager, book_arg):
    """Resolve a book argument (numeric id or title) to a book dict."""
    if book_arg.isdigit():
        book = db_manager.get_book(book_id=int(book_arg))
        if book:
            return book
    return db_manager.get_book(title=book_arg)


def note_update_chapters(db_manager, book_id, chapter_expr):
    """{entity_id: [chapter, ...]} for notes revised inside the filter's range.

    An entity introduced at chapter 1 whose note was rewritten at chapter 245 is
    part of what happened in chapters 240-260, even though its origin_chapter
    says otherwise — this is what lets the chapter filter find it.
    """
    if not chapter_expr:
        return {}
    clause, params = parse_chapter_filter(chapter_expr, column="chapter_number")
    if not clause:
        return {}
    out = {}
    with db_manager._conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT entity_id, chapter_number FROM entity_note_revisions "
            "WHERE (book_id = ? OR book_id IS NULL) AND " + clause + " ORDER BY id",
            [book_id] + params)
        for entity_id, chapter_number in cursor.fetchall():
            out.setdefault(entity_id, []).append(chapter_number)
    return out


def fetch_entities(db_manager, book_id, chapter_clause, chapter_params,
                   as_of_chapter=None, note_chapters=None):
    """Query entities for the given book with optional origin_chapter filter.

    With as_of_chapter set, each note is replaced by the note in force at the end
    of that chapter (db/entities_repo.py::notes_as_of). Entities that carried no
    note then simply have none here.

    note_chapters ({entity_id: [chapter, ...]}, from note_update_chapters) widens
    the chapter filter to entities whose *note* changed in the range, and tags
    them so it is obvious why they are in the list.
    """
    note_chapters = note_chapters or {}
    query = """
        SELECT id, category, untranslated, translation, last_chapter,
               incorrect_translation, gender, book_id, origin_chapter, note
        FROM entities
        WHERE (book_id = ? OR book_id IS NULL)
    """
    params = [book_id]

    if chapter_clause:
        if note_chapters:
            placeholders = ",".join("?" * len(note_chapters))
            query += f" AND ({chapter_clause} OR id IN ({placeholders}))"
            params.extend(chapter_params)
            params.extend(note_chapters.keys())
        else:
            query += f" AND {chapter_clause}"
            params.extend(chapter_params)

    query += " ORDER BY category, COALESCE(origin_chapter, 0), untranslated"

    # dict_rows=True: dict-like rows on both SQLite and MySQL; the scope
    # commits/rolls back/closes the connection.
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]

    historic_notes = ({} if as_of_chapter is None
                      else db_manager.notes_as_of(book_id, as_of_chapter))

    grouped = {}
    for row in rows:
        entry = {
            "untranslated": row["untranslated"],
            "translation": row["translation"],
            "origin_chapter": row["origin_chapter"],
            "last_chapter": row["last_chapter"],
        }
        if row["incorrect_translation"]:
            entry["incorrect_translation"] = row["incorrect_translation"]
        if row["gender"]:
            entry["gender"] = row["gender"]
        note = (historic_notes.get(row["id"]) if as_of_chapter is not None
                else row["note"])
        if note:
            entry["note"] = note
        if note_chapters.get(row["id"]):
            entry["note_updated_chapters"] = note_chapters[row["id"]]
        if row["book_id"] is None:
            entry["global"] = True

        grouped.setdefault(row["category"], []).append(entry)

    return grouped


def resolve_as_of(origin_chapter=None, as_of_chapter=None, current_notes=False):
    """The chapter the notes are wound back to, or None for current notes.

    An --origin-chapter range is a question about a period of the book, so the
    notes should describe the book as it was then. as_of_chapter overrides,
    current_notes opts out.
    """
    as_of = as_of_chapter
    if as_of is None and not current_notes:
        as_of = filter_upper_bound(origin_chapter)
    if current_notes:
        as_of = None
    return as_of


def build_entities_payload(db_manager, book, origin_chapter=None, *,
                           as_of_chapter=None, current_notes=False,
                           origin_only=False):
    """Everything main() dumps: {book, filter, notes_as_of_chapter, entities}.

    `book` is a book dict (see resolve_book) with at least "id" and "title".
    `entities` is fetch_entities' {category: [entry, ...]} mapping.
    Raises ValueError on a malformed origin_chapter filter.
    """
    chapter_clause, chapter_params = parse_chapter_filter(origin_chapter)
    as_of = resolve_as_of(origin_chapter, as_of_chapter, current_notes)

    # An entity whose note was rewritten during the range belongs to that range's
    # glossary work even if it was introduced hundreds of chapters earlier.
    note_chapters = ({} if origin_only
                     else note_update_chapters(db_manager, book["id"], origin_chapter))

    grouped = fetch_entities(db_manager, book["id"], chapter_clause, chapter_params,
                             as_of_chapter=as_of, note_chapters=note_chapters)
    return {
        "book": {"id": book["id"], "title": book.get("title")},
        "filter": origin_chapter,
        "notes_as_of_chapter": as_of,
        "entities": grouped,
    }


def render_entities_text(payload):
    """The --format text rendering of a build_entities_payload() dict.

    Returns the full text, newline-terminated.
    """
    book = payload["book"]
    grouped = payload["entities"]
    origin_chapter = payload.get("filter")
    as_of = payload.get("notes_as_of_chapter")

    lines = [f"# {book.get('title')} (id={book['id']})"]
    if origin_chapter:
        lines.append(f"# origin_chapter filter: {origin_chapter}")
    if as_of is not None:
        lines.append(f"# notes as of chapter {as_of}")
    for category in sorted(grouped):
        entries = grouped[category]
        lines.append("")
        lines.append(f"== {category} ({len(entries)}) ==")
        for e in entries:
            origin = e.get("origin_chapter")
            origin_str = f"ch{origin}" if origin is not None else "ch?"
            extra = []
            if e.get("gender"):
                extra.append(e["gender"])
            if e.get("global"):
                extra.append("global")
            if e.get("note_updated_chapters"):
                chs = ",".join(f"ch{c}" for c in e["note_updated_chapters"])
                extra.append(f"note updated {chs}")
            if e.get("note"):
                extra.append(f"note={e['note']}")
            suffix = f"  [{', '.join(extra)}]" if extra else ""
            lines.append(f"  {origin_str:>6}  {e['untranslated']} -> {e['translation']}{suffix}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Dump entities for a book, optionally filtered by origin_chapter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Filter examples:\n"
            "  --origin-chapter 1-20    chapters 1 through 20\n"
            "  --origin-chapter '>15'   chapters after 15\n"
            "  --origin-chapter '<=99'  chapters up to and including 99\n"
            "  --origin-chapter 42      exactly chapter 42\n"
            "\nMatches entities introduced in the range OR whose note changed in it\n"
            "(--origin-only for origin_chapter alone). Notes are shown as they read\n"
            "at the end of the range unless --current-notes is given.\n"
        ),
    )
    parser.add_argument(
        "--book", "-b", required=True,
        help="Book ID (numeric) or exact title."
    )
    parser.add_argument(
        "--origin-chapter", "-c", default=None,
        help="Origin chapter filter (e.g. '1-20', '>15', '<100', '42')."
    )
    parser.add_argument(
        "--as-of-chapter", "-a", type=int, default=None,
        help=("Show each note as it read at the end of this chapter, rather than "
              "its latest text. Defaults to the upper bound of --origin-chapter "
              "when that filter has one.")
    )
    parser.add_argument(
        "--current-notes", action="store_true",
        help="Show the latest note text even when --origin-chapter implies a point in time."
    )
    parser.add_argument(
        "--origin-only", action="store_true",
        help=("Match --origin-chapter against origin_chapter alone. By default an "
              "entity also matches when its NOTE was revised inside the range.")
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output file path. Defaults to stdout."
    )
    parser.add_argument(
        "--format", "-f", choices=("json", "text"), default="json",
        help="Output format (default: json)."
    )

    args = parser.parse_args()

    try:
        parse_chapter_filter(args.origin_chapter)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    config = TranslationConfig()
    logger = Logger(config)
    db_manager = DatabaseManager(config, logger)

    book = resolve_book(db_manager, args.book)
    if not book:
        print(f"error: book not found: {args.book!r}", file=sys.stderr)
        sys.exit(1)

    payload = build_entities_payload(
        db_manager, book, args.origin_chapter,
        as_of_chapter=args.as_of_chapter, current_notes=args.current_notes,
        origin_only=args.origin_only)
    grouped = payload["entities"]

    if args.format == "json":
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        rendered = render_entities_text(payload)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(rendered)
        total = sum(len(v) for v in grouped.values())
        print(f"Wrote {total} entities to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
        if not rendered.endswith("\n"):
            sys.stdout.write("\n")


if __name__ == "__main__":
    main()
