from typing import Dict, List, Optional, Any, Union, Tuple
from abc import ABC, abstractmethod
from database import DatabaseManager
from logger import Logger
from translation_engine import TranslationEngine
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
    def review_entities(self, entities: Dict, untranslated_text: List[str]) -> Dict:
        """Allow the user to review and edit entities"""
        pass
    
    def run_translation(self):
        """Run the translation process from start to finish"""
        try:
            # Store for queue management
            self._current_queue = None

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
                
                # Continue with regular entity review
                if any(v for v in totally_new_entities.values()):
                    edited_entities = self.review_entities(totally_new_entities, chapter_text)
                else:
                    edited_entities = {}
                
                # Remove auto-cleaned generic entities from end_object so they are not saved to the database
                if hasattr(self, '_cleaned_entity_keys'):
                    for category, keys in self._cleaned_entity_keys.items():
                        for key in keys:
                            end_object['entities'].get(category, {}).pop(key, None)

                # Lowercase any capitalised generic terms that were auto-cleaned
                end_object['content'] = self._decase_cleaned_entities(end_object['content'])

                # Fix any lines where the model left source-language characters untranslated
                if not getattr(self, 'no_repair', False):
                    # Determine source language from the book
                    _source_lang = 'zh'
                    if hasattr(self, 'book_id') and self.book_id:
                        _book_info = self.entity_manager.get_book(self.book_id)
                        if _book_info:
                            _source_lang = _book_info.get('source_language', 'zh') or 'zh'
                    end_object['content'] = self._fix_partial_translations(end_object['content'], source_language=_source_lang)

                # Convert Chinese measurement units to metric equivalents
                if not getattr(self, 'no_convert_units', False):
                    end_object['content'] = self._convert_chinese_units(end_object['content'])

                # Apply entity edits to the translation
                if edited_entities:
                    # Process edited entities
                    for category, entities in edited_entities.items():
                        for key, value in list(entities.items()):
                            # Ensure value is a dictionary before accessing its keys
                            if isinstance(value, dict) and value.get("deleted", False):
                                # Remove from end_object if marked as deleted
                                end_object['entities'][category].pop(key, None)
                            else:
                                # Update translations for non-deleted entities
                                node = value.copy()
                                end_object['content'] = self.entity_manager.update_translated_text(end_object['content'], node)
                                
                                # Update the entity in the SQLite database
                                if not value.get("deleted", False):
                                    # Update existing entity or add a new one
                                    translation = node.get("translation", "")
                                    last_chapter = node.get("last_chapter", current_chapter)
                                    incorrect_translation = node.get("incorrect_translation", None)
                                    gender = node.get("gender", None)
                                    
                                    # Check if this entity already exists in another category
                                    result = self.entity_manager.add_entity(
                                        category,
                                        key,
                                        translation,
                                        book_id=getattr(self, 'book_id', None),
                                        last_chapter=last_chapter,
                                        incorrect_translation=incorrect_translation,
                                        gender=gender,
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
                
                # Convert any "THIS CHAPTER" placeholder to the actual chapter number
                for category in new_entities:
                    try:
                        for entity_key, entity_value in end_object['entities'][category].items():
                            if entity_value["last_chapter"] == "THIS CHAPTER":
                                end_object['entities'][category][entity_key]["last_chapter"] = current_chapter
                    except KeyError:
                        # Skip this iteration if the key is missing
                        continue
                

                # Save updated entities
                #self.entity_manager.save_entities()

                # Build set of entities that are new or were edited during review.
                # Only these should have their translation/category/gender overwritten;
                # pre-existing entities just get last_chapter bumped so we don't
                # clobber edits made via the /entities page while translation was running.
                new_or_edited_keys = set()  # (category, untranslated) tuples
                for cat, ents in totally_new_entities.items():
                    for key in ents:
                        new_or_edited_keys.add((cat, key))
                if edited_entities:
                    for cat, ents in edited_entities.items():
                        for key, value in ents.items():
                            if isinstance(value, dict) and not value.get("deleted", False):
                                new_or_edited_keys.add((cat, key))

                # Save entities directly to database to avoid duplication
                self.logger.debug("--- Direct entity saving ---")
                try:
                    conn = self.entity_manager.get_connection()
                    cursor = conn.cursor()

                    # Process each entity from end_object
                    for category in end_object['entities']:

                        for key, entity_data in end_object['entities'][category].items():
                            translation = entity_data.get("translation", "")
                            last_chapter = entity_data.get("last_chapter", current_chapter)
                            incorrect_translation = entity_data.get("incorrect_translation", None)
                            gender = entity_data.get("gender", None)
                            is_new_or_edited = (category, key) in new_or_edited_keys

                            # Check if entity exists with this book_id
                            cursor.execute('''
                            SELECT id FROM entities
                            WHERE untranslated = ? AND book_id = ?
                            ''', (key, self.book_id))

                            existing = cursor.fetchone()

                            if existing:
                                if is_new_or_edited:
                                    # New entity from LLM or edited during review — full update
                                    cursor.execute('''
                                    UPDATE entities
                                    SET category = ?, translation = ?, last_chapter = ?, incorrect_translation = ?, gender = ?,
                                        origin_chapter = COALESCE(origin_chapter, ?)
                                    WHERE id = ?
                                    ''', (category, translation, last_chapter, incorrect_translation, gender, current_chapter, existing[0]))
                                    self.logger.debug(f"Updated entity {key} ({translation}) in category {category} with book_id={self.book_id}")
                                else:
                                    # Pre-existing entity — only bump last_chapter to avoid
                                    # overwriting edits made while translation was running
                                    cursor.execute('''
                                    UPDATE entities
                                    SET last_chapter = ?
                                    WHERE id = ?
                                    ''', (last_chapter, existing[0]))
                                    self.logger.debug(f"Bumped last_chapter for existing entity {key} in category {category}")
                            else:
                                # Insert new — record origin_chapter
                                cursor.execute('''
                                INSERT INTO entities
                                (category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id, origin_chapter)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (category, key, translation, last_chapter, incorrect_translation, gender, self.book_id, current_chapter))
                                self.logger.debug(f"Added entity {key} ({translation}) to category {category} with book_id={self.book_id}")

                    conn.commit()
                    conn.close()
                    self.logger.info("Entities saved to database successfully")
                except Exception as e:
                    self.logger.error(f"Error saving entities to database: {e}")
                
                # Update in-memory cache for consistent state
                self.entity_manager._load_entities(book_id=self.book_id)
                
                # Add original text to output
                end_object['untranslated'] = chapter_text
                

                self.logger.debug(f"About to save chapter with book_id={self.book_id}, chapter_number={getattr(self, 'chapter_number', 'None')}")
                self.logger.debug(f"Current chapter from translation: {current_chapter}")
                
                # If book_id and chapter_number are set, save as a book chapter
                if hasattr(self, 'book_id') and self.book_id is not None:
                    # Use provided chapter number or the detected one
                    chapter_number = end_object.get('chapter')
        
                    # Ensure we have a valid chapter number
                    if not chapter_number or not isinstance(chapter_number, int) or chapter_number <= 0:
                    # Fall back to explicitly provided chapter number or default to 1
                        chapter_number = getattr(self, 'chapter_number', 1)
                        self.logger.warning(f"Invalid chapter number in translation results, using {chapter_number}")
                    
                    # Save chapter to database
                    chapter_id = self.entity_manager.save_chapter(
                        self.book_id,
                        chapter_number,
                        end_object.get('title', f'Chapter {chapter_number}'),
                        chapter_text,  # untranslated content
                        end_object.get('content', []),  # translated content
                        summary=end_object.get('summary', ''),
                        translation_model=self.translator.config.translation_model
                    )
                    
                    if chapter_id:
                        print(f"Saved as Chapter {chapter_number} of Book ID {self.book_id}")

                        # Also save book-specific entities (only new/edited ones;
                        # pre-existing entities were already handled above)
                        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']:
                            if category not in end_object['entities']:
                                continue

                            for key, entity_data in end_object['entities'][category].items():
                                if (category, key) not in new_or_edited_keys:
                                    continue  # skip pre-existing — already bumped last_chapter above

                                translation = entity_data.get("translation", "")
                                last_chapter = entity_data.get("last_chapter", current_chapter)
                                incorrect_translation = entity_data.get("incorrect_translation", None)
                                gender = entity_data.get("gender", None)

                                self.entity_manager.add_entity(
                                    category,
                                    key,
                                    translation,
                                    book_id=self.book_id,
                                    last_chapter=last_chapter,
                                    incorrect_translation=incorrect_translation,
                                    gender=gender,
                                )
                
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
        from config import TranslationConfig

        config = TranslationConfig()
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

    # Regex patterns for detecting untranslated source-language characters
    _SOURCE_LANG_PATTERNS = {
        'zh': re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]'),
        # Japanese: CJK ideographs OR hiragana/katakana (to catch Japanese-specific text)
        'ja': re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u309f\u30a0-\u30ff]'),
        # Korean: Hangul syllables and Jamo
        'ko': re.compile(r'[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]'),
    }

    _LANG_NAMES = {
        'zh': 'Chinese',
        'ja': 'Japanese',
        'ko': 'Korean',
    }

    def _convert_chinese_units(self, content: List[str]) -> List[str]:
        """Append metric equivalents to Chinese measurement units in translated text."""
        from unit_converter import convert_units
        model = getattr(self, 'cleaning_model', None)
        return convert_units(content, cleaning_model=model)

    def _fix_partial_translations(self, content: List[str], source_language: str = 'zh') -> List[str]:
        """
        Detect lines containing untranslated source-language characters and fix them
        using the cleaning model. Batches all affected lines into a single
        API call, returning the content list with fixed lines spliced back in.

        Only supported for zh, ja, ko. For other languages the content is returned as-is.
        """
        import os

        pattern = self._SOURCE_LANG_PATTERNS.get(source_language)
        if pattern is None:
            self.logger.debug(f"Partial-translation repair not supported for source_language='{source_language}', skipping")
            return content

        affected_indices = [i for i, line in enumerate(content) if pattern.search(line)]

        if not affected_indices:
            return content

        lang_name = self._LANG_NAMES.get(source_language, source_language)
        self.logger.info(f"Found {len(affected_indices)} partially translated line(s) containing {lang_name} characters")

        lines_to_fix = [content[i] for i in affected_indices]
        user_prompt = json.dumps(lines_to_fix, ensure_ascii=False, indent=2)

        try:
            from providers import create_provider
            from config import TranslationConfig
            config = TranslationConfig()

            # Build repair prompt — use the template file with language substituted
            repair_prompt_path = os.path.join(config.script_dir, "translation_repair_prompt.txt")
            try:
                if os.path.exists(repair_prompt_path):
                    with open(repair_prompt_path, 'r', encoding='utf-8') as file:
                        system_prompt = file.read()
                else:
                    self.logger.error(f"translation_repair_prompt.txt not found at {repair_prompt_path}")
                    return content
            except Exception as e:
                self.logger.error(f"Error loading repair prompt: {e}")
                return content

            # Replace language placeholder so the prompt is language-aware
            system_prompt = system_prompt.replace("{{LANGUAGE}}", lang_name)

            if hasattr(self, 'cleaning_model') and self.cleaning_model:
                model_spec = self.cleaning_model
            else:
                model_spec = config.translation_model

            provider_name, model_name = config.parse_model_spec(model_spec)
            provider = create_provider(provider_name)

            self.logger.info(f"Repairing {len(affected_indices)} line(s) with {model_name}...")

            response = provider.chat_completion(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0
            )

            raw = provider.get_response_content(response).strip()

            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
                if raw.startswith("json"):
                    raw = raw[4:].strip()

            fixed_lines = json.loads(raw)

            if not isinstance(fixed_lines, list) or len(fixed_lines) != len(affected_indices):
                raise ValueError(
                    f"Expected {len(affected_indices)} fixed lines, got "
                    f"{len(fixed_lines) if isinstance(fixed_lines, list) else type(fixed_lines)}"
                )

            result = list(content)
            for idx, fixed in zip(affected_indices, fixed_lines):
                result[idx] = fixed

            self.logger.info(f"Repaired {len(affected_indices)} partially translated line(s)")
            return result

        except Exception as e:
            self.logger.error(f"Could not repair partial translations: {e}")
            return content
