#!/usr/bin/env python3
"""
Import a pre-made translation JSON for a QUEUED chapter, as if the model API
had returned it.

The file must be the same JSON object the translation API is asked to produce:

    {
      "title": "The Sword in the Rain",
      "chapter": 101,
      "summary": "A concise summary of no more than 75 words.",
      "content": ["paragraph one", "", "paragraph two"],
      "entities": {
        "characters": {"张羽": {"translation": "Zhang Yu", "gender": "male"}},
        "places":     {"青云城": {"translation": "Azure Cloud City"}}
      }
    }

Only "content" is required. "entities" is optional (each entry needs a
"translation"; "gender"/"incorrect_translation"/"note"/"last_chapter" are
optional). The source text comes from the queue row, never from the file.

The chapter then goes through the exact post-translation pipeline a real run
uses (ui.UserInterface.run_translation): per-book translated-ingest modules,
chapter-prefix stripping, illustration-marker reconciliation, entity
persistence, save_chapter (which re-applies footnotes and trad→simp on the
source), and removal of the queue item.

Usage:
    python3 import_translation.py --book 30 --chapter 101 --file ch101.json
    python3 import_translation.py --book 30 --file ch101.json          # chapter from the JSON
    python3 import_translation.py --queue-id 4821 --file ch101.json
    python3 import_translation.py --book 30 --chapter 101 --file ch101.json --dry-run
    python3 import_translation.py --book 30 --chapter 101 --file ch101.json --draft --yes

Options of note:
    --dry-run      validate and report; touch nothing
    --overwrite    proceed when a chapter already exists at that number
    --draft        save the new chapter unpublished
    --keep-queue   leave the queue item in place (default: remove it, like a
                   completed translation)
    --yes          never prompt
"""

import argparse
import copy
import datetime
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
# One-shot CLI — keep the logger quiet unless --debug is passed.
_DEBUG = "--debug" in sys.argv
if not _DEBUG:
    os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from db.core import DEFAULT_CATEGORIES
from db.queue_repo import worker_identity
from logger import Logger
from translation_engine import TranslationEngine
from ui import UserInterface


# ----------------------------------------------------------------------
# Headless UI — the pipeline in ui.py drives this
# ----------------------------------------------------------------------

class ImportInterface(UserInterface):
    """Non-interactive UserInterface: source text is fixed, no entity review."""

    def __init__(self, translator, entity_manager, logger, *, chapter_lines,
                 book_id, chapter_number, chapter_title, save_as_draft=False):
        super().__init__(translator, entity_manager, logger)
        self._chapter_lines = list(chapter_lines)
        self._consumed = False
        self.book_id = book_id
        self.chapter_number = chapter_number
        self.chapter_title = chapter_title
        # Run flags: no streaming, no review, no AI auto-clean — everything the
        # import path would have to ask a human (or a model) about is skipped.
        self.stream = False
        self.no_review = True
        self.two_pass = False
        self.no_clean = True
        self.save_as_draft = save_as_draft
        self.cleaning_model = None
        self.result = None

    def get_input(self):
        if self._consumed:
            return []
        self._consumed = True
        return list(self._chapter_lines)

    def review_entities(self, entities, untranslated_text, phase='post', note_updates=None):
        # Accept the imported entities as-is (equivalent to a --no-review run).
        return {}

    def display_results(self, end_object, book_info=None):
        self.result = end_object


# ----------------------------------------------------------------------
# The stand-in for TranslationEngine.translate_chapter
# ----------------------------------------------------------------------

def make_import_translate_chapter(engine, payload, forced_chapter):
    """Return a translate_chapter replacement that yields `payload` instead of
    calling the model, mirroring what the real method returns."""

    def translate_chapter(chapter_text, book_id=None, chapter_number=None, **kwargs):
        em = engine.entity_manager

        # Same entity baseline the real method builds (a per-run snapshot, not
        # the shared em.entities cache).
        old_entities = em.get_entities_snapshot(book_id)
        categories = em.get_book_categories(book_id) if book_id else list(DEFAULT_CATEGORIES)
        for cat in categories:
            old_entities.setdefault(cat, {})
        real_old_entities = old_entities

        end_object = copy.deepcopy(payload)
        end_object.setdefault('entities', {})
        end_object.setdefault('content', [])

        current_chapter = forced_chapter or end_object.get('chapter') or chapter_number or 0
        end_object['chapter'] = current_chapter

        # combine_json_chunks stamps last_chapter on every entity; do the same.
        for ents in end_object['entities'].values():
            for data in ents.values():
                if not data.get('last_chapter'):
                    data['last_chapter'] = current_chapter

        totally_new_entities = em.find_new_entities(real_old_entities, end_object['entities'])
        old_entities = em.combine_json_entities(old_entities, end_object['entities'])

        engine.potential_duplicates = []
        engine._check_for_translation_duplicates(end_object['entities'])

        new_entities = {cat: end_object['entities'].get(cat, {}) for cat in categories}
        for cat, ents in end_object['entities'].items():
            new_entities.setdefault(cat, ents)

        end_object['content'] = engine.reconcile_illustration_markers(
            chapter_text, end_object.get('content', [])
        )

        # No token accounting: nothing was generated, so the book's token ratio
        # is deliberately left untouched.
        return {
            "end_object": end_object,
            "new_entities": new_entities,
            "totally_new_entities": totally_new_entities,
            "old_entities": old_entities,
            "real_old_entities": real_old_entities,
            "current_chapter": current_chapter,
            "total_char_count": sum(len(line) for line in chapter_text),
        }

    return translate_chapter


