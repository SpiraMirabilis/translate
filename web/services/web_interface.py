"""
Web-based UserInterface implementation.

Runs the existing translation pipeline (from ui.py) in a background thread,
communicating with the frontend via the JobManager / WebSocket.
"""
import sys
import os
from urllib.parse import quote as _urlquote
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui import UserInterface
from typing import Dict, List, Optional


def _urlencode(s):
    return _urlquote(str(s), safe='')


class WebInterface(UserInterface):
    """
    Implements the UserInterface abstract class for the web GUI.

    Key differences from CLI:
    - get_input() returns pre-loaded text from job_manager (no argparse)
    - review_entities() pauses via threading.Event and waits for frontend
    - display_results() sends final output via WebSocket
    - progress_callback hooks into TranslationEngine chunk progress
    """

    def __init__(self, translator, entity_manager, logger, job_manager):
        super().__init__(translator, entity_manager, logger)
        self.job_manager = job_manager

        # Translation settings (can be overridden per-request)
        self.stream = True            # Streaming enabled — progress_callback fires every 10 tokens
        self.no_review = False        # Entity review enabled
        self.no_clean = False         # Auto-clean generic nouns before review
        self.no_repair = False        # Skip partial translation repair
        self.no_convert_units = False # Skip Chinese unit → metric conversion
        self.silent_notifications = True
        self.cleaning_model = None
        self.output_format = "text"
        self.book_info = None

        # Progress callback wired to job_manager
        self.progress_callback = self.job_manager.on_progress

        # JSON fix callback — pauses translation on parse failure
        self.json_fix_callback = self._handle_json_fix

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    def get_input(self) -> List[str]:
        """
        Return pre-loaded text from job_manager and set book context.
        Called once by run_translation() for a single-chapter job.
        """
        self.book_id = self.job_manager.book_id
        self.chapter_number = self.job_manager.chapter_number
        return self.job_manager.pending_text

    def display_results(self, results: dict, book_info=None) -> None:
        """Send the completed translation to the frontend via WebSocket."""
        content = results.get("content", [])
        # Ensure content is a list of strings
        if isinstance(content, str):
            content = content.splitlines()

        # Resolve book name for activity log
        book_name = None
        if self.job_manager.book_id:
            book = self.entity_manager.get_book(self.job_manager.book_id)
            if book:
                book_name = book.get("title")

        ch = results.get("chapter", 1)
        self.job_manager.send_message_sync({
            "type": "translation_complete",
            "content": content,
            "title": results.get("title", ""),
            "chapter": ch,
            "summary": results.get("summary", ""),
            "book_id": self.job_manager.book_id,
            "book_name": book_name,
        })
        self.job_manager.log_activity(
            type='complete',
            message=f'{book_name or "Translation"} — Chapter {ch} complete.',
            book_id=self.job_manager.book_id, chapter=ch, book_name=book_name,
        )
        summary = results.get("summary", "")
        if summary:
            self.job_manager.log_activity(type='info', message=f'Synopsis: {summary}')
        self.job_manager.last_result = results

    def review_entities(self, entities: Dict, untranslated_text) -> Dict:
        """
        Pause translation and send new entities to the frontend for review.
        Filters duplicates and existing entities, optionally auto-cleans generic
        (non-proper-noun) entities, then blocks until the user submits.
        """
        # Skip review entirely when no_review is set (e.g. auto-process batch jobs)
        if getattr(self, 'no_review', False):
            return {}

        # Filter out entities already in the DB and cross-category duplicates
        has_entities = any(entities.get(cat, {}) for cat in entities)
        if has_entities:
            self._filter_existing_entities(entities)
            has_entities = any(entities.get(cat, {}) for cat in entities)
            if not has_entities:
                return {}

        # Auto-clean non-proper nouns before review (unless disabled)
        if has_entities and not getattr(self, 'no_clean', False):
            self.job_manager.send_message_sync({
                "type": "progress",
                "phase": "cleaning",
                "chunk": 0,
                "total": 0,
            })
            cleaned_count = self._auto_clean_new_entities(entities)
            if cleaned_count > 0:
                self._log_cleaned_entities()
                has_entities = any(entities.get(cat, {}) for cat in entities)
                if not has_entities:
                    return {}

        serializable = _make_serializable(entities)

        # Final guard: if no entities remain after all filtering, skip review
        count = sum(len(v) for v in serializable.values() if isinstance(v, dict))
        if count == 0:
            return {}

        if isinstance(untranslated_text, list):
            context = "\n".join(untranslated_text)
        else:
            context = str(untranslated_text)

        self.job_manager.pending_review = {"entities": serializable, "context": context}
        self.job_manager.send_message_sync({
            "type": "entity_review_needed",
            "entities": serializable,
            "context": context,
        })

        self.job_manager.log_activity(
            type='entity_review',
            message=f'{count} new entit{"y" if count == 1 else "ies"} found — review required.',
        )

        # Block until user submits (or timeout)
        result = self.job_manager.wait_for_review()
        return result

    def _log_cleaned_entities(self):
        """Log cleaned (removed) entities to the activity log with add-entity links."""
        cleaned = getattr(self, '_cleaned_translations', {})
        cleaned_keys = getattr(self, '_cleaned_entity_keys', {})
        if not cleaned_keys:
            return

        book_id = self.job_manager.book_id
        entity_links = []
        for category, keys in cleaned_keys.items():
            for key in keys:
                translation = cleaned.get(key, '')
                params = f'add=1&untranslated={_urlencode(key)}&translation={_urlencode(translation)}&category={_urlencode(category)}'
                if book_id:
                    params += f'&book_id={book_id}'
                entity_links.append({
                    'name': key,
                    'label': f'{key} \u2192 {translation}' if translation else key,
                    'link': f'/entities?{params}',
                })

        self.job_manager.log_activity(
            type='entity_cleaned',
            message='Generic terms cleaned:',
            entities=entity_links,
        )

    def check_chapter_conflict(self, chapter_text):
        """
        If a chapter with this (book_id, chapter_number) already exists and
        its source text differs, pause translation and ask the user whether
        to overwrite or skip.

        Returns True to proceed (no conflict, identical retranslation, or user
        accepted), False if the user cancelled.
        """
        book_id = getattr(self, 'book_id', None)
        chapter_number = getattr(self, 'chapter_number', None)
        if not book_id or not isinstance(chapter_number, int) or chapter_number <= 0:
            return True

        try:
            existing = self.entity_manager.get_chapter(
                book_id=book_id, chapter_number=chapter_number
            )
        except Exception as e:
            self.logger.error(f"Chapter conflict check — get_chapter failed: {e}")
            return True

        if not existing:
            return True

        existing_untranslated = existing.get('untranslated') or []
        if isinstance(existing_untranslated, str):
            existing_untranslated = existing_untranslated.splitlines()

        new_untranslated = chapter_text or []
        if isinstance(new_untranslated, str):
            new_untranslated = new_untranslated.splitlines()

        def _normalise(lines):
            return [str(line).strip() for line in lines if str(line).strip()]

        if _normalise(existing_untranslated) == _normalise(new_untranslated):
            # Same source — legitimate retranslation, proceed silently.
            return True

        # Resolve book title for display
        book_title = None
        try:
            book = self.entity_manager.get_book(book_id=book_id)
            if book:
                book_title = book.get('title')
        except Exception:
            pass

        new_title = ''
        if isinstance(chapter_text, list) and chapter_text:
            first = str(chapter_text[0]).strip()
            new_title = first.lstrip('#').strip() if first.startswith('#') else first

        payload = {
            "book_id": book_id,
            "chapter_number": chapter_number,
            "book_title": book_title,
            "existing_title": existing.get('title') or '',
            "existing_untranslated": existing_untranslated,
            "new_title": new_title,
            "new_untranslated": new_untranslated,
        }
        self.job_manager.pending_chapter_conflict = payload
        self.job_manager.send_message_sync({
            "type": "chapter_conflict_needed",
            **payload,
        })
        self.job_manager.log_activity(
            type='info',
            message=(
                f'Chapter {chapter_number} of "{book_title or book_id}" already exists with different '
                f'source text — awaiting user decision.'
            ),
            book_id=book_id, chapter=chapter_number, book_name=book_title,
        )

        result = self.job_manager.wait_for_chapter_conflict()
        decision = (result or {}).get("decision", "cancel")
        return decision == "proceed"

    def _handle_json_fix(self, raw_response, chunk_index, total_chunks, chunk_text):
        """Pause translation and send malformed JSON to the frontend for fixing."""
        # Truncate source text for display
        display_text = chunk_text[:500] + ('…' if len(chunk_text) > 500 else '')

        payload = {
            "raw_response": raw_response or "",
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "chunk_text": display_text,
            "is_empty": not bool(raw_response and raw_response.strip()),
        }
        self.job_manager.pending_json_fix = payload
        self.job_manager.send_message_sync({
            "type": "json_fix_needed",
            **payload,
        })

        self.job_manager.log_activity(
            type='json_fix',
            message=f'JSON parse failed on chunk {chunk_index}/{total_chunks} — fix required.',
        )

        return self.job_manager.wait_for_json_fix()

    def _fix_partial_translations(self, content, source_language='zh'):
        """Override to send a progress message before running repair."""
        self.job_manager.send_message_sync({
            "type": "progress",
            "phase": "repairing",
            "chunk": 0,
            "total": 0,
        })
        return super()._fix_partial_translations(content, source_language)


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _make_serializable(obj):
    """Recursively convert an object to JSON-serializable form."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(i) for i in obj]
    return obj
