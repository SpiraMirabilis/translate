#!/usr/bin/env python3
"""
Correct an entity's translation in the database for a given book.

Optionally, propagate the correction across the book's translated chapters
using the same find-and-replace logic as the Entity Editor modal:
case-insensitive match, case-preserving word-by-word replacement.

Usage:
    python correct_entity_translation.py --book-id 5 --untranslated "陆青云" --translation "Lu Qingyun"
    python correct_entity_translation.py --book-id 5 --untranslated "陆青云" --translation "Lu Qingyun" --substitute
    python correct_entity_translation.py --book-id 5 --untranslated "陆青云" --translation "Lu Qingyun" --safer-substitute

------------------------------------------------------------------------
MIGRATION TEMPLATE — see get_entities.py's header for the full pattern.
This script demonstrates the WRITE side:
  * DatabaseManager(config, logger, strict_writes=True) — failures raise
    loudly instead of returning None.
  * db.update_entity_by_id(...) repo method instead of hand-rolled UPDATE.
  * chapter_text_ops for the case-preserving substitution — the single
    canonical implementation shared with the web Entity Editor modal
    (this script used to carry its own drifting copy).
  * All custom SQL inside `with db_manager._conn(dict_rows=True) as conn:`
    — the whole substitution sweep is now ONE transaction (commit on
    success, rollback on error) instead of autocommit-per-statement.
------------------------------------------------------------------------
"""

import argparse
import json
import sys

from chapter_text_ops import (
    build_case_preserving_replacer,
    build_substitution_pattern,
    source_mentions,
)
from config import TranslationConfig
from db import DatabaseManager
from logger import Logger


def find_entity(db_manager: DatabaseManager, book_id: int, untranslated: str):
    """
    Look up an entity by (book_id, untranslated). Returns a list of matching rows
    (id, category, translation) — there may be more than one row across categories.
    """
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, category, translation FROM entities WHERE book_id = ? AND untranslated = ?",
            (book_id, untranslated),
        )
        return [(r["id"], r["category"], r["translation"]) for r in cursor.fetchall()]


def update_entity_translation(db_manager: DatabaseManager, entity_id: int,
                              new_translation: str, old_translation: str):
    """Update the entity's translation, and store the old value as incorrect_translation."""
    db_manager.update_entity_by_id(
        entity_id,
        translation=new_translation,
        incorrect_translation=old_translation or None,
    )


def find_chapters_with_untranslated(db_manager: DatabaseManager, book_id: int,
                                    untranslated: str) -> dict:
    """
    Return ``{chapter_id: chapter_number}`` for the chapters of `book_id` whose
    untranslated (source) content contains `untranslated`.

    Used by --safer-substitute to limit the blast radius of a translation
    substitution to chapters that actually feature the entity in their source
    text — avoiding accidental rewrites in chapters that merely happen to
    contain the old English string for unrelated reasons.

    A dict rather than a set of ids: membership tests and len() read the same,
    and the chapter *numbers* are what scopes the accompanying note sweep to
    entities that originated in these chapters (see substitute_in_chapters).
    """
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, chapter_number, untranslated_content FROM chapters WHERE book_id = ?",
            (book_id,),
        )
        return {r["id"]: r["chapter_number"] for r in cursor.fetchall()
                if source_mentions(r["untranslated_content"], untranslated)}


def note_scope_for(chapter_ids) -> set:
    """Chapter numbers the note sweep is restricted to.

    ``None`` (no chapter restriction = book-wide prose sweep) stays ``None``,
    which tells substitute_in_entity_notes that every note in the book is
    eligible — matching the blast radius the prose sweep just used.
    """
    return None if chapter_ids is None else set(chapter_ids.values())


def count_note_substitutions(db_manager: DatabaseManager, book_id: int,
                             old_translation: str, new_translation: str,
                             chapter_ids: dict = None,
                             word_boundary: bool = False) -> int:
    """Dry-run counterpart of the note sweep run by substitute_in_chapters."""
    return db_manager.substitute_in_entity_notes(
        book_id, old_translation, new_translation,
        chapter_numbers=note_scope_for(chapter_ids),
        word_boundary=word_boundary,
        dry_run=True,
    )


