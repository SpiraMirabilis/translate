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


def chapters_that_would_change(db_manager: DatabaseManager, book_id: int,
                                old_translation: str, new_translation: str,
                                chapter_ids=None,
                                word_boundary: bool = False) -> list:
    """Chapter numbers whose prose or title the substitution would alter.

    The read-only predicate behind count_substitutions (which see); the same
    rule substitute_in_chapters applies when it writes.
    """
    if not old_translation or old_translation == new_translation:
        return []
    pattern = build_substitution_pattern(old_translation, word_boundary)
    match_case = build_case_preserving_replacer(old_translation, new_translation)
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, chapter_number, translated_content, title FROM chapters "
            "WHERE book_id = ?",
            (book_id,),
        )
        numbers = []
        for r in cursor.fetchall():
            if chapter_ids is not None and r["id"] not in chapter_ids:
                continue
            title = r["title"] or ""
            if pattern.sub(match_case, title) != title:
                numbers.append(r["chapter_number"])
                continue
            try:
                content = json.loads(r["translated_content"])
            except (json.JSONDecodeError, TypeError):
                continue
            if any(pattern.sub(match_case, line) != line for line in content):
                numbers.append(r["chapter_number"])
    return numbers


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
    return len(chapters_that_would_change(
        db_manager, book_id, old_translation, new_translation,
        chapter_ids, word_boundary))


