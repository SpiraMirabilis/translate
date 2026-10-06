#!/usr/bin/env python3
"""
Read the entity-note revision log — the append-only history behind entity notes.

Every entity note write goes through db/entities_repo.py::set_entity_note, which
records the previous note, the new note, who wrote it (model/human/script), the
chapter it belongs to and the model's stated reason. That log is the only record
of *when a fact about a character became true*, and until now the only way to
read it was get_entities.py's automatic rewind or the Entities-page panel.

Usage:
    python note_revisions.py -b 90 -e 许春娘                 # full history, one entity
    python note_revisions.py -b 90 -e "Xu Chunniang" --diff  # word-diff each step
    python note_revisions.py -b 90 --chapters 460-500        # every entity, chapter range
    python note_revisions.py -b 90 -e 许春娘 --grep 'arm'    # revisions whose note mentions it
    python note_revisions.py -b 90 --introduced 'Golden Core'
    python note_revisions.py -b 90 -e 许春娘 --dropped 'RIGHT ARM'
    python note_revisions.py -b 90 -e 许春娘 --as-of 500     # the note as it read at ch500
    python note_revisions.py -b 90 --author human --limit 20
    python note_revisions.py -b 90 --shrink                  # flagged >50% length loss

THE TWO QUESTIONS THIS IS FOR.

  --introduced PATTERN  answers "which chapter first wrote this down" — it keeps
      only revisions where the pattern appears in the new note and NOT in the
      previous one. A plain --grep matches every later revision that merely
      carries the clause forward, which on a long-running protagonist is
      hundreds of rows; --introduced is the one row that is the event.

  --dropped PATTERN     is the mirror: the revision where a fact stopped being
      restated. Treat it as a lead, not a finding. Notes are capped at 500
      chars, so a clause can fall out because it was crowded out rather than
      because it stopped being true — the note history tells you when the model
      stopped saying something, and only the chapter text tells you why.
      (Book 90 carried "Severed her own RIGHT ARM in ch464" for 140 revisions
      after the arm actually grew back in ch490.)

Chapter-filter syntax matches get_entities.py --origin-chapter:
    N, N-M, >N, >=N, <N, <=N
Revisions with no chapter (hand edits, script sweeps) are excluded by any
chapter filter; --no-chapter selects exactly those instead.
"""

import argparse
import difflib
import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from database import DatabaseManager
from logger import Logger

from get_entities import parse_chapter_filter, resolve_book


def find_entities(db_manager, book_id, needle, entity_id=None):
    """Rows for the entities a -e/--entity-id selector names.

    -e matches untranslated OR translation, case-insensitively, as a substring
    — one selector finds 许春娘 and "Xu Chunniang" alike.
    """
    query = """
        SELECT id, category, untranslated, translation, origin_chapter, note
        FROM entities
        WHERE (book_id = ? OR book_id IS NULL)
    """
    params = [book_id]

    if entity_id:
        query += " AND id = ?"
        params.append(entity_id)
    elif needle:
        query += " AND (LOWER(untranslated) LIKE ? OR LOWER(translation) LIKE ?)"
        like = f"%{needle.lower()}%"
        params.extend([like, like])

    query += " ORDER BY COALESCE(origin_chapter, 0), untranslated"

    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]


def fetch_revisions(db_manager, book_id, entity_ids=None, chapter_expr=None,
                    no_chapter=False, author=None, shrink_only=False):
    """Revision rows in chronological (id) order, joined to their entity."""
    query = """
        SELECT r.id, r.entity_id, r.chapter_number, r.author, r.reason,
               r.previous_note, r.new_note, r.shrink, r.created_at,
               e.untranslated, e.translation, e.category
        FROM entity_note_revisions r
        LEFT JOIN entities e ON e.id = r.entity_id
        WHERE (r.book_id = ? OR r.book_id IS NULL)
    """
    params = [book_id]

    if entity_ids:
        placeholders = ",".join("?" for _ in entity_ids)
        query += f" AND r.entity_id IN ({placeholders})"
        params.extend(entity_ids)

    if no_chapter:
        query += " AND r.chapter_number IS NULL"
    elif chapter_expr:
        clause, chapter_params = parse_chapter_filter(chapter_expr,
                                                      column="r.chapter_number")
        query += f" AND {clause}"
        params.extend(chapter_params)

    if author:
        query += " AND r.author = ?"
        params.append(author)

    if shrink_only:
        query += " AND r.shrink = 1"

    query += " ORDER BY r.id"

    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]


