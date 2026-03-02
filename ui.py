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
                    stream=getattr(self, 'stream', False)
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
                if totally_new_entities != {'characters': {}, 'places': {}, 'organizations': {}, 'abilities': {}, 'titles': {}, 'equipment': {}, 'creatures': {}}:
                    edited_entities = self.review_entities(totally_new_entities, chapter_text)
                else:
                    edited_entities = {}
                
                # Lowercase any capitalised generic terms that were auto-cleaned
                if hasattr(self, '_decase_cleaned_entities'):
                    end_object['content'] = self._decase_cleaned_entities(end_object['content'])

                # Fix any lines where the model left Chinese characters untranslated
                if hasattr(self, '_fix_partial_translations'):
                    end_object['content'] = self._fix_partial_translations(end_object['content'])

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
                                        last_chapter=last_chapter,
                                        incorrect_translation=incorrect_translation,
                                        gender=gender
                                    )
                                    
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

                # Save entities directly to database to avoid duplication
                self.logger.debug("--- Direct entity saving ---")
                try:
                    conn = sqlite3.connect(self.entity_manager.db_path)
                    cursor = conn.cursor()

                    # Process each entity from end_object
                    for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']:
                        if category not in end_object['entities']:
                            continue
                            
                        for key, entity_data in end_object['entities'][category].items():
                            translation = entity_data.get("translation", "")
                            last_chapter = entity_data.get("last_chapter", current_chapter)
                            incorrect_translation = entity_data.get("incorrect_translation", None)
                            gender = entity_data.get("gender", None)
                            
                            # Check if entity exists with this book_id
                            cursor.execute('''
                            SELECT id FROM entities 
                            WHERE category = ? AND untranslated = ? AND book_id = ?
                            ''', (category, key, self.book_id))
                            
                            existing = cursor.fetchone()
                            
                            if existing:
                                # Update existing
                                cursor.execute('''
                                UPDATE entities
                                SET translation = ?, last_chapter = ?, incorrect_translation = ?, gender = ?
                                WHERE id = ?
                                ''', (translation, last_chapter, incorrect_translation, gender, existing[0]))
                                self.logger.debug(f"Updated entity {key} ({translation}) in category {category} with book_id={self.book_id}")
                            else:
                                # Insert new
                                cursor.execute('''
                                INSERT INTO entities
                                (category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ''', (category, key, translation, last_chapter, incorrect_translation, gender, self.book_id))
                                self.logger.debug(f"Added entity {key} ({translation}) to category {category} with book_id={self.book_id}")
                    
                    conn.commit()
                    conn.close()
                    self.logger.info("Entities saved to database successfully")
                except sqlite3.Error as e:
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

                        # Also save book-specific entities
                        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']:
                            if category not in end_object['entities']:
                                continue
                                
                            for key, entity_data in end_object['entities'][category].items():
                                # Add to database with book_id
                                translation = entity_data.get("translation", "")
                                last_chapter = entity_data.get("last_chapter", current_chapter)
                                incorrect_translation = entity_data.get("incorrect_translation", None)
                                gender = entity_data.get("gender", None)
                                
                                # Add to database, with book_id
                                self.entity_manager.add_entity(
                                    category, 
                                    key, 
                                    translation, 
                                    book_id=self.book_id,
                                    last_chapter=last_chapter, 
                                    incorrect_translation=incorrect_translation, 
                                    gender=gender
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

                        # Break the loop if queue is empty
                        if remaining == 0:
                            self.logger.debug(f"Breaking out after updating queue cause queue empty now")
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
