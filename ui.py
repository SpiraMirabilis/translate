from typing import Dict, List, Optional, Any, Union, Tuple
from abc import ABC, abstractmethod
from database import DatabaseManager
from logger import Logger
from modules import apply_source_ingest, apply_translated_ingest
from translation_engine import TranslationEngine, TranslationCancelled
from strip_chapter_prefix_titles import strip_chapter_prefix
import datetime
import json
import re
import sqlite3

class UserInterface(ABC):
    """Abstract base class for different user interfaces"""
    
    def __init__(self, translator: TranslationEngine, entity_manager: DatabaseManager, logger: Logger):
        self.translator = translator
        self.entity_manager = entity_manager
        self.logger = logger
    
    @abstractmethod
    def get_input(self) -> List[str]:
        """Get input text from the user interface"""
        pass
    
    @abstractmethod
    def display_results(self, results: Dict, book_info=None) -> None:
        """Display the translation results to the user"""
        pass
    
    @abstractmethod
    def review_entities(self, entities: Dict, untranslated_text: List[str], phase: str = 'post',
                        note_updates: List[Dict] = None) -> Dict:
        """Allow the user to review and edit entities.

        `phase` is 'post' (default — review after the chapter is translated, single-pass)
        or 'pre' (review before chapter prose is translated, two-pass mode). Subclasses
        may use phase to label the UI appropriately; the data shape is identical.

        `note_updates` carries the model's proposed revisions to the notes and
        genders of entities the book already knows (see
        TranslationEngine.validate_note_updates). The return value stays "edited
        entities" as it always was; approved updates are left on
        `self.reviewed_note_updates` for the caller to apply, so this contract and
        every consumer of the return value are unchanged.
        """
        pass

    def _write_entity_note(self, entity_id, existing_note, incoming_note, *,
                           human_edited, chapter_number, cursor=None):
        """Apply the note that came in on the *entities* channel, if it may.

        Precedence (unchanged in behaviour, moved out of SQL so the write can be
        recorded in entity_note_revisions):
          - no incoming note        -> nothing happens; omitting one never clears.
          - human-edited in review  -> the reviewer's note wins outright.
          - model-volunteered       -> written only when there is no note yet.
            An existing note is never clobbered here: the model re-emits known
            entities routinely, and revising a known entity's note is what the
            separate note_updates channel is for.

        Returns the revision id when a write happened, else None.
        """
        if incoming_note is None or not str(incoming_note).strip():
            return None
        if not human_edited and (existing_note or "").strip():
            return None
        return self.entity_manager.set_entity_note(
            entity_id, incoming_note,
            author='human' if human_edited else 'model',
            chapter_number=chapter_number, cursor=cursor)

    def _write_entity_gender(self, entity_id, existing_gender, incoming_gender, *,
                             human_edited, chapter_number, cursor=None):
        """Apply the gender that came in on the *entities* channel, if it may.

        The same precedence as _write_entity_note, for the same reason:
          - no incoming gender     -> nothing happens; omitting one never clears.
          - human-edited in review -> the reviewer's choice wins outright.
          - model-volunteered      -> written only when none is recorded yet.
            The model re-emits known entities every chapter, so letting it
            overwrite here would make a single bad guess silently flip a
            character's pronouns for the rest of the book. Revising a known
            entity's gender is what the note_updates channel is for, where the
            change is deliberate, reviewable and recorded.

        Returns the revision id when a write happened, else None.
        """
        if incoming_gender is None or not str(incoming_gender).strip():
            return None
        if not human_edited and (existing_gender or "").strip():
            return None
        return self.entity_manager.set_entity_gender(
            entity_id, incoming_gender,
            author='human' if human_edited else 'model',
            chapter_number=chapter_number, cursor=cursor)

    def _apply_note_updates(self, updates: List[Dict], chapter_number=None, author='model'):
        """Write approved revisions through the repo's note/gender choke points.

        One update may carry a new note, a corrected gender, or both — they ride
        the same channel because they usually move together (the chapter that
        reveals who someone is fixes the pronouns too). Each write snapshots the
        value it replaces (entity_note_revisions / entity_gender_revisions), so
        a change that turns out to be wrong is one Revert away on the Entities
        page. Failures are logged and skipped — neither is worth losing a
        translated chapter over.

        Returns the number of entities on which something was written.
        """
        if not updates:
            return 0
        applied = 0
        for upd in updates:
            try:
                entity_id = upd.get("entity_id") or self.entity_manager.get_entity_id(
                    self.book_id, upd["untranslated"], upd.get("category"))
                if not entity_id:
                    self.logger.warning(
                        f"note update: entity '{upd.get('untranslated')}' not found for "
                        f"book {self.book_id} — skipped")
                    continue
                touched = False
                if upd.get("new_note"):
                    revision_id = self.entity_manager.set_entity_note(
                        entity_id, upd["new_note"],
                        author=upd.get("author", author),
                        chapter_number=upd.get("chapter_number", chapter_number),
                        reason=upd.get("reason") or None,
                        shrink=bool(upd.get("shrink")),
                    )
                    if revision_id:
                        touched = True
                        self.logger.info(
                            f"note updated for '{upd['untranslated']}' "
                            f"({upd.get('translation', '')}): {upd['new_note'][:80]}")
                if upd.get("new_gender"):
                    revision_id = self.entity_manager.set_entity_gender(
                        entity_id, upd["new_gender"],
                        author=upd.get("author", author),
                        chapter_number=upd.get("chapter_number", chapter_number),
                        reason=upd.get("reason") or None,
                    )
                    if revision_id:
                        touched = True
                        self.logger.info(
                            f"gender corrected for '{upd['untranslated']}' "
                            f"({upd.get('translation', '')}): "
                            f"{upd.get('old_gender') or '(unset)'} → {upd['new_gender']}")
                if touched:
                    applied += 1
            except Exception as e:
                self.logger.error(f"Failed to apply note update for "
                                  f"{upd.get('untranslated')}: {e}")
        return applied

    def _store_footnote_candidates(self, chapter_number, chapter_title, chapter_text):
        """Persist candidates this translation pass collected (scan_mode
        "translation"), and record the chapter as scanned.

        Runs after save_chapter because ``chapter_number`` is only settled
        there. Reviewed rows survive: unlike the on-ingest scan this path fires
        on every retranslation, and a keep/reject decision must outlive one.
        Every failure is swallowed — a footnote suggestion is never worth losing
        a translated chapter over.
        """
        found = getattr(self, '_pending_footnote_candidates', None)
        self._pending_footnote_candidates = None  # one-shot, never leak to the next item
        if found is None or not getattr(self, 'book_id', None):
            # No channel in this run (book scans on ingest, or the model never
            # emitted the key). Deliberately leaves NO scan row, so a later
            # footnote_scan.py sweep still treats the chapter as unscanned.
            return 0
        try:
            from footnote_scan_core import persist_inline_candidates
            kept = persist_inline_candidates(
                self.entity_manager, self.book_id, chapter_number, chapter_title,
                self.entity_manager.config.translation_model,
                "\n".join(chapter_text or []), found)
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Failed to store footnote candidates for chapter "
                              f"{chapter_number}: {e}")
            return 0
        if kept:
            self.logger.info(f"footnote_candidates: stored {len(kept)} for chapter "
                             f"{chapter_number}")
            try:
                from modules.activity import log_module_activity
                book = self.entity_manager.get_book(book_id=self.book_id)
                terms = ", ".join(c["term_en"] or c["term_zh"] for c in kept)
                log_module_activity(
                    self.entity_manager, "info",
                    f"Footnote scan found {len(kept)} candidate(s) in chapter "
                    f"{chapter_number} of "
                    f"{(book or {}).get('title') or f'book {self.book_id}'}: {terms}",
                    self.book_id)
            except Exception:  # noqa: BLE001 - an activity line is cosmetic
                pass
        return len(kept)

    def check_chapter_conflict(self, chapter_text: List[str]) -> bool:
        """
        Pre-translation guard: when an existing chapter has the same
        (book_id, chapter_number), decide whether to proceed.

        Returns True to proceed (legit retranslation, or user opted to overwrite),
        False to abort the current item.

        Default base behaviour: proceed silently — only the WebInterface
        subclass surfaces this to the user.
        """
        return True

    def run_translation(self):
        """Run the translation process from start to finish"""
        try:
            # Store for queue management
            self._current_queue = None
            end_object = None

            # are we resuming?
            while True:
                # Get input
                chapter_text = self.get_input()
                if not chapter_text:
                    self.logger.info("No more text to process. Exiting.")
                    break

                self.logger.debug(f"Book ID: {self.book_id}, Book Title: {getattr(self, 'book_title', 'Unknown')}")
                self.logger.debug(f"Chapter Number: {getattr(self, 'chapter_number', 'None')}")

                # Check for book ID and create default if needed
                if not hasattr(self, 'book_id') or self.book_id is None:
                    # Look for a default book
                    default_book = self.entity_manager.get_book(title="Default Book")
                    
                    if default_book:
                        self.book_id = default_book["id"]
                        self.book_title = default_book["title"]
                        self.logger.info(f"Using existing Default Book (ID: {self.book_id})")
                    else:
                        # Create a default book
                        self.book_id = self.entity_manager.create_book(
                            "Default Book", 
                            author="Translator",
                            description="Default book for translations without a specified book ID"
                        )
                        if self.book_id:
                            self.book_title = "Default Book"
                            self.logger.info(f"Created Default Book (ID: {self.book_id}) for this translation")
                        else:
                            self.logger.info("Warning: Failed to create default book, chapter will not be saved to database")

                # Per-book module source transforms (e.g. novel543/twkan boilerplate
                # strip). Mirrors the queue-ingest path in database.save_chapter so a
                # manually-pasted chapter is "ingested" through the modules before it
                # is translated. Idempotent, so a later save_chapter re-application is
                # harmless. Runs before the chapter-conflict check below so the source
                # is compared transformed-vs-transformed.
                if getattr(self, 'book_id', None) is not None:
                    book_for_modules = self.entity_manager.get_book(book_id=self.book_id)
                    if book_for_modules:
                        chapter_text = apply_source_ingest(
                            book_for_modules, chapter_text,
                            self.translator.config, self.logger,
                            db=self.entity_manager,
                            chapter_number=getattr(self, 'chapter_number', None),
                        )

                # Pre-translation guard: if a chapter with this
                # (book_id, chapter_number) already exists and the source text
                # differs, ask the user whether to overwrite or skip.
                chapter_num_for_check = getattr(self, 'chapter_number', None)
                if (self.book_id is not None
                        and isinstance(chapter_num_for_check, int)
                        and chapter_num_for_check > 0):
                    if not self.check_chapter_conflict(chapter_text):
                        self.logger.info(
                            f"Skipping chapter {chapter_num_for_check} for book {self.book_id} "
                            f"— user cancelled chapter-conflict prompt."
                        )
                        # Drop the queue item (if any) so auto-process advances
                        # to the next item instead of re-fetching this one.
                        if hasattr(self, '_current_queue_item') and self._current_queue_item:
                            try:
                                self.entity_manager.remove_from_queue(self._current_queue_item['id'])
                            except Exception as e:
                                self.logger.error(f"Failed to remove cancelled queue item: {e}")
                        break

                # Two-pass mode: identify and review entities BEFORE translating the prose.
                # Only active when entity review is also on — they're mutually exclusive
                # by UI design; the backend defensively forces two_pass off when no_review on.
                two_pass = bool(getattr(self, 'two_pass', False)) and not bool(getattr(self, 'no_review', False))
                pass2_only = False
                if two_pass:
                    self.logger.info("Two-pass mode: running entity extraction pass before translation")
                    pre_extract_failed = False
                    try:
                        (pre_entities, pre_note_updates,
                         pre_footnote_candidates) = self.translator.extract_entities(
                            chapter_text,
                            book_id=getattr(self, 'book_id', None),
                            chapter_number=getattr(self, 'chapter_number', None),
                            progress_callback=getattr(self, 'progress_callback', None),
                            retranslation_reason=getattr(self, 'retranslation_reason', None),
                            should_cancel=getattr(self, 'should_cancel', None),
                            return_note_updates=True,
                        )
                    except TranslationCancelled:
                        raise
                    except Exception as e:
                        self.logger.error(f"Two-pass entity extraction failed: {e}. Falling back to single-pass.")
                        pre_entities = None
                        pre_note_updates = []
                        pre_footnote_candidates = []
                        pre_extract_failed = True

                    if pre_extract_failed:
                        # Fall through to standard single-pass — user still gets post-translation review.
                        pass2_only = False
                    else:
                        self.reviewed_note_updates = list(pre_note_updates or [])
                        if (pre_entities and any(v for v in pre_entities.values())) or pre_note_updates:
                            edited_pre = self.review_entities(pre_entities or {}, chapter_text,
                                                              phase='pre',
                                                              note_updates=pre_note_updates)
                        else:
                            edited_pre = {}

                        # Persist approved entities BEFORE pass 2 builds its prompt so the
                        # pre-translated entities block contains the user's chosen names.
                        self._save_reviewed_entities(pre_entities or {}, edited_pre or {})
                        # Same reason for note revisions: pass 2 must be given the
                        # corrected note, not the one this pass just superseded.
                        self._apply_note_updates(getattr(self, 'reviewed_note_updates', []),
                                                 getattr(self, 'chapter_number', None))
                        self.reviewed_note_updates = []
                        # Pass 2 is translate-only and carries no channel for
                        # them, so pass 1's candidates are the chapter's.
                        self._pending_footnote_candidates = list(
                            pre_footnote_candidates or [])
                        pass2_only = True

                # Perform translation
                stream = getattr(self,'stream', False)
                self.logger.debug(f"Stream mode is {stream}")
                translation_results = self.translator.translate_chapter(
                    chapter_text,
                    book_id=getattr(self, 'book_id', None),
                    stream=getattr(self, 'stream', False),
                    progress_callback=getattr(self, 'progress_callback', None),
                    chapter_number=getattr(self, 'chapter_number', None),
                    json_fix_callback=getattr(self, 'json_fix_callback', None),
                    retranslation_reason=getattr(self, 'retranslation_reason', None),
                    pass2_only=pass2_only,
                    chapter_title=getattr(self, 'chapter_title', None),
                    should_cancel=getattr(self, 'should_cancel', None),
                )

                if translation_results is None:
                    self.logger.error("Translation process failed - translation_results is None")
                    return None
                
                self.logger.debug("--- Entity handling debug ---")
                for category, entities in translation_results["new_entities"].items():
                    for key, value in entities.items():
                        self.logger.debug(f"New entity: {category}/{key}")
                    
                # Allow entity review if new entities were found
                totally_new_entities = translation_results["totally_new_entities"]
                end_object = translation_results["end_object"]
                new_entities = translation_results["new_entities"]
                old_entities = translation_results["old_entities"]
                real_old_entities = translation_results["real_old_entities"]
                current_chapter = translation_results["current_chapter"]
                total_char_count = translation_results["total_char_count"]

                # Prefer user-provided chapter number over the LLM's guess
                if hasattr(self, 'chapter_number') and self.chapter_number and isinstance(self.chapter_number, int) and self.chapter_number > 0:
                    current_chapter = self.chapter_number

                # Handle potential duplicate entities across categories if there are any
                if hasattr(self.translator, 'potential_duplicates') and self.translator.potential_duplicates:
                    resolved_duplicates = self.resolve_duplicate_entities(self.translator.potential_duplicates, chapter_text)
                    
                    # Process each resolved duplicate
                    for duplicate in resolved_duplicates:
                        if duplicate.get('decision') == 'move_to_new':
                            # Entity was moved, update end_object to reflect this
                            untranslated = duplicate['untranslated']
                            new_category = duplicate['new_category']
                            existing_category = duplicate['existing_category']
                            
                            # Remove from old category in end_object if present
                            if existing_category in end_object['entities'] and untranslated in end_object['entities'][existing_category]:
                                entity_data = end_object['entities'][existing_category].pop(untranslated)
                                
                                # Add to new category
                                if new_category not in end_object['entities']:
                                    end_object['entities'][new_category] = {}
                                end_object['entities'][new_category][untranslated] = entity_data
                        
                        elif duplicate.get('decision') == 'allow_duplicate':
                            # Add to end_object in new category
                            untranslated = duplicate['untranslated']
                            new_category = duplicate['new_category']
                            translation = duplicate['translation']
                            
                            if new_category not in end_object['entities']:
                                end_object['entities'][new_category] = {}
                            
                            end_object['entities'][new_category][untranslated] = {
                                "translation": translation,
                                "last_chapter": current_chapter
                            }
                
                # Proposed revisions to notes on entities this book already knows.
                # Approved (or, with review off, simply validated) updates are applied
                # after the entity save below.
                note_updates = translation_results.get("note_updates") or []
                self.reviewed_note_updates = list(note_updates)

                # Footnote candidates collected by this pass (scan_mode
                # "translation"). Held until after save_chapter, which is where
                # the chapter number is finally settled — it can be
                # model-detected, and these rows are keyed by it. In two-pass
                # mode pass 1 already produced them.
                # None is meaningful here and must not be flattened to []:
                # it means the model never opened the channel, which is not the
                # same as looking and finding nothing.
                self._pending_footnote_candidates = (
                    getattr(self, '_pending_footnote_candidates', None)
                    if pass2_only else
                    translation_results.get("footnote_candidates"))

                # Continue with regular entity review (skipped in two-pass mode —
                # entities were already reviewed and persisted before pass 2 ran).
                # A chapter that only proposes note changes still opens review.
                if not pass2_only and (any(v for v in totally_new_entities.values()) or note_updates):
                    edited_entities = self.review_entities(totally_new_entities, chapter_text,
                                                            note_updates=note_updates)
                else:
                    edited_entities = {}
                
                # Remove auto-cleaned generic entities from end_object so they are not saved to the database
                if hasattr(self, '_cleaned_entity_keys'):
                    for category, keys in self._cleaned_entity_keys.items():
                        for key in keys:
                            end_object['entities'].get(category, {}).pop(key, None)

                # Lowercase any capitalised generic terms that were auto-cleaned
                end_object['content'] = self._decase_cleaned_entities(end_object['content'])

                # Per-book module transforms of the translated text (partial-translation
                # repair, unit conversion, spacing, …). Fired here at the post-translation
                # point (not save_chapter) so each runs once per fresh translation with the
                # per-run cleaning model, and the optional AI filters aren't invoked on every
                # manual chapter edit. Enablement is per book (modules), so the model is
                # passed for model-based auto-rules (e.g. partial_repair auto-on for DeepSeek).
                _config = self.entity_manager.config
                _book = self.entity_manager.get_book(self.book_id) if getattr(self, 'book_id', None) else None
                end_object['content'] = apply_translated_ingest(
                    _book, end_object['content'], _config, self.logger,
                    cleaning_model=getattr(self, 'cleaning_model', None),
                    model=_config.translation_model,
                    chapter_number=getattr(self, 'chapter_number', None))

                # Strip "Chapter N" prefix from translated titles (raw sources usually
                # carry it through, but we store titles bare).
                if end_object.get('title'):
                    end_object['title'] = strip_chapter_prefix(end_object['title'])

                # Apply entity edits to the translation
                if edited_entities:
                    # Process edited entities
                    for category, entities in edited_entities.items():
                        for key, value in list(entities.items()):
                            # Ensure value is a dictionary before accessing its keys
                            if isinstance(value, dict) and value.get("deleted", False):
                                # Remove from end_object if marked as deleted.
                                # Deleted entries are keyed under their original category
                                # (the frontend preserves originalCategory for deletions).
                                end_object['entities'].get(category, {}).pop(key, None)
                            else:
                                # If user changed the entity's category during review,
                                # move it in end_object so the bulk save below writes
                                # the new category instead of the original one.
                                original_category = value.get("original_category")
                                if original_category and original_category != category:
                                    moved = end_object['entities'].get(original_category, {}).pop(key, None)
                                    if moved is not None:
                                        end_object['entities'].setdefault(category, {})[key] = moved

                                # Update translations for non-deleted entities
                                node = value.copy()
                                end_object['content'] = self.entity_manager.update_translated_text(end_object['content'], node)

                                # Update the entity in the SQLite database
                                # Update existing entity or add a new one
                                translation = node.get("translation", "")
                                last_chapter = current_chapter
                                incorrect_translation = node.get("incorrect_translation", None)
                                gender = node.get("gender", None)
                                note = node.get("note", None)

                                # Check if this entity already exists in another category
                                result = self.entity_manager.add_entity(
                                    category,
                                    key,
                                    translation,
                                    book_id=getattr(self, 'book_id', None),
                                    last_chapter=last_chapter,
                                    incorrect_translation=incorrect_translation,
                                    gender=gender,
                                    note=note,
                                    note_author='human',
                                    note_chapter=current_chapter,
                                )

                                # Update end_object so direct SQL save stays consistent
                                if category in end_object['entities'] and key in end_object['entities'][category]:
                                    end_object['entities'][category][key]['translation'] = translation
                                    if incorrect_translation:
                                        end_object['entities'][category][key]['incorrect_translation'] = incorrect_translation
                                    if gender:
                                        end_object['entities'][category][key]['gender'] = gender

                                if not result:
                                    self.logger.warning(f"Failed to add entity '{key}' to '{category}' - may already exist elsewhere")
                
                # last_chapter is stamped from the chapter we resolved, never taken
                # from the response: the contract stopped asking for it (it was a
                # field the model could only copy back), and review edits don't
                # carry it either. Covers entities added by duplicate resolution
                # and by the review handshake, both of which bypass the engine's
                # own stamping pass.
                for _cat_entities in end_object['entities'].values():
                    if isinstance(_cat_entities, dict):
                        for _entity_value in _cat_entities.values():
                            if isinstance(_entity_value, dict):
                                _entity_value["last_chapter"] = current_chapter
                

                # Save updated entities
                #self.entity_manager.save_entities()

                # Build set of entities that are new or were edited during review.
                # Only these should have their translation/category/gender overwritten;
                # pre-existing entities just get last_chapter bumped so we don't
                # clobber edits made via the /entities page while translation was running.
                new_or_edited_keys = set()  # (category, untranslated) tuples
                # Entities a human touched during review. Only these may overwrite an
                # existing note; a note the model volunteered must never clobber a
                # curated one (the model re-emits already-known entities routinely).
                human_edited_keys = set()
                for cat, ents in totally_new_entities.items():
                    for key in ents:
                        new_or_edited_keys.add((cat, key))
                if edited_entities:
                    for cat, ents in edited_entities.items():
                        for key, value in ents.items():
                            if isinstance(value, dict) and not value.get("deleted", False):
                                new_or_edited_keys.add((cat, key))
                                human_edited_keys.add((cat, key))

                # Save entities directly to database to avoid duplication
                self.logger.debug("--- Direct entity saving ---")
                try:
                    conn = self.entity_manager.get_connection()
                    cursor = conn.cursor()

                    # Process each entity from end_object
                    for category in end_object['entities']:

                        for key, entity_data in end_object['entities'][category].items():
                            translation = entity_data.get("translation", "")
                            last_chapter = entity_data.get("last_chapter") or current_chapter
                            incorrect_translation = entity_data.get("incorrect_translation", None)
                            gender = entity_data.get("gender", None)
                            note = entity_data.get("note", None)
                            is_new_or_edited = (category, key) in new_or_edited_keys

                            # Check if entity exists with this book_id
                            cursor.execute('''
                            SELECT id, note, gender FROM entities
                            WHERE untranslated = ? AND book_id = ?
                            ''', (key, self.book_id))

                            existing = cursor.fetchone()

                            if existing:
                                entity_id, existing_note, existing_gender = existing[0], existing[1], existing[2]
                                if is_new_or_edited:
                                    # New entity from LLM or edited during review — full update.
                                    # Neither the note nor the gender column is written here:
                                    # both go through their choke points below so the change
                                    # is recorded (entity_note_revisions / entity_gender_revisions)
                                    # and a model re-emitting a known entity cannot clobber
                                    # what is already recorded.
                                    cursor.execute('''
                                    UPDATE entities
                                    SET category = ?, translation = ?, last_chapter = ?, incorrect_translation = ?,
                                        origin_chapter = COALESCE(origin_chapter, ?)
                                    WHERE id = ?
                                    ''', (category, translation, last_chapter, incorrect_translation, current_chapter, entity_id))
                                    self.logger.debug(f"Updated entity {key} ({translation}) in category {category} with book_id={self.book_id}")
                                    self._write_entity_note(
                                        entity_id, existing_note, note,
                                        human_edited=(category, key) in human_edited_keys,
                                        chapter_number=current_chapter, cursor=cursor)
                                    self._write_entity_gender(
                                        entity_id, existing_gender, gender,
                                        human_edited=(category, key) in human_edited_keys,
                                        chapter_number=current_chapter, cursor=cursor)
                                else:
                                    # Pre-existing entity — only bump last_chapter to avoid
                                    # overwriting edits made while translation was running.
                                    # Its note is left alone too; the note_updates channel is
                                    # the sanctioned way to revise a known entity's note.
                                    cursor.execute('''
                                    UPDATE entities
                                    SET last_chapter = ?
                                    WHERE id = ?
                                    ''', (last_chapter, entity_id))
                                    self.logger.debug(f"Bumped last_chapter for existing entity {key} in category {category}")
                            else:
                                # Insert new — record origin_chapter. The note follows
                                # separately so its creation is a revision like any change.
                                cursor.execute('''
                                INSERT INTO entities
                                (category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id, origin_chapter)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (category, key, translation, last_chapter, incorrect_translation, gender, self.book_id, current_chapter))
                                self._write_entity_note(
                                    cursor.lastrowid, None, note,
                                    human_edited=(category, key) in human_edited_keys,
                                    chapter_number=current_chapter, cursor=cursor)
                                self.logger.debug(f"Added entity {key} ({translation}) to category {category} with book_id={self.book_id}")

                    conn.commit()
                    conn.close()
                    self.logger.info("Entities saved to database successfully")
                except Exception as e:
                    self.logger.error(f"Error saving entities to database: {e}")

                # Note revisions on entities that already existed. These ride their own
                # channel (the entity save above still never lets a volunteered note
                # clobber a curated one) and are applied here, after the entity rows are
                # settled. With review on, this list is what the user approved; with
                # review off, it is what validate_note_updates accepted — either way the
                # replaced note is snapshotted and revertible.
                applied_notes = self._apply_note_updates(
                    getattr(self, 'reviewed_note_updates', []), current_chapter)
                if applied_notes:
                    self.logger.info(f"Applied {applied_notes} entity note revision(s)")
                self.reviewed_note_updates = []
                
                # Incremental "Append & retranslate" merge: only the new segment was
                # translated above. Prepend the existing chapter's already-translated
                # lines (stashed in check_chapter_conflict) so the saved chapter is the
                # full combined source + translation, without re-translating the old part.
                merge_prefix = getattr(self, '_merge_prefix', None)
                self._merge_prefix = None  # one-shot — never leak to the next queue item
                if merge_prefix:
                    chapter_text = list(merge_prefix["untranslated"]) + list(chapter_text)
                    end_object['content'] = list(merge_prefix["translated"]) + list(end_object.get('content') or [])
                    # Keep the existing chapter's title (matches the old full-merge
                    # behaviour, where the merged first line was the existing heading).
                    if merge_prefix["title"]:
                        end_object['title'] = merge_prefix["title"]
                    # Concatenate summaries — the new summary only covers the appended part.
                    new_summary = (end_object.get('summary') or '').strip()
                    old_summary = (merge_prefix["summary"] or '').strip()
                    end_object['summary'] = (
                        (old_summary + ' ' + new_summary).strip() if old_summary else new_summary
                    )

                # Add original text to output
                end_object['untranslated'] = chapter_text

                # Belt-and-suspenders: re-assert illustration markers after the
                # post-translation passes (decase / partial-fix / unit-convert /
                # entity application) and the merge-prefix prepend, in case any of
                # them dropped a marker line. Idempotent — a no-op when the
                # markers already match (the common case).
                try:
                    end_object['content'] = self.translator.reconcile_illustration_markers(
                        chapter_text, end_object.get('content', [])
                    )
                except Exception as e:
                    self.logger.error(f"Illustration marker re-assert skipped: {e}")


                self.logger.debug(f"About to save chapter with book_id={self.book_id}, chapter_number={getattr(self, 'chapter_number', 'None')}")
                self.logger.debug(f"Current chapter from translation: {current_chapter}")
                
                # If book_id and chapter_number are set, save as a book chapter
                if hasattr(self, 'book_id') and self.book_id is not None:
                    # The user/queue-supplied chapter number wins; the model's
                    # self-reported number is only a fallback. (A raw whose text
                    # says "Chapter 12" but was queued as 13 used to save into —
                    # and overwrite — slot 12, while the conflict guard had
                    # checked slot 13.)
                    chapter_number = getattr(self, 'chapter_number', None)
                    if not isinstance(chapter_number, int) or chapter_number <= 0:
                        chapter_number = end_object.get('chapter')
                        if not chapter_number or not isinstance(chapter_number, int) or chapter_number <= 0:
                            chapter_number = 1
                            self.logger.warning(f"Invalid chapter number in translation results, using {chapter_number}")
                        else:
                            self.logger.info(f"No chapter number supplied; using model-detected {chapter_number}")
                    
                    # Save chapter to database. publish: the save_as_draft run
                    # option forces a draft; otherwise save_chapter's default
                    # applies (publish now, unless the book is an original work).
                    # Only affects newly created chapters — retranslations of
                    # existing chapters never change publish state.
                    chapter_id = self.entity_manager.save_chapter(
                        self.book_id,
                        chapter_number,
                        end_object.get('title', f'Chapter {chapter_number}'),
                        chapter_text,  # untranslated content
                        end_object.get('content', []),  # translated content
                        summary=end_object.get('summary', ''),
                        translation_model=self.translator.config.translation_model,
                        publish=False if getattr(self, 'save_as_draft', False) else None
                    )
                    
                    if chapter_id:
                        print(f"Saved as Chapter {chapter_number} of Book ID {self.book_id}")
                        self._store_footnote_candidates(
                            chapter_number, end_object.get('title'), chapter_text)
                    else:
                        # The save failed, so there is no chapter to hang them
                        # on. Drop them rather than let them ride to whatever
                        # queue item comes next.
                        self._pending_footnote_candidates = None
                        # (Entity saves — new, edited, and pre-existing — were all
                        # handled by the direct-SQL block above; a second add_entity
                        # loop here used to redundantly re-save a hardcoded category
                        # subset.)

                # In run_translation method, when calling display_results:
                if hasattr(self, 'book_id') and self.book_id is not None:
                    # Get book info for output
                    book = self.entity_manager.get_book(book_id=self.book_id)
                    if book:
                        book_info = {
                            "title": book["title"],
                            "author": book["author"] or "Translator",
                            "language": book["language"] or "en"
                        }
                    else:
                        book_info = None
                else:
                    book_info = None
                        
                # Display results
                self.display_results(end_object, book_info)

                self.logger.debug(f"Has _current_queue attribute: {hasattr(self, '_current_queue')}")
                if hasattr(self, '_current_queue'):
                    if isinstance(self._current_queue,list):
                        self.logger.debug(f"_current_queue length: {len(self._current_queue)}")
                    else:
                        self.logger.debug(f"_current_queue is not a list: {type(self._current_queue)}")
                
                # If this was a queue item, update the queue after successful translation
                if hasattr(self, '_current_queue_item') and self._current_queue_item:
                    # Remove processed item from database queue
                    queue_item_id = self._current_queue_item['id']
                    success = self.entity_manager.remove_from_queue(queue_item_id)

                    if success:
                        remaining = self.entity_manager.get_queue_count()
                        self.logger.info(f"Updated queue - {remaining} items remaining.")
                        # Always break after one item — callers (web or CLI) are responsible
                        # for looping to process the next item.
                        break
                    else:
                        self.logger.error("Failed to remove item from queue")
                        break
                else:
                    # if not processing a queue, just do one translation
                    break
                
                
            return end_object
        except TranslationCancelled:
            # A user cancelling their book is a normal outcome, not a failure.
            # It used to fall into the handler below and log an ERROR with an
            # empty message — noise that multiplies now that each book can be
            # cancelled independently.
            self.logger.info("Translation cancelled by user.")
            raise
        except Exception as e:
            self.logger.error(f"Error during translation process: {str(e)}")
            raise

    def resolve_duplicate_entities(self, duplicates, untranslated_text):
        """
        Interactive method to resolve duplicate entities across categories.

        Args:
            duplicates: List of potential duplicate entities to resolve
            untranslated_text: Original text for context

        Returns:
            List of resolved entities with their decisions
        """
        # No implementation in base class
        return []

    # ------------------------------------------------------------------
    # Entity filtering and cleaning (shared by CLI and Web)
    # ------------------------------------------------------------------

    def _save_reviewed_entities(self, pre_entities: Dict, edited: Dict):
        """
        Persist entities approved by the user during a two-pass pre-review.
        Called after `review_entities` returns in two-pass mode so pass-2's
        system prompt includes the user's chosen names in the
        pre-translated-entities block.

        Args:
            pre_entities: Raw entities returned by extract_entities, before review.
            edited: Result dict from review_entities. {} means "skip review — accept
                    AI translations as-is". Otherwise, contains the user's edits
                    (translation/category/gender changes, deletions).
        """
        book_id = getattr(self, 'book_id', None)
        chapter_number = getattr(self, 'chapter_number', None) or 0

        if edited:
            # User submitted review — save according to their edits.
            for category, ents in edited.items():
                if not isinstance(ents, dict):
                    continue
                for key, val in ents.items():
                    if not isinstance(val, dict):
                        continue
                    if val.get("deleted"):
                        continue
                    translation = val.get("translation", "")
                    gender = val.get("gender")
                    note = val.get("note")
                    last_chapter = chapter_number
                    self.entity_manager.add_entity(
                        category, key, translation,
                        book_id=book_id,
                        last_chapter=last_chapter,
                        gender=gender,
                        note=note,
                        note_author='human',
                        note_chapter=chapter_number or None,
                    )
                    self.logger.debug(f"Two-pass: saved entity {key} -> {translation} ({category})")
        else:
            # Skip review path — save AI's pass-1 output unchanged.
            for category, ents in (pre_entities or {}).items():
                if not isinstance(ents, dict):
                    continue
                for key, val in ents.items():
                    if not isinstance(val, dict):
                        continue
                    translation = val.get("translation", "")
                    gender = val.get("gender")
                    note = val.get("note")
                    last_chapter = chapter_number
                    self.entity_manager.add_entity(
                        category, key, translation,
                        book_id=book_id,
                        last_chapter=last_chapter,
                        gender=gender,
                        note=note,
                        note_author='model',
                        note_chapter=chapter_number or None,
                    )
                    self.logger.debug(f"Two-pass (skip-review): saved entity {key} -> {translation} ({category})")

        # No cache refresh needed: translate_chapter() takes its own per-run entity
        # snapshot at entry (get_entities_snapshot), so pass 2 already sees the
        # entities pass 1 just wrote.

    def _filter_existing_entities(self, data: Dict):
        """
        Filter out entities that already exist in the database for this book or as global entities.
        Also deduplicates within the batch: if the same untranslated key appears in multiple
        categories, only the first (by category order) is kept.
        Modifies the data dictionary in-place.

        Returns:
            Number of entities filtered out
        """
        import sqlite3

        categories = list(data.keys())

        # --- Phase 1: remove intra-batch duplicates (same key in multiple categories) ---
        seen_keys = {}  # untranslated -> first category
        dedup_count = 0
        for cat in categories:
            entities = data.get(cat, {})
            for untranslated in list(entities.keys()):
                if untranslated in seen_keys:
                    self.logger.info(
                        f"Removing duplicate entity '{untranslated}' from '{cat}' "
                        f"(already in '{seen_keys[untranslated]}')"
                    )
                    del entities[untranslated]
                    dedup_count += 1
                else:
                    seen_keys[untranslated] = cat

        # --- Phase 2: remove entities that already exist in the database ---
        all_untranslated = set()
        for cat in categories:
            all_untranslated.update(data.get(cat, {}).keys())

        if not all_untranslated and dedup_count == 0:
            return 0

        current_book_id = getattr(self, 'book_id', None)
        existing_entities = {}

        if all_untranslated:
            try:
                conn = self.entity_manager.get_connection()
                cursor = conn.cursor()

                for untranslated in all_untranslated:
                    if current_book_id is not None:
                        cursor.execute('''
                        SELECT category, translation FROM entities
                        WHERE untranslated = ? AND (book_id = ? OR book_id IS NULL)
                        LIMIT 1
                        ''', (untranslated, current_book_id))
                    else:
                        cursor.execute('''
                        SELECT category, translation FROM entities
                        WHERE untranslated = ? AND book_id IS NULL
                        LIMIT 1
                        ''', (untranslated,))

                    row = cursor.fetchone()
                    if row:
                        existing_entities[untranslated] = {
                            'category': row[0],
                            'translation': row[1]
                        }

                conn.close()
            except Exception as e:
                self.logger.error(f"Error checking existing entities: {e}")

        db_count = 0
        if existing_entities:
            for cat in categories:
                entities = data.get(cat, {})
                for untranslated in list(entities.keys()):
                    if untranslated in existing_entities:
                        del entities[untranslated]
                        db_count += 1

        total = dedup_count + db_count
        if total > 0:
            self.logger.info(
                f"Filtered {total} entities ({dedup_count} cross-category duplicates, "
                f"{db_count} already in database)"
            )
        return total

    def _auto_clean_new_entities(self, data: Dict):
        """
        Automatically clean non-proper noun entities from new entity data before review.
        This modifies the data dictionary in-place.

        Args:
            data: Dict of new entities by category (from translation)

        Returns:
            Number of entities deleted
        """
        entity_dict = {}
        for category, entities in data.items():
            for untranslated, entity_data in entities.items():
                translated = entity_data.get('translation', '')
                entity_dict[untranslated] = translated

        if not entity_dict:
            return 0

        initial_count = len(entity_dict)
        self.logger.info(f"Auto-cleaning {initial_count} new entities...")

        proper_nouns = self._classify_proper_nouns(entity_dict)

        if proper_nouns is None:
            return 0

        to_delete_keys = [k for k in entity_dict.keys() if k not in proper_nouns]

        if not to_delete_keys:
            self.logger.info("All new entities are proper nouns. No cleanup needed.")
            return 0

        self.logger.info(f"Classification: {len(proper_nouns)} proper nouns, {len(to_delete_keys)} generic terms to remove")

        deleted_count = 0
        self._cleaned_translations = {}
        self._cleaned_entity_keys = {}
        for category, entities in data.items():
            for untranslated in list(entities.keys()):
                if untranslated in to_delete_keys:
                    translation = entities[untranslated].get('translation', '')
                    if translation:
                        self._cleaned_translations[untranslated] = translation
                    self._cleaned_entity_keys.setdefault(category, []).append(untranslated)
                    del entities[untranslated]
                    deleted_count += 1

        self.logger.info(f"Removed {deleted_count} generic terms from review.")
        return deleted_count

    def _classify_proper_nouns(self, entities: Dict[str, str], model_spec: str = None):
        """
        Send entities to AI model to classify which are proper nouns.

        Args:
            entities: Dictionary of untranslated:translated entities
            model_spec: Optional model spec (provider:model). Uses cleaning_model or translation_model if not specified.

        Returns:
            Set of untranslated entity keys that are proper nouns, or None if classification fails
        """
        import os
        from providers import create_provider

        # Reuse the live config: constructing a fresh TranslationConfig here
        # re-ran dotenv/settings loading on every chapter and ignored any
        # --model override applied to the running translator.
        config = self.translator.config
        cleaning_prompt_path = os.path.join(config.script_dir, "cleaning_prompt.txt")

        try:
            if os.path.exists(cleaning_prompt_path):
                with open(cleaning_prompt_path, 'r', encoding='utf-8') as file:
                    system_prompt = file.read()
            else:
                self.logger.error(f"cleaning_prompt.txt not found at {cleaning_prompt_path}")
                return None
        except Exception as e:
            self.logger.error(f"Error loading cleaning prompt from file: {e}")
            return None

        categorizer_prompt_path = os.path.join(config.script_dir, "categorizer_prompt.txt")

        try:
            if os.path.exists(categorizer_prompt_path):
                with open(categorizer_prompt_path, 'r', encoding='utf-8') as file:
                    categorizer_template = file.read()
                user_prompt = categorizer_template.replace("{ENTITIES_JSON}", json.dumps(entities, ensure_ascii=False, indent=2))
            else:
                self.logger.error(f"categorizer_prompt.txt not found at {categorizer_prompt_path}")
                return None
        except Exception as e:
            self.logger.error(f"Error loading categorizer prompt from file: {e}")
            return None

        try:
            if model_spec is None:
                if hasattr(self, 'cleaning_model') and self.cleaning_model:
                    model_spec = self.cleaning_model
                else:
                    model_spec = config.translation_model

            provider_name, model = config.parse_model_spec(model_spec)
            provider = create_provider(provider_name)

            self.logger.info(f"Analyzing {len(entities)} entities with {model}...")

            response = provider.chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0
            )

            content = provider.get_response_content(response)
            content = content.strip()

            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
                if content.startswith("json"):
                    content = content[4:].strip()

            proper_nouns = json.loads(content)

            if not isinstance(proper_nouns, list):
                raise ValueError("Response is not a JSON array")

            return set(proper_nouns)

        except Exception as e:
            self.logger.error(f"Error during AI classification: {e}")
            return None

    def _decase_cleaned_entities(self, text: List[str]) -> List[str]:
        """
        Lowercase the English translations of entities that were removed by auto-clean.
        Skips occurrences that appear at a sentence start (preceded by .!? or newline).
        text is a list of paragraph strings, matching the shape of end_object['content'].
        """
        cleaned = getattr(self, '_cleaned_translations', {})
        if not cleaned:
            return text

        def make_replacer(paragraph, lower):
            def replacer(match):
                preceding = paragraph[max(0, match.start() - 2):match.start()]
                if re.search(r'[.!?\n"\'"\u2018\u201C]\s?$', preceding):
                    return match.group(0)  # sentence start — leave capitalised
                return lower
            return replacer

        for untranslated, translation in cleaned.items():
            if not translation:
                continue
            lower = translation.lower()
            if lower == translation:
                continue

            pattern = re.compile(r'\b' + re.escape(translation) + r'\b')
            for i in range(len(text)):
                text[i] = re.sub(pattern, make_replacer(text[i], lower), text[i])

        return text