def build_matcher(pattern, use_regex, ignore_case):
    """fn(text) -> bool. Substring by default, regex with --regex.

    Returns None for an empty pattern. Raises ValueError on an invalid regex.
    """
    if not pattern:
        return None
    if use_regex:
        try:
            compiled = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            raise ValueError(f"invalid regex {pattern!r}: {e}") from None
        return lambda text: bool(text) and compiled.search(text) is not None
    if ignore_case:
        needle = pattern.lower()
        return lambda text: bool(text) and needle in text.lower()
    return lambda text: bool(text) and pattern in text


def word_diff(previous, new):
    """A compact word-level diff: [-removed-] and {+added+}."""
    old_words = (previous or "").split()
    new_words = (new or "").split()
    out = []
    matcher = difflib.SequenceMatcher(None, old_words, new_words)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            chunk = old_words[i1:i2]
            if len(chunk) > 6:
                out.append(" ".join(chunk[:3] + ["…"] + chunk[-3:]))
            else:
                out.extend(chunk)
        elif tag == "delete":
            out.append("[-" + " ".join(old_words[i1:i2]) + "-]")
        elif tag == "insert":
            out.append("{+" + " ".join(new_words[j1:j2]) + "+}")
        elif tag == "replace":
            out.append("[-" + " ".join(old_words[i1:i2]) + "-]")
            out.append("{+" + " ".join(new_words[j1:j2]) + "+}")
    return " ".join(out)


def entity_label(row):
    untranslated = row.get("untranslated") or "?"
    translation = row.get("translation") or ""
    return f"{untranslated} : {translation}" if translation else untranslated


def filter_revisions(rows, grep=None, introduced=None, dropped=None, *,
                     regex=False, ignore_case=True, limit=None):
    """Apply --grep / --introduced / --dropped / --limit to fetch_revisions rows.

    Patterns are substrings (regexes with regex=True), case-insensitive unless
    ignore_case=False — the CLI default. Filters AND together:
      grep        the new note matches
      introduced  the new note matches and the previous note does not
      dropped     the previous note matches and the new note does not
    limit keeps the LAST N rows (the most recent). Raises ValueError on an
    invalid regex. Returns a new list.
    """
    grep_m = build_matcher(grep, regex, ignore_case)
    introduced_m = build_matcher(introduced, regex, ignore_case)
    dropped_m = build_matcher(dropped, regex, ignore_case)

    rows = list(rows)
    if grep_m:
        rows = [r for r in rows if grep_m(r["new_note"])]
    if introduced_m:
        rows = [r for r in rows
                if introduced_m(r["new_note"]) and not introduced_m(r["previous_note"])]
    if dropped_m:
        rows = [r for r in rows
                if dropped_m(r["previous_note"]) and not dropped_m(r["new_note"])]
    if limit:
        rows = rows[-limit:]
    return rows


def render_revision(row, diff=False, show_prev=False, show_entity=True):
    """The text block print_revision prints for one revision row (no trailing
    newline): a 78-dash rule, the header, then why/note/prev/diff lines."""
    chapter = f"ch{row['chapter_number']}" if row["chapter_number"] else "ch—"
    header = f"[{row['id']}] {chapter}  {row['author']}"
    if row["shrink"]:
        header += "  SHRINK"
    if show_entity:
        header += f"  {entity_label(row)}"
    lines = ["-" * 78, header]
    if row["reason"]:
        lines.append(f"  why : {row['reason']}")
    if diff:
        if row["previous_note"] is None:
            lines.append(f"  new : {row['new_note']}")
        else:
            lines.append(f"  diff: {word_diff(row['previous_note'], row['new_note'])}")
    else:
        if show_prev and row["previous_note"] is not None:
            lines.append(f"  prev: {row['previous_note']}")
        lines.append(f"  note: {row['new_note']}")
    return "\n".join(lines)


