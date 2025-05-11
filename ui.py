from typing import Dict, List, Optional, Any, Union, Tuple
from abc import ABC, abstractmethod
from entities import EntityManager
from logger import Logger
from translation_engine import TranslationEngine
import json
import re

class UserInterface(ABC):
    """Abstract base class for different user interfaces"""
    
    def __init__(self, translator: TranslationEngine, entity_manager: EntityManager, logger: Logger):
        self.translator = translator
        self.entity_manager = entity_manager
        self.logger = logger
    
    @abstractmethod
    def get_input(self) -> List[str]:
        """Get input text from the user interface"""
        pass
    
    @abstractmethod
    def display_results(self, results: Dict) -> None:
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
        
            # Get input
            chapter_text = self.get_input()
        
            # Perform translation
            translation_results = self.translator.translate_chapter(chapter_text)
            if translation_results is None:
                self.logger.error("Translation process failed - translation_results is None")
                return None
                
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
            if totally_new_entities != {'characters': {}, 'places': {}, 'organizations': {}, 'abilities': {}, 'titles': {}, 'equipment': {}}:
                edited_entities = self.review_entities(totally_new_entities, chapter_text)
            else:
                edited_entities = {}
            
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
                                    last_chapter, 
                                    incorrect_translation, 
                                    gender
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
            
            # Add new entities to SQLite database
            for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']:
                if category not in end_object['entities']:
                    continue
                    
                for key, entity_data in end_object['entities'][category].items():
                    # Skip if the entity already exists in our database (in any category)
                    translation = entity_data.get("translation", "")
                    last_chapter = entity_data.get("last_chapter", current_chapter)
                    incorrect_translation = entity_data.get("incorrect_translation", None)
                    gender = entity_data.get("gender", None)
                    
                    # Add to database, with uniqueness constraint
                    self.entity_manager.add_entity(
                        category, 
                        key, 
                        translation, 
                        last_chapter, 
                        incorrect_translation, 
                        gender
                    )
            
            # Save updated entities
            self.entity_manager.save_entities()
            
            # Add original text to output
            end_object['untranslated'] = chapter_text
            
            # Display results
            self.display_results(end_object)
            
            # If this was a queue item, update the queue after successful translation
            if hasattr(self, '_current_queue') and self._current_queue:
                updated_queue = self._current_queue[1:]  # Remove the processed item
                self.entity_manager.save_json_file(f"{self.entity_manager.config.script_dir}/queue.json", updated_queue)
                self.logger.info(f"Updated queue - {len(updated_queue)} items remaining.")
            
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