def count_substitutions(db_manager: DatabaseManager, book_id: int,
                        old_translation: str, new_translation: str,
                        chapter_ids: set = None,
                        word_boundary: bool = False) -> int:
    """
    Count how many chapters would actually change if `old_translation` were
    replaced with `new_translation` in their translated_content or their title.
    If `chapter_ids` is given, only those chapters are considered. Used for
    dry-run reporting.

    A chapter is counted only when the replacement genuinely alters a line or
    the title, so the dry-run figure matches what an apply run reports —
    chapters that already contain the corrected text are not counted. A chapter
    whose *title alone* matches still counts.
    """
    if not old_translation or old_translation == new_translation:
        return 0
    pattern = build_substitution_pattern(old_translation, word_boundary)
    match_case = build_case_preserving_replacer(old_translation, new_translation)
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, translated_content, title FROM chapters WHERE book_id = ?",
            (book_id,),
        )
        n = 0
        for r in cursor.fetchall():
            if chapter_ids is not None and r["id"] not in chapter_ids:
                continue
            title = r["title"] or ""
            if pattern.sub(match_case, title) != title:
                n += 1
                continue
            try:
                content = json.loads(r["translated_content"])
            except (json.JSONDecodeError, TypeError):
                continue
            if any(pattern.sub(match_case, line) != line for line in content):
                n += 1
    return n


def substitute_in_chapters(db_manager: DatabaseManager, book_id: int,
                           old_translation: str, new_translation: str,
                           chapter_ids: dict = None,
                           word_boundary: bool = False) -> tuple:
    """
    Replace `old_translation` with `new_translation` in every chapter's
    translated_content AND title for `book_id`, using the same chapter_text_ops
    logic as the web Entity Editor modal (case-insensitive search,
    case-preserving word-by-word replacement).

    Titles live in their own column, so a sweep that touched only
    translated_content left the corrected term stale in the chapter heading —
    book 93's ch1 kept "The Sky Dog Devours the Sun" in its title long after the
    prose said "Celestial Dog", and the Reader, the TOC and every EPUB export
    read from that column. This mirrors `DatabaseManager.replace_in_chapters`,
    which has included titles since 2026-07-10.

    If `chapter_ids` is given, only those chapters are touched (the rest are
    skipped). This is how --safer-substitute restricts the replacement to
    chapters whose source text actually contains the entity.

    When `word_boundary` is True, only whole-word occurrences are replaced
    (e.g. "Dai" won't be rewritten inside "Daiyu").

    The same substitution is then applied to the *notes* of entities that
    originated in the swept chapters — a stale note keeps feeding the old term
    back into later translations (see substitute_in_entity_notes). It runs on
    this function's cursor, so notes and chapters share one transaction.

    Returns ``(chapters_modified, notes_modified)``. A chapter counts as
    modified if its prose or its title changed. All updates commit as a
    single transaction — an error mid-sweep rolls the whole run back.
    """
    if not old_translation or old_translation == new_translation:
        return 0, 0

    pattern = build_substitution_pattern(old_translation, word_boundary)
    match_case = build_case_preserving_replacer(old_translation, new_translation)

    affected = 0
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, translated_content, title FROM chapters WHERE book_id = ?",
            (book_id,),
        )
        rows = cursor.fetchall()

        for r in rows:
            if chapter_ids is not None and r["id"] not in chapter_ids:
                continue

            try:
                content = json.loads(r["translated_content"])
            except (json.JSONDecodeError, TypeError):
                content = None

            changed = False
            if content is not None:
                for i in range(len(content)):
                    new_line = pattern.sub(match_case, content[i])
                    if new_line != content[i]:
                        content[i] = new_line
                        changed = True

            # Titles are a separate column and a separate decision: a chapter
            # whose title alone matches must still be written and counted.
            old_title = r["title"]
            new_title = old_title
            title_changed = False
            if old_title:
                new_title = pattern.sub(match_case, old_title)
                title_changed = new_title != old_title

            if changed and title_changed:
                cursor.execute(
                    "UPDATE chapters SET translated_content = ?, title = ? "
                    "WHERE id = ?",
                    (json.dumps(content, ensure_ascii=False), new_title, r["id"]),
                )
            elif changed:
                cursor.execute(
                    "UPDATE chapters SET translated_content = ? WHERE id = ?",
                    (json.dumps(content, ensure_ascii=False), r["id"]),
                )
            elif title_changed:
                cursor.execute(
                    "UPDATE chapters SET title = ? WHERE id = ?",
                    (new_title, r["id"]),
                )

            if changed or title_changed:
                affected += 1

        notes_affected = db_manager.substitute_in_entity_notes(
            book_id, old_translation, new_translation,
            chapter_numbers=note_scope_for(chapter_ids),
            word_boundary=word_boundary,
            cursor=cursor,
        )

    return affected, notes_affected


