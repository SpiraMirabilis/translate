"""
Web-based UserInterface implementation.

Runs the existing translation pipeline (from ui.py) in a background thread,
communicating with the frontend via the JobManager / WebSocket.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui import UserInterface
from typing import Dict, List, Optional


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
        self.silent_notifications = True
        self.cleaning_model = None
        self.output_format = "text"
        self.book_info = None

        # Progress callback wired to job_manager
        self.progress_callback = self.job_manager.on_progress

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

        self.job_manager.send_message_sync({
            "type": "translation_complete",
            "content": content,
            "title": results.get("title", ""),
            "chapter": results.get("chapter", 1),
            "summary": results.get("summary", ""),
        })
        self.job_manager.last_result = results

    def review_entities(self, entities: Dict, untranslated_text) -> Dict:
        """
        Pause translation and send new entities to the frontend for review.
        Filters duplicates and existing entities, optionally auto-cleans generic
        (non-proper-noun) entities, then blocks until the user submits.
        """
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
                has_entities = any(entities.get(cat, {}) for cat in entities)
                if not has_entities:
                    return {}

        serializable = _make_serializable(entities)

        if isinstance(untranslated_text, list):
            context = "\n".join(untranslated_text[:40])
        else:
            context = str(untranslated_text)[:2000]

        self.job_manager.send_message_sync({
            "type": "entity_review_needed",
            "entities": serializable,
            "context": context,
        })

        # Block until user submits (or timeout)
        result = self.job_manager.wait_for_review()
        return result


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