def print_revision(row, args, show_entity):
    print(render_revision(row, diff=args.diff, show_prev=args.show_prev,
                          show_entity=show_entity))


def main():
    parser = argparse.ArgumentParser(
        description="Read the entity-note revision log for a book.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("-b", "--book", required=True,
                        help="Book id or exact title")
    parser.add_argument("-e", "--entity",
                        help="Entity selector: substring of the untranslated "
                             "text or the translation (case-insensitive)")
    parser.add_argument("--entity-id", type=int, help="Exact entity row id")
    parser.add_argument("--chapters", help="Chapter filter: N, N-M, >N, <=N …")
    parser.add_argument("--no-chapter", action="store_true",
                        help="Only revisions with no chapter (hand edits, "
                             "script sweeps)")
    parser.add_argument("--author", choices=["model", "human", "script"],
                        help="Only revisions written by this author")
    parser.add_argument("--shrink", action="store_true",
                        help="Only revisions flagged as losing >50%% of the note")
    parser.add_argument("--grep", metavar="PATTERN",
                        help="Only revisions whose new note matches")
    parser.add_argument("--introduced", metavar="PATTERN",
                        help="Only revisions where the pattern appears in the "
                             "new note but not the previous one")
    parser.add_argument("--dropped", metavar="PATTERN",
                        help="Only revisions where the pattern was in the "
                             "previous note but not the new one")
    parser.add_argument("--regex", action="store_true",
                        help="Treat pattern arguments as regexes")
    parser.add_argument("-s", "--case-sensitive", action="store_true",
                        help="Case-sensitive pattern matching")
    parser.add_argument("--as-of", type=int, metavar="N",
                        help="Print the selected entities' notes as they read "
                             "at the end of chapter N, then exit")
    parser.add_argument("--diff", action="store_true",
                        help="Show a word-level diff instead of the full note")
    parser.add_argument("--show-prev", action="store_true",
                        help="Print the previous note alongside the new one")
    parser.add_argument("--limit", type=int, help="Keep only the last N rows")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    config = TranslationConfig()
    db_manager = DatabaseManager(config, Logger(config))

    book = resolve_book(db_manager, args.book)
    if not book:
        print(f"error: book not found: {args.book}", file=sys.stderr)
        sys.exit(1)
    book_id = book["id"]

    entity_ids = None
    entities = []
    if args.entity or args.entity_id:
        entities = find_entities(db_manager, book_id, args.entity, args.entity_id)
        if not entities:
            print(f"error: no entity in book {book_id} matches "
                  f"{args.entity or args.entity_id!r}", file=sys.stderr)
            sys.exit(1)
        entity_ids = [e["id"] for e in entities]

    if args.as_of is not None:
        notes = db_manager.notes_as_of(book_id, args.as_of, entity_ids=entity_ids)
        if args.format == "json":
            print(json.dumps({str(k): v for k, v in notes.items()},
                             ensure_ascii=False, indent=2))
            return
        by_id = {e["id"]: e for e in entities}
        print(f"# notes as of ch{args.as_of} — book {book_id} "
              f"({book.get('title')})")
        for entity_id, note in sorted(notes.items()):
            label = entity_label(by_id.get(entity_id, {})) if by_id else entity_id
            print("-" * 78)
            print(f"{label}")
            print(f"  note: {note if note else '(none at that point)'}")
        return

    rows = fetch_revisions(db_manager, book_id, entity_ids=entity_ids,
                           chapter_expr=args.chapters, no_chapter=args.no_chapter,
                           author=args.author, shrink_only=args.shrink)

    try:
        rows = filter_revisions(rows, args.grep, args.introduced, args.dropped,
                                regex=args.regex,
                                ignore_case=not args.case_sensitive,
                                limit=args.limit)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return

    scope = entity_label(entities[0]) if len(entities) == 1 else \
        (f"{len(entities)} entities" if entities else "all entities")
    print(f"# {len(rows)} revision(s) — book {book_id} "
          f"({book.get('title')}), {scope}")
    show_entity = len(entities) != 1
    for row in rows:
        print_revision(row, args, show_entity)


if __name__ == "__main__":
    main()