def main():
    parser = argparse.ArgumentParser(description="Correct an entity translation in the database.")
    parser.add_argument("--book-id", type=int, required=True, help="Book ID the entity belongs to.")
    parser.add_argument("--untranslated", required=True, help="Untranslated (Chinese) word/phrase.")
    parser.add_argument("--translation", required=True, help="New (corrected) translation.")
    parser.add_argument(
        "--substitute",
        action="store_true",
        help="Also replace old translation with new translation across all translated chapters "
             "(case-insensitive, case-preserving — same as Entity Editor modal).",
    )
    parser.add_argument(
        "--safer-substitute",
        action="store_true",
        help="Like --substitute, but first finds the chapters whose source (untranslated) text "
             "contains the entity and only substitutes within that subset — avoids rewriting "
             "chapters that merely happen to contain the old English string.",
    )
    parser.add_argument(
        "-w", "--word-boundary",
        action="store_true",
        help="Make the substitution word-boundary safe: only replace whole-word "
             "occurrences of the old translation (e.g. 'Dai' won't be rewritten "
             "inside 'Daiyu'). Applies to both --substitute and --safer-substitute.",
    )
    parser.add_argument(
        "--category",
        help="If the untranslated text exists in more than one category, restrict to this one.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing to the database.",
    )
    args = parser.parse_args()

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: a failed UPDATE raises loudly instead of returning None.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    matches = find_entity(db_manager, args.book_id, args.untranslated)
    if args.category:
        matches = [m for m in matches if m[1] == args.category]

    if not matches:
        scope = f" in category '{args.category}'" if args.category else ""
        print(f"No entity found for book_id={args.book_id}, untranslated={args.untranslated!r}{scope}.")
        sys.exit(1)

    if len(matches) > 1:
        print(f"Found {len(matches)} entities matching {args.untranslated!r} in book {args.book_id}:")
        for eid, cat, trans in matches:
            print(f"  id={eid}  category={cat}  translation={trans!r}")
        print("Pass --category to disambiguate.")
        sys.exit(1)

    entity_id, category, old_translation = matches[0]
    print(f"Entity id={entity_id} category={category}")
    print(f"  Old translation: {old_translation!r}")
    print(f"  New translation: {args.translation!r}")

    if old_translation == args.translation:
        print("New translation matches existing — nothing to update.")
        return

    do_substitute = args.substitute or args.safer_substitute

    if args.dry_run:
        print("[dry-run] Would update entity row.")
        if do_substitute:
            chapter_ids = (
                find_chapters_with_untranslated(db_manager, args.book_id, args.untranslated)
                if args.safer_substitute else None
            )
            would_change = count_substitutions(
                db_manager, args.book_id, old_translation, args.translation,
                chapter_ids, args.word_boundary
            )
            would_note = count_note_substitutions(
                db_manager, args.book_id, old_translation, args.translation,
                chapter_ids, args.word_boundary
            )
            if args.safer_substitute:
                print(f"[dry-run] {len(chapter_ids)} chapter(s) contain {args.untranslated!r} "
                      f"in their source; would substitute in {would_change} of them.")
            else:
                print(f"[dry-run] Would substitute in {would_change} chapter(s).")
            print(f"[dry-run] Would rewrite {would_note} entity note(s).")
        return

    update_entity_translation(db_manager, entity_id, args.translation, old_translation)
    print("✅ Entity translation updated.")

    if do_substitute:
        chapter_ids = (
            find_chapters_with_untranslated(db_manager, args.book_id, args.untranslated)
            if args.safer_substitute else None
        )
        affected, notes_affected = substitute_in_chapters(
            db_manager, args.book_id, old_translation, args.translation,
            chapter_ids, args.word_boundary
        )
        if args.safer_substitute:
            print(f"✅ Substituted across {affected} chapter(s) "
                  f"(restricted to {len(chapter_ids)} chapter(s) with "
                  f"{args.untranslated!r} in their source).")
        else:
            print(f"✅ Substituted across {affected} chapter(s).")
        print(f"✅ Rewrote {notes_affected} entity note(s).")


if __name__ == "__main__":
    main()