# ----------------------------------------------------------------------
# Loading / validation
# ----------------------------------------------------------------------

def load_payload(path):
    """Read and validate the translation JSON. Returns the normalized object."""
    with open(path, 'r', encoding='utf-8') as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as e:
            raise SystemExit(f"Error: {path} is not valid JSON — {e}")

    if not isinstance(data, dict):
        raise SystemExit("Error: the JSON root must be an object, like an API response.")

    content = data.get('content')
    if content is None:
        raise SystemExit("Error: missing required field 'content'.")
    if isinstance(content, str):
        content = content.splitlines()
    if not isinstance(content, list) or not all(isinstance(l, str) for l in content):
        raise SystemExit("Error: 'content' must be a list of strings (one per paragraph).")
    if not any(l.strip() for l in content):
        raise SystemExit("Error: 'content' has no non-empty lines.")
    data['content'] = content

    title = data.get('title')
    if title is not None and not isinstance(title, str):
        raise SystemExit("Error: 'title' must be a string.")

    summary = data.get('summary')
    if summary is not None and not isinstance(summary, str):
        raise SystemExit("Error: 'summary' must be a string.")

    ch = data.get('chapter')
    if ch is not None:
        try:
            data['chapter'] = int(ch)
        except (TypeError, ValueError):
            raise SystemExit(f"Error: 'chapter' must be an integer, got {ch!r}.")

    entities = data.get('entities') or {}
    if not isinstance(entities, dict):
        raise SystemExit("Error: 'entities' must be an object keyed by category.")
    for category, ents in entities.items():
        if not isinstance(ents, dict):
            raise SystemExit(f"Error: entities['{category}'] must be an object keyed by source term.")
        for key, val in ents.items():
            if not isinstance(val, dict):
                raise SystemExit(f"Error: entities['{category}']['{key}'] must be an object.")
            if not val.get('translation') or not isinstance(val['translation'], str):
                raise SystemExit(
                    f"Error: entities['{category}']['{key}'] needs a non-empty string 'translation'.")
    data['entities'] = entities

    return data


def resolve_book(db, book_arg):
    if book_arg is None:
        return None
    try:
        book = db.get_book(book_id=int(book_arg))
    except ValueError:
        book = db.get_book(title=book_arg)
    if not book:
        raise SystemExit(f"Error: no book matching {book_arg!r}.")
    return book


def find_queue_item(db, book_id, chapter_number, queue_id):
    """Locate the queue row to import into, with its content."""
    items = db.list_queue(book_id=book_id, include_content=True, include_processing=True)

    if queue_id is not None:
        matches = [i for i in items if i['id'] == queue_id]
        if not matches:
            raise SystemExit(f"Error: queue item {queue_id} not found"
                             + (f" for book {book_id}." if book_id else "."))
        return matches[0]

    if chapter_number is not None:
        matches = [i for i in items if i.get('chapter_number') == chapter_number]
        if not matches:
            raise SystemExit(
                f"Error: no queued chapter {chapter_number}"
                + (f" for book {book_id}." if book_id else ".")
                + " Use --queue-id, or check the queue.")
        if len(matches) > 1:
            ids = ", ".join(str(i['id']) for i in matches)
            raise SystemExit(f"Error: {len(matches)} queue items claim chapter {chapter_number} "
                             f"(ids: {ids}). Disambiguate with --queue-id.")
        return matches[0]

    if len(items) == 1:
        return items[0]
    if not items:
        raise SystemExit("Error: the queue is empty for that book.")
    raise SystemExit(
        f"Error: {len(items)} items queued and no chapter given. Pass --chapter or --queue-id, "
        "or put a \"chapter\" field in the JSON.")


def claim(db, queue_id):
    """Mark the row processing so a running translator can't take it too."""
    now = datetime.datetime.now().isoformat()
    with db._conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE queue SET status = 'processing', claimed_at = ?, claimed_by = ? "
            "WHERE id = ? AND (status IS NULL OR status = 'queued')",
            (now, worker_identity()[:64], queue_id),
        )
        return cursor.rowcount == 1


def confirm(prompt, assume_yes):
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise SystemExit(f"{prompt} — not a TTY; re-run with --yes to confirm.")
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


# ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import a translated chapter JSON for a queued chapter, "
                    "as if the model API had returned it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:", 1)[1] if "Usage:" in __doc__ else None,
    )
    parser.add_argument('--file', '-f', required=True, help="Translation JSON file")
    parser.add_argument('--book', '-b', help="Book ID or exact title")
    parser.add_argument('--chapter', '-c', type=int, help="Queued chapter number to import into")
    parser.add_argument('--queue-id', type=int, help="Target a specific queue row by id")
    parser.add_argument('--dry-run', action='store_true', help="Validate and report; change nothing")
    parser.add_argument('--overwrite', action='store_true',
                        help="Proceed without asking when the chapter already exists")
    parser.add_argument('--draft', action='store_true', help="Save the new chapter unpublished")
    parser.add_argument('--keep-queue', action='store_true',
                        help="Leave the queue item in place instead of removing it")
    parser.add_argument('--yes', '-y', action='store_true', help="Never prompt")
    parser.add_argument('--debug', action='store_true', help="Verbose logging")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"Error: file not found: {args.file}")
    if args.book is None and args.queue_id is None:
        raise SystemExit("Error: pass --book (and --chapter), or --queue-id.")

    payload = load_payload(args.file)

    config = TranslationConfig()
    logger = Logger(config)
    db = DatabaseManager(config, logger, strict_writes=True)

    book = resolve_book(db, args.book)
    book_id = book['id'] if book else None

    chapter_number = args.chapter
    if chapter_number is None and args.queue_id is None:
        chapter_number = payload.get('chapter')

    item = find_queue_item(db, book_id, chapter_number, args.queue_id)
    if book_id is None:
        book = db.get_book(book_id=item['book_id'])
        if not book:
            raise SystemExit(f"Error: queue item {item['id']} references missing book {item['book_id']}.")
        book_id = book['id']
    elif item['book_id'] != book_id:
        raise SystemExit(f"Error: queue item {item['id']} belongs to book {item['book_id']}, not {book_id}.")

    # The queue row's chapter number wins, exactly as it does in a real run.
    target_chapter = item.get('chapter_number') or chapter_number or payload.get('chapter')
    if not isinstance(target_chapter, int) or target_chapter <= 0:
        raise SystemExit(f"Error: queue item {item['id']} has no usable chapter number; "
                         "pass --chapter.")
    if payload.get('chapter') and payload['chapter'] != target_chapter:
        print(f"Note: JSON says chapter {payload['chapter']}, queue says {target_chapter} — "
              f"saving as {target_chapter}.")

    source_lines = item.get('content')
    if isinstance(source_lines, str):
        source_lines = source_lines.splitlines()
    if not source_lines:
        raise SystemExit(f"Error: queue item {item['id']} has no source content.")

    existing = db.get_chapter(book_id=book_id, chapter_number=target_chapter)
    entity_count = sum(len(e) for e in payload['entities'].values())

    print(f"Book:      {book['title']} (id {book_id})")
    print(f"Queue row: id {item['id']}, chapter {target_chapter}, status {item.get('status')}"
          f", title {item.get('title') or '—'}")
    print(f"Source:    {len(source_lines)} lines")
    print(f"Import:    {len(payload['content'])} translated lines, {entity_count} entities, "
          f"title {payload.get('title') or '—'}")
    if existing:
        print(f"⚠ Chapter {target_chapter} already exists (id {existing['id']}) — it will be overwritten.")

    if args.dry_run:
        print("\nDry run — nothing written, queue untouched.")
        return 0

    if item.get('status') == 'processing':
        if not confirm(f"Queue item {item['id']} is claimed by a worker "
                       f"({item.get('claimed_at')}); import anyway?", args.yes):
            raise SystemExit("Aborted.")
    if existing and not args.overwrite:
        if not confirm(f"Overwrite existing chapter {target_chapter}?", args.yes):
            raise SystemExit("Aborted.")

    claimed = claim(db, item['id'])
    if not claimed and item.get('status') != 'processing':
        raise SystemExit(f"Error: could not claim queue item {item['id']} — it changed underneath us.")

    engine = TranslationEngine(config, logger, db)
    engine.translate_chapter = make_import_translate_chapter(engine, payload, target_chapter)

    ui = ImportInterface(
        engine, db, logger,
        chapter_lines=source_lines,
        book_id=book_id,
        chapter_number=target_chapter,
        chapter_title=item.get('title'),
        save_as_draft=args.draft,
    )
    if not args.keep_queue:
        # run_translation removes the row once the chapter is saved.
        ui._current_queue_item = item

    try:
        result = ui.run_translation()
    except BaseException:
        db.release_queue_item(item['id'])
        raise

    if result is None:
        db.release_queue_item(item['id'])
        raise SystemExit("Error: import failed — the chapter was not saved.")

    saved = db.get_chapter(book_id=book_id, chapter_number=target_chapter)
    if not saved:
        db.release_queue_item(item['id'])
        raise SystemExit("Error: chapter was not found after saving — nothing committed.")

    if args.keep_queue:
        db.release_queue_item(item['id'])
        queue_note = "queue item kept (released back to 'queued')"
    else:
        queue_note = f"queue item {item['id']} removed"

    print(f"\n✅ Saved chapter {target_chapter} — \"{saved.get('title')}\" "
          f"({len(result.get('content', []))} lines); {queue_note}.")
    print(f"   {db.get_queue_count(book_id=book_id)} items still queued for this book.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