def substitute_in_chapters(db_manager: DatabaseManager, book_id: int,
                           old_translation: str, new_translation: str,
                           chapter_ids: dict = None,
                           word_boundary: bool = False,
                           affected_chapters: list = None) -> tuple:
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

    If `affected_chapters` is a list, the chapter number of every modified
    chapter is appended to it.
    """
    if not old_translation or old_translation == new_translation:
        return 0, 0

    pattern = build_substitution_pattern(old_translation, word_boundary)
    match_case = build_case_preserving_replacer(old_translation, new_translation)

    affected = 0
    with db_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, chapter_number, translated_content, title FROM chapters "
            "WHERE book_id = ?",
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
                if affected_chapters is not None:
                    affected_chapters.append(r["chapter_number"])

        notes_affected = db_manager.substitute_in_entity_notes(
            book_id, old_translation, new_translation,
            chapter_numbers=note_scope_for(chapter_ids),
            word_boundary=word_boundary,
            cursor=cursor,
        )

    return affected, notes_affected


SUBSTITUTE_MODES = ("none", "substitute", "safer")


def correct_entity(db_manager: DatabaseManager, book_id: int, untranslated: str,
                   translation: str, *, category: str = None, mode: str = "none",
                   word_boundary: bool = False, dry_run: bool = False) -> dict:
    """Correct one entity's translation, optionally sweeping the book's prose.

    `mode` is ``"none"`` (entity record only), ``"substitute"`` (book-wide
    prose/title/note sweep) or ``"safer"`` (the sweep restricted to chapters
    whose *source* mentions `untranslated` — the CLI's --safer-substitute).
    With `dry_run` nothing is written; the counts are what an apply run would
    report.

    Never prints and never raises for user errors — a missing entity, an
    ambiguous key (several categories and no `category`) or a bad mode come
    back as ``ok=False`` with ``error`` set and ``status`` naming the case:
    ``not_found`` | ``ambiguous`` | ``invalid_mode`` | ``invalid_translation``. A successful call has
    ``status`` ``updated`` | ``would_update`` | ``unchanged`` (the new
    translation already matches; nothing written, ``changed`` False).

    Keys: ok, error, status, entity_id, category, untranslated,
    old_translation, new_translation, mode, word_boundary, dry_run, changed,
    matches ([{id, category, translation}] of the candidate rows),
    chapters_scanned (chapters in the safer scope, None when book-wide or not
    substituting), chapter_substitutions, note_substitutions,
    chapters_affected (sorted chapter numbers changed / that would change).
    """
    result = {
        "ok": False, "error": None, "status": None,
        "entity_id": None, "category": category,
        "untranslated": untranslated,
        "old_translation": None, "new_translation": translation,
        "mode": mode, "word_boundary": word_boundary, "dry_run": dry_run,
        "changed": False, "matches": [],
        "chapters_scanned": None,
        "chapter_substitutions": 0, "note_substitutions": 0,
        "chapters_affected": [],
    }

    if mode not in SUBSTITUTE_MODES:
        result.update(status="invalid_mode",
                      error=f"Invalid mode {mode!r}; expected one of {list(SUBSTITUTE_MODES)}.")
        return result

    if not isinstance(translation, str) or not translation.strip():
        result.update(status="invalid_translation",
                      error="The new translation must be a non-empty string.")
        return result

    matches = find_entity(db_manager, book_id, untranslated)
    if category:
        matches = [m for m in matches if m[1] == category]
    result["matches"] = [{"id": eid, "category": cat, "translation": trans}
                         for eid, cat, trans in matches]

    if not matches:
        scope = f" in category '{category}'" if category else ""
        result.update(status="not_found",
                      error=f"No entity found for book_id={book_id}, "
                            f"untranslated={untranslated!r}{scope}.")
        return result

    if len(matches) > 1:
        cats = [m[1] for m in matches]
        result.update(status="ambiguous",
                      error=f"Found {len(matches)} entities matching {untranslated!r} "
                            f"in book {book_id} (categories: {cats}). "
                            f"Pass a category to disambiguate.")
        return result

    entity_id, found_category, old_translation = matches[0]
    result.update(entity_id=entity_id, category=found_category,
                  old_translation=old_translation)

    if old_translation == translation:
        result.update(ok=True, status="unchanged")
        return result

    result.update(ok=True, changed=True,
                  status="would_update" if dry_run else "updated")

    chapter_ids = None
    if mode == "safer":
        chapter_ids = find_chapters_with_untranslated(db_manager, book_id, untranslated)
        result["chapters_scanned"] = len(chapter_ids)

    if dry_run:
        if mode != "none":
            numbers = chapters_that_would_change(
                db_manager, book_id, old_translation, translation,
                chapter_ids, word_boundary)
            result["chapter_substitutions"] = len(numbers)
            result["chapters_affected"] = sorted(numbers)
            result["note_substitutions"] = count_note_substitutions(
                db_manager, book_id, old_translation, translation,
                chapter_ids, word_boundary)
        return result

    update_entity_translation(db_manager, entity_id, translation, old_translation)

    if mode != "none":
        numbers = []
        affected, notes_affected = substitute_in_chapters(
            db_manager, book_id, old_translation, translation,
            chapter_ids, word_boundary, affected_chapters=numbers)
        result["chapter_substitutions"] = affected
        result["note_substitutions"] = notes_affected
        result["chapters_affected"] = sorted(numbers)
        if affected:
            # The cached EPUB/AZW3 were built from the old prose.
            db_manager.invalidate_epub_cache(book_id)

    return result


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

    mode = ("safer" if args.safer_substitute
            else "substitute" if args.substitute else "none")

    config = TranslationConfig()
    logger = Logger(config)
    # strict_writes: a failed UPDATE raises loudly instead of returning None.
    db_manager = DatabaseManager(config, logger, strict_writes=True)

    r = correct_entity(
        db_manager, args.book_id, args.untranslated, args.translation,
        category=args.category, mode=mode,
        word_boundary=args.word_boundary, dry_run=args.dry_run,
    )

    if r["status"] == "ambiguous":
        print(f"Found {len(r['matches'])} entities matching {args.untranslated!r} "
              f"in book {args.book_id}:")
        for m in r["matches"]:
            print(f"  id={m['id']}  category={m['category']}  translation={m['translation']!r}")
        print("Pass --category to disambiguate.")
        sys.exit(1)
    if not r["ok"]:
        print(r["error"])
        sys.exit(1)

    print(f"Entity id={r['entity_id']} category={r['category']}")
    print(f"  Old translation: {r['old_translation']!r}")
    print(f"  New translation: {args.translation!r}")

    if r["status"] == "unchanged":
        print("New translation matches existing — nothing to update.")
        return

    if args.dry_run:
        print("[dry-run] Would update entity row.")
        if mode != "none":
            if mode == "safer":
                print(f"[dry-run] {r['chapters_scanned']} chapter(s) contain "
                      f"{args.untranslated!r} in their source; would substitute in "
                      f"{r['chapter_substitutions']} of them.")
            else:
                print(f"[dry-run] Would substitute in {r['chapter_substitutions']} chapter(s).")
            print(f"[dry-run] Would rewrite {r['note_substitutions']} entity note(s).")
        return

    print("✅ Entity translation updated.")
    if mode != "none":
        if mode == "safer":
            print(f"✅ Substituted across {r['chapter_substitutions']} chapter(s) "
                  f"(restricted to {r['chapters_scanned']} chapter(s) with "
                  f"{args.untranslated!r} in their source).")
        else:
            print(f"✅ Substituted across {r['chapter_substitutions']} chapter(s).")
        print(f"✅ Rewrote {r['note_substitutions']} entity note(s).")


if __name__ == "__main__":
    main()
