"""
Modular Translator Application with Class-Based Design
"""
import json
import os
import math
import re
import unicodedata
import sqlite3
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Union, Tuple
from itertools import zip_longest
from openai import OpenAI
from dotenv import load_dotenv
from epub_processor import EPUBProcessor
from output_formatter import OutputFormatter
from directory_processor import DirectoryProcessor


class TranslationConfig:
    """Configuration class for translation settings"""
    
    def __init__(self):
        load_dotenv()
        
        # API credentials
        self.deepseek_key = os.getenv("DEEPSEEK_KEY")
        self.openai_key = os.getenv("OPENAI_KEY")
        
        # Model settings
        self.translation_model = os.getenv("TRANSLATION_MODEL", "o3-mini")
        self.advice_model = os.getenv("ADVICE_MODEL", "o3-mini")
        
        # Debug mode
        self.debug_mode = os.getenv("DEBUG") == "True"
        
        # Paths
        self.script_dir = os.path.dirname(os.path.abspath(__file__)) + "/"
        
        # Translation settings
        self.max_chars = int(os.getenv("MAX_CHARS", "10000"))
    
    def get_client(self, use_deepseek=False):
        """Return an appropriate API client based on configuration"""
        if use_deepseek:
            return OpenAI(api_key=self.deepseek_key, base_url="https://api.deepseek.com")
        else:
            return OpenAI(api_key=self.openai_key)


class Logger:
    """Class to handle logging configuration and operations"""
    
    def __init__(self, config: TranslationConfig):
        self.config = config
        self.logger = self._setup_logger()
        
        # Check if logger is None and provide a fallback
        if self.logger is None:
            import logging
            self.logger = logging.getLogger("fallback_logger")
            self.logger.setLevel(logging.DEBUG)
            # Add at least a console handler
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
    
    def _setup_logger(self):
        """Set up and return a configured logger"""
        import logging
        logger = logging.getLogger("translate_logger")
        logger.setLevel(logging.DEBUG if self.config.debug_mode else logging.ERROR)
        
        # Formatter for log messages
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        
        # File handler
        file_handler = logging.FileHandler("translate.log", mode="w")  # Overwrites the file
        file_handler.setLevel(logging.DEBUG if self.config.debug_mode else logging.ERROR)
        file_handler.setFormatter(formatter)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG if self.config.debug_mode else logging.ERROR)
        console_handler.setFormatter(formatter)
        
        # Add handlers to the logger
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        return logger
    
    def debug(self, message, *args, **kwargs):
        """Pass debug messages to the underlying logger"""
        self.logger.debug(message, *args, **kwargs)
    
    def info(self, message, *args, **kwargs):
        """Pass info messages to the underlying logger"""
        self.logger.info(message, *args, **kwargs)
    
    def warning(self, message, *args, **kwargs):
        """Pass warning messages to the underlying logger"""
        self.logger.warning(message, *args, **kwargs)
    
    def error(self, message, *args, **kwargs):
        """Pass error messages to the underlying logger"""
        self.logger.error(message, *args, **kwargs)
    
    def critical(self, message, *args, **kwargs):
        """Pass critical messages to the underlying logger"""
        self.logger.critical(message, *args, **kwargs)


class EntityManager:
    """Class to manage entity operations, storage, and consistency using SQLite"""
    
    def __init__(self, config: 'TranslationConfig', logger: 'Logger'):
        self.config = config
        self.logger = logger
        self.db_path = os.path.join(self.config.script_dir, "entities.db")
        self.entities = {}  # Cached entities
        self._initialize_database()
        self._load_entities()
    
    def _initialize_database(self):
        """Initialize the SQLite database with proper schema if it doesn't exist"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create main entities table with a unique constraint on category+untranslated
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                untranslated TEXT NOT NULL,
                translation TEXT NOT NULL,
                last_chapter TEXT,
                incorrect_translation TEXT,
                gender TEXT,
                UNIQUE(category, untranslated)
            )
            ''')
            
            # Create indices for faster lookups
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON entities(category)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_untranslated ON entities(untranslated)')
            
            conn.commit()
            conn.close()
            self.logger.info("Database initialized successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Database initialization error: {e}")
            raise
    
    def _load_entities(self) -> Dict:
        """Load existing entities from database into memory cache"""
        
        # Define default entity categories
        default_entities = {
            "characters": {}, 
            "places": {}, 
            "organizations": {}, 
            "abilities": {}, 
            "titles": {}, 
            "equipment": {}
        }
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get all entities grouped by category
            cursor.execute('SELECT category, untranslated, translation, last_chapter, incorrect_translation, gender FROM entities')
            rows = cursor.fetchall()
            
            # Process results
            entities = default_entities.copy()
            for row in rows:
                category, untranslated, translation, last_chapter, incorrect_translation, gender = row
                
                # Initialize category if needed (should be unnecessary with defaults)
                entities.setdefault(category, {})
                
                # Create entity entry
                entity_data = {"translation": translation, "last_chapter": last_chapter}
                
                # Add optional attributes if they exist
                if incorrect_translation:
                    entity_data["incorrect_translation"] = incorrect_translation
                if gender:
                    entity_data["gender"] = gender
                
                # Add to our entities dictionary
                entities[category][untranslated] = entity_data
            
            conn.close()
            self.entities = entities
            self.logger.debug(f"Loaded {sum(len(cat) for cat in entities.values())} entities from database")
            return entities
            
        except sqlite3.Error as e:
            self.logger.error(f"Error loading entities from database: {e}")
            # Return default empty structure on error
            self.entities = default_entities
            return default_entities
    
    def _load_json_file(self, filepath, default=None):
        """Load JSON data from a file with error handling"""
        full_path = os.path.join(self.config.script_dir, filepath)
        
        if not os.path.exists(full_path):
            return default or {}
        
        try:
            with open(full_path, 'r', encoding='utf-8') as file:
                return json.load(file)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to decode JSON from file '{filepath}': {e}")
            return default or {}
        except OSError as e:
            self.logger.error(f"Failed to read file '{filepath}': {e}")
            return default or {}
    
    def save_json_file(self, filepath, data):
        """Save data to a JSON file with error handling"""
        full_path = os.path.join(self.config.script_dir, filepath)
        
        try:
            with open(full_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, indent=4, ensure_ascii=False)
        except OSError as e:
            self.logger.error(f"Failed to write to file '{filepath}': {e}")
    
    def combine_json_entities(self, old_entities, new_entities):
        """
        Merges two JSON-like dictionaries, updating 'old_entities' with entries
        from 'new_entities'. The keys are entity categories, and values are dictionaries
        of untranslated-translated pairs. Entries from 'new_entities' will replace
        existing ones from 'old_entities' if they have the same keys.
        """
        # Create a copy of old_entities to avoid modifying the original
        result = {category: old_entities.get(category, {}).copy() 
                 for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']}
        
        # Update with new entities
        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']:
            new_category_dict = new_entities.get(category, {})
            result[category].update(new_category_dict)
        
        return result
    
    def save_entities(self):
        """Save the current entities cache to the SQLite database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # For each category and entity in memory cache
            for category, entities in self.entities.items():
                for untranslated, entity_data in entities.items():
                    translation = entity_data.get('translation', '')
                    last_chapter = entity_data.get('last_chapter', '')
                    incorrect_translation = entity_data.get('incorrect_translation', None)
                    gender = entity_data.get('gender', None)
                    
                    # Use INSERT OR REPLACE to handle both new entities and updates
                    cursor.execute('''
                    INSERT OR REPLACE INTO entities 
                    (category, untranslated, translation, last_chapter, incorrect_translation, gender)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''', (category, untranslated, translation, last_chapter, incorrect_translation, gender))
            
            conn.commit()
            conn.close()
            self.logger.info("Entities saved to database successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Error saving entities to database: {e}")
            # Consider creating a backup JSON in this case
            self.save_json_file("entities_backup.json", self.entities)
            self.logger.info("Created backup of entities in entities_backup.json")
    
    def entities_inside_text(self, text_lines, all_entities, current_chapter, do_count=True):
        """
        Extracts entities mentioned in the given text and updates their running count and last chapter.
        
        Args:
            text_lines (list of str): The chapter's text content split into lines.
            all_entities (dict): The complete entities dictionary with global counts.
            current_chapter (int or str): The current chapter number.
            do_count (bool): Defaults to True. Set to False if regenerating system prompt to avoid double counting.
        
        Returns:
            dict: A filtered dictionary of entities mentioned in the text with updated global counts and last chapter.
        """
        found_entities = {}
        
        # Ensure combined_text is a string
        if isinstance(text_lines, list):
            combined_text = ' '.join(text_lines)
        elif isinstance(text_lines, str):
            combined_text = text_lines
        else:
            self.logger.error(f"Unexpected type for text_lines: {type(text_lines)}")
            # Convert to string as a fallback
            combined_text = str(text_lines)
        
        # Add debugging
        self.logger.debug(f"entities_inside_text: type of combined_text = {type(combined_text)}")
        
        # Normalize the combined text for consistent matching
        combined_text = self._normalize_text(combined_text)
        
        # Query all entities from the database if all_entities is empty
        if not all_entities:
            all_entities_dict = {}
            for key, value in all_entities.items():
                key_normalized = self._normalize_text(key)
                
                regex = re.compile(re.escape(key_normalized))
                try:
                    matches = regex.findall(combined_text)
                    occurrence_count = len(matches)
                except TypeError as e:
                    self.logger.error(f"TypeError in regex.findall: {e}")
                    self.logger.error(f"Key: {key}, Type of combined_text: {type(combined_text)}")
                    occurrence_count = 0
                
                if occurrence_count > 0:
                    self.logger.debug(f"'{key}' ({value['translation']}) was found {occurrence_count} times.")
                    
                    if key not in found_entities:
                        # Initialize entity data
                        found_entities[key] = {
                            "translation": value["translation"],
                            "last_chapter": current_chapter
                        }
                    
                    # Update global entities
                    all_entities[key]["last_chapter"] = current_chapter
        
        return found_entities
    
    def find_new_entities(self, old_data, new_data):
        """
        Return a dictionary of all entities that are present in new_data
        but do NOT exist in old_data at all.
        """
        newly_added = {}
        
        for category, new_items in new_data.items():
            if category not in old_data:
                newly_added[category] = new_items
                continue
            
            for entity_name, entity_info in new_items.items():
                if entity_name not in old_data[category]:
                    if category not in newly_added:
                        newly_added[category] = {}
                    newly_added[category][entity_name] = entity_info
        
        return newly_added
    
    def update_translated_text(self, translated_text, entity):
        """
        Does a substitution on translated_text, replacing entity['old_translation'] 
        with entity['translation'] in a case-insensitive way, but preserving 
        word-by-word casing of the original matched text.
        """
        old_translation = entity.get('incorrect_translation', '')
        new_translation = entity['translation']
        
        self.logger.info(f"We will update '{old_translation}' for '{new_translation}'...")
        
        def match_case(match):
            matched_text = match.group()
            old_words = matched_text.split()
            new_words = new_translation.split()
            
            # Use zip_longest to handle mismatched word counts
            transformed_words = []
            for old_w, new_w in zip_longest(old_words, new_words, fillvalue=""):
                if old_w.isupper():
                    transformed_words.append(new_w.upper())
                elif old_w.istitle():
                    transformed_words.append(new_w.capitalize())
                elif old_w.islower():
                    transformed_words.append(new_w.lower())
                else:
                    transformed_words.append(new_w)  # Preserve as is for unknown cases
            
            return " ".join(transformed_words).strip()
        
        # Compile pattern for case-insensitive search
        pattern = re.compile(re.escape(old_translation), re.IGNORECASE)
        for i in range(len(translated_text)):
            translated_text[i] = pattern.sub(match_case, translated_text[i])
        
        return translated_text
    
    def _normalize_text(self, text):
        """Normalize text for consistent comparison"""
        return unicodedata.normalize('NFC', text)
    
    def add_entity(self, category, untranslated, translation, last_chapter=None, incorrect_translation=None, gender=None):
        """
        Add a new entity to the database.
        Returns True if successful, False if the entity already exists in a different category.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # First check if this entity exists in any other category
            cursor.execute('''
            SELECT category FROM entities 
            WHERE untranslated = ? AND category != ?
            ''', (untranslated, category))
            
            existing = cursor.fetchone()
            if existing:
                self.logger.warning(f"Entity '{untranslated}' already exists in category '{existing[0]}', not adding to '{category}'")
                conn.close()
                return False
            
            # Add or update the entity
            cursor.execute('''
            INSERT OR REPLACE INTO entities 
            (category, untranslated, translation, last_chapter, incorrect_translation, gender)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (category, untranslated, translation, last_chapter, incorrect_translation, gender))
            
            conn.commit()
            conn.close()
            
            # Update the in-memory cache
            self.entities.setdefault(category, {})
            entity_data = {"translation": translation}
            if last_chapter:
                entity_data["last_chapter"] = last_chapter
            if incorrect_translation:
                entity_data["incorrect_translation"] = incorrect_translation
            if gender:
                entity_data["gender"] = gender
                
            self.entities[category][untranslated] = entity_data
            return True
            
        except sqlite3.Error as e:
            self.logger.error(f"Error adding entity to database: {e}")
            return False
    
    def update_entity(self, category, untranslated, **kwargs):
        """
        Update an existing entity with new values.
        Returns True if the entity was updated, False if it wasn't found.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Build the SET clause dynamically based on provided kwargs
            set_clause = []
            values = []
            
            for key, value in kwargs.items():
                if key in ['translation', 'last_chapter', 'incorrect_translation', 'gender']:
                    set_clause.append(f"{key} = ?")
                    values.append(value)
            
            if not set_clause:
                self.logger.warning("No valid fields to update")
                conn.close()
                return False
            
            # Complete the parameter list with category and untranslated
            values.extend([category, untranslated])
            
            # Execute the update
            cursor.execute(f'''
            UPDATE entities 
            SET {', '.join(set_clause)}
            WHERE category = ? AND untranslated = ?
            ''', values)
            
            if cursor.rowcount == 0:
                self.logger.warning(f"Entity '{untranslated}' in category '{category}' not found for update")
                conn.close()
                return False
            
            conn.commit()
            conn.close()
            
            # Update the in-memory cache
            if category in self.entities and untranslated in self.entities[category]:
                for key, value in kwargs.items():
                    if key in ['translation', 'last_chapter', 'incorrect_translation', 'gender']:
                        self.entities[category][untranslated][key] = value
            
            return True
            
        except sqlite3.Error as e:
            self.logger.error(f"Error updating entity in database: {e}")
            return False
    
    def delete_entity(self, category, untranslated):
        """
        Delete an entity from the database.
        Returns True if the entity was deleted, False if it wasn't found.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            DELETE FROM entities 
            WHERE category = ? AND untranslated = ?
            ''', (category, untranslated))
            
            if cursor.rowcount == 0:
                self.logger.warning(f"Entity '{untranslated}' in category '{category}' not found for deletion")
                conn.close()
                return False
            
            conn.commit()
            conn.close()
            
            # Update the in-memory cache
            if category in self.entities and untranslated in self.entities[category]:
                del self.entities[category][untranslated]
            
            return True
            
        except sqlite3.Error as e:
            self.logger.error(f"Error deleting entity from database: {e}")
            return False
    
    def change_entity_category(self, old_category, untranslated, new_category):
        """
        Move an entity from one category to another.
        Returns True if the entity was moved, False otherwise.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if entity exists in the source category
            cursor.execute('''
            SELECT translation, last_chapter, incorrect_translation, gender 
            FROM entities 
            WHERE category = ? AND untranslated = ?
            ''', (old_category, untranslated))
            
            entity_data = cursor.fetchone()
            if not entity_data:
                self.logger.warning(f"Entity '{untranslated}' not found in category '{old_category}'")
                conn.close()
                return False
            
            # Check if entity already exists in the target category
            cursor.execute('''
            SELECT id FROM entities 
            WHERE category = ? AND untranslated = ?
            ''', (new_category, untranslated))
            
            if cursor.fetchone():
                self.logger.warning(f"Entity '{untranslated}' already exists in target category '{new_category}'")
                conn.close()
                return False
            
            # Update the category
            cursor.execute('''
            UPDATE entities 
            SET category = ?
            WHERE category = ? AND untranslated = ?
            ''', (new_category, old_category, untranslated))
            
            conn.commit()
            conn.close()
            
            # Update the in-memory cache
            if old_category in self.entities and untranslated in self.entities[old_category]:
                entity_data_dict = self.entities[old_category][untranslated]
                del self.entities[old_category][untranslated]
                
                self.entities.setdefault(new_category, {})
                self.entities[new_category][untranslated] = entity_data_dict
            
            return True
            
        except sqlite3.Error as e:
            self.logger.error(f"Error changing entity category in database: {e}")
            return False
    
    def get_entity_by_translation(self, translation):
        """
        Find an entity by its translation.
        Returns a tuple (category, untranslated, entity_data) if found, None otherwise.
        
        This is useful for finding duplicates by translation rather than by untranslated text.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT category, untranslated, last_chapter, incorrect_translation, gender 
            FROM entities 
            WHERE translation = ?
            ''', (translation,))
            
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                return None
            
            # Return the first match
            category, untranslated, last_chapter, incorrect_translation, gender = rows[0]
            
            entity_data = {"translation": translation, "last_chapter": last_chapter}
            if incorrect_translation:
                entity_data["incorrect_translation"] = incorrect_translation
            if gender:
                entity_data["gender"] = gender
            
            return (category, untranslated, entity_data)
            
        except sqlite3.Error as e:
            self.logger.error(f"Error finding entity by translation in database: {e}")
            return None
    
    def export_to_json(self, filepath):
        """
        Export the entire database to a JSON file (for compatibility with original code).
        """
        try:
            # Export current in-memory cache to JSON
            self.save_json_file(filepath, self.entities)
            return True
        except Exception as e:
            self.logger.error(f"Error exporting entities to JSON: {e}")
            return False
    
    def import_from_json(self, filepath):
        """
        Import entities from a JSON file into the database.
        Returns True if successful, False otherwise.
        """
        try:
            json_data = self._load_json_file(filepath)
            if not json_data:
                self.logger.warning(f"No data found in JSON file '{filepath}'")
                return False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Clear existing data?
            clear_first = False  # Could be a parameter
            if clear_first:
                cursor.execute('DELETE FROM entities')
            
            # Import each entity
            count = 0
            for category, entities in json_data.items():
                for untranslated, entity_data in entities.items():
                    translation = entity_data.get('translation', '')
                    last_chapter = entity_data.get('last_chapter', '')
                    incorrect_translation = entity_data.get('incorrect_translation', None)
                    gender = entity_data.get('gender', None)
                    
                    cursor.execute('''
                    INSERT OR REPLACE INTO entities 
                    (category, untranslated, translation, last_chapter, incorrect_translation, gender)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''', (category, untranslated, translation, last_chapter, incorrect_translation, gender))
                    count += 1
            
            conn.commit()
            conn.close()
            self.logger.info(f"Imported {count} entities from JSON file '{filepath}'")
            
            # Refresh the in-memory cache
            self._load_entities()
            return True
            
        except Exception as e:
            self.logger.error(f"Error importing entities from JSON: {e}")
            return False


class TranslationEngine:
    """Core class for handling text translation logic"""
    
    def __init__(self, config: 'TranslationConfig', logger: 'Logger', entity_manager: 'EntityManager'):
        self.config = config
        self.logger = logger
        self.entity_manager = entity_manager
        self.client = config.get_client()
    
    def find_substring_with_context(self, text_array, substring, padding=20):
        """
        Search for a substring in a joined string (converted from a list of strings)
        and return padding[20] characters before and after the match.
        
        Parameters:
            text_array (list of str or str): The array of strings or string representing the text.
            substring (str): The substring to search for.
            padding (int) [optional]: the number of characters before and after to include
        
        Returns:
            str: The context of the match (padding characters before, the match, padding characters after) 
                 or None if no match is found.
        """
        if isinstance(text_array, list):
            # Join the array of strings into a single string with spaces separating lines
            full_text = ' '.join(text_array)
        elif isinstance(text_array, str):
            full_text = text_array
        
        # Find the index of the substring in the full text
        match_index = full_text.find(substring)
        if match_index != -1:
            start_index = max(0, match_index - padding)
            end_index = min(len(full_text), match_index + len(substring) + padding)
            return full_text[start_index:end_index]
        return None
    
    def split_by_n(self, sequence, n):
        """
        Generator that splits a list (sequence) into n (approximately) equal chunks.
        e.g., [1,2,3,4,5,6,7,8,9],3 => [[1,2,3], [4,5,6], [7,8,9]]
        
        Safely handles cases where n is 0 or sequence is empty.
        """
        if not sequence:
            # Return the empty sequence as a single chunk
            yield sequence
            return
        
        # Always return at least one chunk
        n = max(1, n)
        n = min(n, len(sequence))
        
        chunk_size, remainder = divmod(len(sequence), n)
        
        # Debug info
        self.logger.debug(f"Splitting sequence of length {len(sequence)} into {n} chunks")
        self.logger.debug(f"Chunk size: {chunk_size}, remainder: {remainder}")
        
        for i in range(n):
            start_idx = i * chunk_size + min(i, remainder)
            end_idx = (i + 1) * chunk_size + min(i + 1, remainder)
            
            self.logger.debug(f"Chunk {i+1}: indices {start_idx} to {end_idx}")
            yield sequence[start_idx:end_idx]
    
    def generate_system_prompt(self, pretext, entities, do_count=True):
        """
        Generate the system (instruction) prompt for translation, incorporating any discovered entities.
        """
        # Debug info
        self.logger.debug(f"generate_system_prompt: type of pretext = {type(pretext)}")
        if isinstance(pretext, list) and len(pretext) > 0:
            self.logger.debug(f"First line: {pretext[0][:50]}")
    
        # Ensure all entity categories exist
        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']:
            entities.setdefault(category, {})
    
        end_entities = {}
        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']:
            entities.setdefault(category, {})
    
        end_entities = {}
        end_entities['characters'] = self.entity_manager.entities_inside_text(pretext, entities['characters'], "THIS CHAPTER", do_count)
        end_entities['places'] = self.entity_manager.entities_inside_text(pretext, entities['places'], "THIS CHAPTER", do_count)
        end_entities['organizations'] = self.entity_manager.entities_inside_text(pretext, entities['organizations'], "THIS CHAPTER", do_count)
        end_entities['abilities'] = self.entity_manager.entities_inside_text(pretext, entities['abilities'], "THIS CHAPTER", do_count)
        end_entities['titles'] = self.entity_manager.entities_inside_text(pretext, entities['titles'], "THIS CHAPTER", do_count)
        end_entities['equipment'] = self.entity_manager.entities_inside_text(pretext, entities['equipment'], "THIS CHAPTER", do_count)

        entities_json = json.dumps(end_entities, ensure_ascii=False, indent=4)
        
        # This large template string remains the same as your original implementation
        prompt = """Your task is to translate the provided material into English, preserving the original content, title, and entities. Focus on semantic accuracy, cultural relevance, and stylistic fidelity.
Key Guidelines:

    Content:
        I have permission to translate this content.
        Translate all content without summarizing. Double-space lines for clarity.
        Ensure the translation reflects the meaning, tone, and flow of the original text, including slang, idioms, and subtle nuances.
        Use double quotes for speech and maintain correct English grammar, syntax, and tenses.
        Retain formatting symbols (e.g., 【】) unless specified otherwise.
        NEVER Summarize "content"! Always translate!
        Prioritize meaningful translation over literal transliteration (e.g., 天海国 → "Heavenly Sea Kingdom").
        This is a Chinese xianxia story.
	
    Entities:
        Always translate proper nouns (characters, places, organizations, etc.).
        Translate most place names meaningfully (e.g., 黑风镇 → "Black Wind Town").
        Places, abilities and characters are especially important and should always be incorporated into the new entities record.
        Abilities could encompass skills, techniques, spells, etc
        Use provided pre-translated entities for consistency; translate new ones as required.
        Categories: CHARACTERS, PLACES, ORGANIZATIONS, ABILITIES, TITLES, and EQUIPMENT.
        If there are no entities to put in the category then just leave it blank but include the full JSON empty dictionary format:
{}

    Entities Format:
        Use this JSON format for entities:

Translate entities accurately, ensuring their relevance and significance in the context of the text.

Here is a list of pre-translated entities, in JSON. If and when you see these nouns in this text, please translate them as provided for a consistent translation experience. If an entity (as described above) is not in this list, translate it yourself:

ENTITIES: """ + entities_json + '\n' + """

---

#### Response Template Example

{
    \"title\": \"Chapter 3 - The Great Apcalyptic Battle\",
    \"chapter\": 3,
    \"summary\": \"A concise 75-word or less summary of this chapter. This is the only place where you can summarize.\",
    \"content\": [
        \"This is an example of the great battle. We must remember to NEVER summarise the content.\",
        \"\",
        \"Now we are on a new line to express the fact we should go to new lines.\",
        \"\",
        \"'I wonder what I am supposed to do now.'\",
        \"\",
        \"Now we are on the last line, which shouldn't include any linebreaks.\"
    ],
    \"entities\": {
        \"characters\": {
            \"钟岳\": {\"translation\": \"Zhong Yue\", \"gender\":\"male\", \"last_chapter\": 3},
            \"夏儿\": {\"translation\": \"Xia'er\", \"gender\":\"female\", \"last_chapter\": 3},
            \"方剑\": {\"translation\": \"Fang Jian\", \"gender\":\"male\", \"last_chapter\": 2}
        },
        \"places\": {
            \"剑门山\": {\"translation\": \"Jianmen Mountain\", \"last_chapter\": 3},
            \"大荒\": {\"translation\": \"Great Wilderness\", \"last_chapter\": 3},
            \"染霜城\": {\"translation\": \"Frostveil City\", \"last_chapter\": 75
        }
        },
        \"organizations\": {
            \"风氏\": {\"translation\": \"Feng Clan\", \"last_chapter\": 3}
        },
        \"abilities\": {
            \"太极拳\": {\"translation\": \"Supreme Ultimate Fist\", \"last_chapter\": 3},
            \"天级上品武技·星陨斩\": {\"translation\": \"High-level Heaven Rank Martial Skill: Starfall Slash\", \"last_chapter\": 2}
        },
        \"titles\": {
            \"鉴宝师\": {\"translation\": \"Treasure Appraiser\", \"last_chapter\": 1},
            \"真君\": {\"translation\": \"True Sovereign\", \"last_chapter\": 5},
            \"筑道\": {\"translation\": \"Foundation Establishment\", \"last_chapter\": 7}
        },
        \"equipment\": {
            \"蓝龙药鼎\": {\"translation\": \"Azure Dragon Medicinal Cauldron\", \"last_chapter\": 3},
            \"血魔九影剑\": {\"translation\": \"Blood Demon Nine Shadows Sword\", \"last_chapter\": 1}
        }
    }
}
---

### Key Notes:
1. **Content**: The `content` array must include the full textual content of the chapter, formatted exactly as given, with line breaks preserved. DO NOT summarize or alter the content.
2. **Chapter**: The chapter number, as an integer. Provide a good guess based on the initial translation.
3. **Summary**: Provide a concise summary of no more than 75 words for the chapter.
4. **Entities**: The `entities` section should include all relevant `characters`, `places`, `organizations`, `abilities`, `titles`, and `equipment`. Each entry must:
    - Each entity key inside each category is untranslated text. IMPORTANT: NEVER PLACE AN ENGLISH ENTITY KEY. KEYS ARE UNTRANSLATED.
    - Equipment can include things like weapons, tools, potions, and special resources. It's not limited to things that have to be carried.
    - Use the untranslated name as the key.
    - Include:
        - \"translation\": The accurate and consistent translated name or term.
        - \"gender\": CHARACTER exclusive attribute. female, male, or neither. Used to keep pronouns consistent since Chinese doesn't have gendered pronouns
        - \"last_chapter\": You only see entities if they are in this chapter, so this will always be THIS CHAPTER for you.
        - \"incorrect_translation\": this field only exists if I have corrected your translation of this entity in the past. this is the incorrect translation you made. pay some attention to how your translation was corrected, if you can.
5. **Translation Formatting** In general, do not split sentences with whitespaces. For example: 'Yet deep down, Chen Shaojun felt that this\nwas really important' is wrong. That should be on one line.
6. **Titles** Titles in the entity list should include both obvious titles as well as cultivation ranks or levels.
7. **Ensure Consistency**: Check for existing entities in the pre-translated entities list above. Only add new entities or update existing ones if necessary.
8. **Formatting**: The output must strictly adhere to JSON formatting standards to ensure proper parsing.
"""
        return prompt
    
    def combine_json_chunks(self, chunk1_data, chunk2_data, current_chapter):
        """
        Combine two JSON-like chapter data chunks into one by merging their
        content, summary, and entities. 'current_chapter' is used to update
        the 'last_chapter' field.
        """
        if not chunk1_data:
            return chunk2_data
        if not chunk2_data:
            return chunk1_data
        
        chunk1_data.setdefault("entities", {})
        chunk2_data.setdefault("entities", {})
        
        chunk1_data.setdefault("content", [])
        chunk2_data.setdefault("content", [])
        chunk1_data["content"].extend(chunk2_data["content"])
        
        chunk1_data["summary"] = f"{chunk1_data.get('summary', '')} {chunk2_data.get('summary', '')}".strip()
        
        # Process each entity category
        for category, entities in chunk2_data.get("entities", {}).items():
            chunk1_data["entities"].setdefault(category, {})
            for key, data in entities.items():
                # Check if this entity already exists in another category
                entity_exists_elsewhere = False
                
                for other_category in chunk1_data["entities"]:
                    if other_category != category and key in chunk1_data["entities"][other_category]:
                        # This entity key already exists in a different category
                        entity_exists_elsewhere = True
                        self.logger.warning(f"Duplicate entity '{key}' found in both '{category}' and '{other_category}'")
                        
                        # Check if the translations match
                        existing_translation = chunk1_data["entities"][other_category][key].get("translation")
                        new_translation = data.get("translation")
                        
                        if existing_translation != new_translation:
                            self.logger.warning(f"Entity translations don't match: '{existing_translation}' vs '{new_translation}'")
                        break
                
                if entity_exists_elsewhere:
                    # Skip adding this entity to avoid duplication
                    continue
                
                # Check if the translation already exists in any category
                translation = data.get("translation", "")
                translation_exists = False
                if translation:
                    for check_category, check_entities in chunk1_data["entities"].items():
                        for check_key, check_data in check_entities.items():
                            if check_data.get("translation") == translation and check_key != key:
                                translation_exists = True
                                self.logger.warning(f"Entity translation '{translation}' already exists for key '{check_key}' in '{check_category}'")
                                break
                        if translation_exists:
                            break
                
                if translation_exists:
                    # Skip adding this entity to avoid translation duplication
                    # or optionally, we could add with a modified translation
                    # data["translation"] = f"{translation} (alt)"
                    continue
                
                # Add the entity if it doesn't exist elsewhere
                if key not in chunk1_data["entities"][category]:
                    # Add new entity
                    chunk1_data["entities"][category][key] = {
                        "translation": data["translation"],
                        "last_chapter": current_chapter,
                    }
                    # Add optional fields
                    if "gender" in data:
                        chunk1_data["entities"][category][key]["gender"] = data["gender"]
                    if "incorrect_translation" in data:
                        chunk1_data["entities"][category][key]["incorrect_translation"] = data["incorrect_translation"]
                else:
                    # Update existing entity's last_chapter field
                    chunk1_data["entities"][category][key]["last_chapter"] = current_chapter
        
        return chunk1_data
    
    def get_translation_options(self, node, untranslated_text):
        """
        Asks the LLM for translation options for an entity node.
        Also checks for potential duplicates of suggested translations.
        
        Parameters:
        node(dict): JSON data corresponding to one entity
        untranslated_text(array): lines of untranslated text, optional. will provide additional context to LLM
        
        Returns:
        dict: A dictionary with message and options for translation
        """
        context = self.find_substring_with_context(untranslated_text, node['untranslated'], 35)
        node['context'] = context
        
        # Check if there are existing translations that might conflict
        existing_duplicates = []
        try:
            # We'll look for similar translations to warn the user
            import sqlite3
            conn = sqlite3.connect(self.entity_manager.db_path)
            cursor = conn.cursor()
            
            # Get current translations that might be similar
            cursor.execute('''
            SELECT translation, category, untranslated 
            FROM entities 
            WHERE untranslated != ? AND category != ?
            ''', (node['untranslated'], node.get('category', '')))
            
            results = cursor.fetchall()
            conn.close()
            
            # If we have results, include them in the node so the LLM can avoid them
            if results:
                node['existing_translations'] = [
                    {'translation': trans, 'category': cat, 'untranslated': unt}
                    for trans, cat, unt in results
                ]
                
                # Find exact duplicates for later warning
                current_translation = node.get('translation', '')
                if current_translation:
                    existing_duplicates = [
                        {'translation': trans, 'category': cat, 'untranslated': unt}
                        for trans, cat, unt in results
                        if trans.lower() == current_translation.lower()
                    ]
        except Exception as e:
            self.logger.error(f"Error checking for duplicate translations: {e}")
        
        # Use the advice model for this
        advice_client = self.config.get_client(use_deepseek=(self.config.advice_model == "deepseek-chat"))
        
        # Modify the prompt to include awareness of duplicates
        prompt = """Your task is to offer translation options. Below in the user text is a JSON node consisting of a translation you have performed previously, which may include "context" which is 20-50 characters before and after the untranslated text. The user did not like the translation and wants to change it, so please offer three alternatives, as well as a short message (less than 200 words) about the untranslated Chinese characters and why you chose to translate it this way. 

    You should include a very literal translation of each character in your message, but not necessarily in your alternatives, unless the translation is phonetic (foreign words). Order the alternatives by your preference, use the context to more finely tune your advice if it is offered.

    One of the most common rejections of translations is simply transliterating, so if if you transliterated last time, do not do so this time.

    IMPORTANT: If "existing_translations" is provided in the node, AVOID suggesting translations that are identical or very similar to these existing translations, as this would cause confusion. If you see similar translations, try to make your suggestions clearly distinct.

    Your output should be in this schema:
    {
    "message": "Your message to the user",
    "options": ["translation option 1", "translation option 2", "translation option 3"]
    }

    Do not include your original translation option among the three options.
    """
        
        dumped_node = json.dumps(node, indent=4, ensure_ascii=False)
        print(dumped_node)
        
        response = advice_client.chat.completions.create(
            model=self.config.advice_model,
            messages=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": dumped_node
                        }
                    ]
                }
            ],
            temperature=1,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
            response_format={"type": "json_object"}
        )
        
        try:
            parsed_response = json.loads(response.choices[0].message.content)
            
            # If we found duplicates earlier, append a warning to the message
            if existing_duplicates:
                duplicate_warning = "\n\nWARNING: The current translation conflicts with existing entities:"
                for dup in existing_duplicates:
                    duplicate_warning += f"\n- '{dup['untranslated']}' in '{dup['category']}' (also translated as '{dup['translation']}')"
                duplicate_warning += "\nConsider choosing a more distinctive translation to avoid confusion."
                
                parsed_response['message'] = parsed_response['message'] + duplicate_warning
        except json.JSONDecodeError as e:
            print("Failed to parse JSON. Payload:")
            print(response.choices[0].message.content)
            print(f"Error: {e}")
            return {'message': f'The translation failed: {e}', 'options': []}
        
        return parsed_response
    
    def translate_chapter(self, chapter_text):
        """
        Translate a chapter of text using the configured LLM.
        
        Args:
            chapter_text (list of str): The chapter's text content split into lines.
            
        Returns:
            dict: A dictionary containing the translated chapter data.
        """
        # Initialize current_chapter to a default value
        current_chapter = 0
        self.logger.debug(f"Using translation model: {self.config.translation_model}")
        self.logger.debug(f"API client initialized: {self.client is not None}")
        self.logger.debug(f"translate_chapter called with text of {len(chapter_text)} lines")

        # Handle empty input
        if not chapter_text:
            self.logger.warning("Empty text provided for translation. Nothing to translate.")
            return {
                "end_object": {"title": "Empty Chapter", "chapter": 0, "content": [], "entities": {}},
                "new_entities": {},
                "totally_new_entities": {},
                "old_entities": self.entity_manager.entities.copy(),
                "real_old_entities": self.entity_manager.entities.copy(),
                "current_chapter": 0,
                "total_char_count": 0
            }

        total_char_count = sum(len(line) for line in chapter_text)

        # Use entities from SQLite database
        old_entities = self.entity_manager.entities.copy()
        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']:
            old_entities.setdefault(category, {})

        real_old_entities = old_entities

        # Calculate chunks count, ensuring at least 1 chunk
        chunks_count = max(1, math.ceil(total_char_count / self.config.max_chars))

        # Generate the initial system prompt
        system_prompt = self.generate_system_prompt(chapter_text, old_entities)

        # Split the text into chunks for the LLM if necessary due to output token limits
        split_text = list(self.split_by_n(chapter_text, chunks_count))

        self.logger.debug(f"Text split into {len(split_text)} chunks")

        if len(split_text) == 0:
            self.logger.error("Error: Text was split into 0 chunks. This should never happen.")
            # Create a single chunk with the entire text as a fallback
            split_text = [chapter_text]
            self.logger.debug("Created fallback chunk with entire text")

        if len(split_text) > 1:
            self.logger.info(f"Input text is {total_char_count} characters. Splitting text into {len(split_text)} chunks.")
        
        end_object = {}

        self.logger.debug("Initializing totally_new_entities")
        totally_new_entities = {}
        self.entity_manager.save_json_file(f"{self.config.script_dir}/prompt.tmp", system_prompt)
        
        self.logger.debug(f"About to process {len(split_text)} chunks")
        for chunk_index, chunk in enumerate(split_text, 1):
            self.logger.debug(f"Processing chunk {chunk_index} of {len(split_text)}")
            chunk_str = "\n".join(chunk)
            user_text = "Translate the following into English: \n" + chunk_str
            self.logger.debug(f"About to call {self.config.translation_model} with chunk {chunk_index} of {len(split_text)}")
            response = self.client.chat.completions.create(
                model=self.config.translation_model,
                messages=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": system_prompt
                            }
                        ]
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": user_text
                            }
                        ]
                    }
                ],
                temperature=1,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
                response_format={"type": "json_object"}
            )
            
            self.logger.info(f"Translation of chunk {chunk_index} complete.")
            self.logger.debug(f"API call completed for chunk {chunk_index}")
            self.entity_manager.save_json_file(f"{self.config.script_dir}/response.tmp", response.choices[0].message.content)
            
            try:
                parsed_chunk = json.loads(response.choices[0].message.content)
            except json.JSONDecodeError as e:
                print("Failed to parse JSON. Payload:")
                print(response.choices[0].message.content)
                print(f"Error: {e}")
                exit(1)
            
            current_chapter = parsed_chunk['chapter']
            
            end_object = self.combine_json_chunks(end_object, parsed_chunk, current_chapter)
            
            # Find new entities in this chunk and record them in totally_new_entities as a running total
            new_entities_this_chunk = self.entity_manager.find_new_entities(real_old_entities, end_object['entities'])
            totally_new_entities = self.entity_manager.combine_json_entities(totally_new_entities, new_entities_this_chunk)
            
            # Update old_entities with the newly processed chunk's combined entities
            old_entities = self.entity_manager.combine_json_entities(old_entities, end_object['entities'])
            
            # Regenerate the system prompt for the next chunk to maintain consistency
            system_prompt = self.generate_system_prompt(chapter_text, old_entities, do_count=False)
        
        self.logger.debug("Finished processing all chunks")
        
        # Check for duplicate entities based on translation value
        self._check_for_translation_duplicates(end_object['entities'])
        
        # Ensure all entity categories exist
        new_entities = {
            "characters": end_object.get('entities', {}).get('characters', {}),
            "places": end_object.get('entities', {}).get('places', {}),
            "organizations": end_object.get('entities', {}).get('organizations', {}),
            "abilities": end_object.get('entities', {}).get('abilities', {}),
            "titles": end_object.get('entities', {}).get('titles', {}),
            "equipment": end_object.get('entities', {}).get('equipment', {})
        }

        return {
            "end_object": end_object,
            "new_entities": new_entities,
            "totally_new_entities": totally_new_entities,
            "old_entities": old_entities,
            "real_old_entities": real_old_entities,
            "current_chapter": current_chapter,
            "total_char_count": total_char_count
        }
    def _check_for_translation_duplicates(self, entities_dict):
        """
        Check for duplicate translations across different categories or within the same category
        and log warnings for manual review.
        
        Args:
            entities_dict (dict): Dictionary of entities organized by category
        """
        # Create a mapping of translations to their sources
        translation_map = {}
        
        for category, entities in entities_dict.items():
            for key, data in entities.items():
                translation = data.get('translation', '')
                if not translation:
                    continue
                
                if translation in translation_map:
                    # Found a duplicate translation
                    prev_category, prev_key = translation_map[translation]
                    self.logger.warning(f"Duplicate translation '{translation}' found:")
                    self.logger.warning(f"  - {prev_category}: {prev_key}")
                    self.logger.warning(f"  - {category}: {key}")
                else:
                    # Add this translation to the map
                    translation_map[translation] = (category, key)


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


class CommandLineInterface(UserInterface):
    """Command-line interface implementation"""
    
    def __init__(self, translator: TranslationEngine, entity_manager: EntityManager, logger: Logger):
        super().__init__(translator, entity_manager, logger)
        self.import_optional_modules()
    
    def import_optional_modules(self):
        """Import modules needed for CLI that might not be available in all UI versions"""
        try:
            import questionary
            from rich import print_json
            self.questionary = questionary
            self.print_json = print_json
            self.has_rich_ui = True
        except ImportError:
            self.has_rich_ui = False
            print("Warning: Rich UI components (questionary/rich) not available. Using basic interface.")
    
    def get_input(self) -> List[str]:
        """Get input text from CLI - clipboard, file, or manual entry"""
        import argparse
        
        # Create the parser
        parser = argparse.ArgumentParser(description="Process input from clipboard, file, or manual entry.")
        
        # Create a mutually exclusive group, this is for the input option
        group = parser.add_mutually_exclusive_group()
        
        # Add arguments to the group
        group.add_argument("--clipboard", action="store_true", help="Process input from the clipboard")
        group.add_argument("--resume", action="store_true", help="Take input from the queue, and translate sequentially")
        group.add_argument("--file", type=str, help="Process input from a specified file")
        group.add_argument("--epub", type=str, help="Process an EPUB file and add chapters to the queue")

        # directory input and options
        group.add_argument("--dir", type=str, help="Process all text files in a directory and add to queue")
        parser.add_argument("--sort", type=str, choices=["auto", "name", "modified", "none"], default="auto",
                    help="Sorting strategy for directory files (default: auto)")
        parser.add_argument("--pattern", type=str, default="*.txt",
                    help="File pattern for directory processing (default: *.txt)")

        # output options
        parser.add_argument("--format", type=str, choices=["text", "html", "markdown", "epub"], default="text",
                   help="Output format for translation results (default: text)")
        parser.add_argument("--book-title", type=str, help="Book title for EPUB output")
        parser.add_argument("--book-author", type=str, help="Book author for EPUB output")
        parser.add_argument("--book-language", type=str, default="en", help="Book language code for EPUB output (default: en)")
        parser.add_argument("--edit-book-info", action="store_true", help="Edit book information for EPUB output")
        
        # Queue argument and manipulation
        parser.add_argument("--queue", action="store_true", help="Add a chapter to the queue, for later sequential translation")
        parser.add_argument("--list-queue", action="store_true", 
                        help="List all items in the translation queue")
        parser.add_argument("--clear-queue", action="store_true", 
                        help="Clear the translation queue")
        
        # SQLite arguments
        parser.add_argument("--export-json", type=str, help="Export SQLite database to JSON file")
        parser.add_argument("--import-json", type=str, help="Import entities from JSON file to SQLite database")
        parser.add_argument("--check-duplicates", action="store_true", help="Check for duplicate entities in the database")
        
        # Model arguments
        parser.add_argument("--model", type=str, help=f"Specify a specific model. Default is {self.translator.config.translation_model}")
        parser.add_argument("--key", type=str, help=f"Specify an API key. Default is from environmental variables or .env")
        
        args = parser.parse_args()

        # Process directory
        if args.dir:
            self._process_directory(args.dir, args.sort, args.pattern)
            exit(0)  # Exit after processing directory

        # edit epub info from --edit-book-info
        if args.edit_book_info:
            self._edit_book_info()
            exit(0)

        # List queue contents
        if args.list_queue:
            self._list_queue_contents()
            exit(0)

        # Clear queue
        if args.clear_queue:
            self._clear_queue()
            exit(0)
        
        # Store the format in a class variable
        self.output_format = args.format

        # Create book_info if EPUB format is selected
        if self.output_format == "epub":
            self.book_info = self._get_book_info()
            
            # Override with command line arguments if provided
            if args.book_title:
                self.book_info["title"] = args.book_title
            if args.book_author:
                self.book_info["author"] = args.book_author
            if args.book_language:
                self.book_info["language"] = args.book_language
        else:
            self.book_info = None

        # Handle API key override
        if args.key:
            self.translator.config.openai_key = args.key
            self.translator.client = self.translator.config.get_client()
        
        # Handle model override
        if args.model:
            self.translator.config.translation_model = args.model
        
        # Handle SQLite database management commands
        if args.export_json:
            self.logger.info(f"Exporting SQLite database to {args.export_json}")
            if self.entity_manager.export_to_json(args.export_json):
                print(f"Database successfully exported to {args.export_json}")
            else:
                print("Export failed. Check logs for details.")
            exit(0)
            
        if args.import_json:
            self.logger.info(f"Importing entities from {args.import_json} to SQLite database")
            if self.entity_manager.import_from_json(args.import_json):
                print(f"Successfully imported entities from {args.import_json}")
            else:
                print("Import failed. Check logs for details.")
            exit(0)
            
        if args.check_duplicates:
            self.check_database_duplicates()
            exit(0)
        
        # Get the input based on the arguments
        if args.clipboard:
            import pyperclip
            self.logger.info("Processing input from the clipboard.")
            pretext = pyperclip.paste().splitlines()
        elif args.file:
            self.logger.info(f"Processing input from the file: {args.file}")
            pretext = self.file_to_array(args.file)
        elif args.resume:
            queue_json = self.entity_manager._load_json_file("queue.json")
            if not queue_json or not isinstance(queue_json, list) or len(queue_json) == 0:
                print("No items in queue to resume.")
                exit(1)
            
            # Debug info
            self.logger.info(f"Queue contains {len(queue_json)} items.")
            self.logger.info(f"First item type: {type(queue_json[0])}")
            
            # Get the first item from the queue
            first_item = queue_json[0]
            
            # Handle different possible formats in the queue
            if isinstance(first_item, list):
                # Already a list of strings
                pretext = first_item
            elif isinstance(first_item, str):
                # Single string, split into lines
                pretext = first_item.splitlines()
            else:
                # Unknown format
                self.logger.error(f"Unknown queue item format: {type(first_item)}")
                print(f"Queue item has unexpected format: {type(first_item)}")
                exit(1)
            
            # Verify the item is not empty
            if not pretext:
                self.logger.error("Empty item in queue.")
                print("The first item in the queue is empty. Removing it.")
                
                # Remove the empty item and save the updated queue
                updated_queue = queue_json[1:]
                self.entity_manager.save_json_file(f"{self.entity_manager.config.script_dir}/queue.json", updated_queue)
                
                # Exit or recurse to get the next item
                if not updated_queue:
                    print("Queue is now empty.")
                    exit(1)
                else:
                    print("Trying next item in queue...")
                    return self.get_input()  # Recursive call to try the next item
            
            self.logger.info(f"Processing queue item with {len(pretext)} lines.")
            
            # Store the queue for later updating after successful translation
            self._current_queue = queue_json
            
            return pretext
        elif args.epub:
            self._process_epub_file(args.epub)
            exit(0)  # Exit after processing EPUB
        else:
            # Manual entry
            print("Enter/Paste your content. Type ENDEND or Ctrl-D out to start translating.")
            pretext = []
            while True:
                try:
                    line = input()
                    if line == "ENDEND":
                        break
                    pretext.append(line)
                except EOFError:
                    break
        
        # Handle queue option - this should happen for all input types
        if args.queue:
            queue = self.entity_manager._load_json_file("queue.json") or []
            self.logger.info(f"Appending to queue (len: {len(queue)}) ")
            queue.append(pretext)
            self.entity_manager.save_json_file(f"{self.entity_manager.config.script_dir}/queue.json", queue)
            self.logger.info("queue.json written")
            exit(0)  # Exit after queuing - don't continue to translation!
        
        return pretext
    
    def _process_epub_file(self, epub_path):
        """Process an EPUB file and add chapters to the queue."""
        try:
            # Initialize EPUB processor
            processor = EPUBProcessor(self.entity_manager.config, self.logger)
            
            print(f"Processing EPUB file: {epub_path}")
            success, num_chapters, message = processor.process_epub(epub_path)
            
            if success:
                print(f"Success! {message}")
                
                # Show queue summary
                self._list_queue_contents(summary_only=True)
            else:
                print(f"Failed! {message}")
                
        except Exception as e:
            self.logger.error(f"Error processing EPUB: {e}")
            print(f"Error processing EPUB: {e}")
            import traceback
            traceback.print_exc()
    
    def check_database_duplicates(self):
        """
        Check for entity duplications in the database, report them, and
        provide interactive resolution options.
        """
        
        if not self.has_rich_ui:
            print("Rich UI components not available. Reporting duplicates without interactive resolution.")
            self._report_database_duplicates()
            return
        
        # Connect to the database
        conn = sqlite3.connect(self.entity_manager.db_path)
        cursor = conn.cursor()
        
        # Check for duplicate untranslated entities
        print("\n=== Checking for Duplicate Untranslated Entities ===")
        cursor.execute('''
        SELECT untranslated, COUNT(*) as count, GROUP_CONCAT(category) as categories
        FROM entities
        GROUP BY untranslated
        HAVING COUNT(*) > 1
        ''')
        
        duplicate_untranslated = cursor.fetchall()
        if duplicate_untranslated:
            print(f"Found {len(duplicate_untranslated)} duplicate untranslated entities:")
            
            for idx, row in enumerate(duplicate_untranslated, 1):
                untranslated, count, categories = row
                print(f"\n{idx}. '{untranslated}' appears in {count} categories: {categories}")
                
                # Get details for this duplicate
                cursor.execute('''
                SELECT category, translation, last_chapter
                FROM entities
                WHERE untranslated = ?
                ORDER BY category
                ''', (untranslated,))
                
                instances = cursor.fetchall()
                for i, (category, translation, last_chapter) in enumerate(instances):
                    print(f"   {chr(97+i)}) In '{category}' as '{translation}' (Chapter {last_chapter})")
                
                # Ask if user wants to resolve this duplicate
                resolve = self.questionary.confirm(
                    f"Do you want to resolve this duplicate entity '{untranslated}'?"
                ).ask()
                
                if resolve:
                    # Ask what action to take
                    action = self.questionary.select(
                        "How would you like to resolve this duplicate?",
                        choices=[
                            "Keep one instance and delete others",
                            "Rename instances to be distinct",
                            "Get LLM suggestions for better translations",
                            "Skip for now"
                        ]
                    ).ask()
                    
                    if action == "Keep one instance and delete others":
                        # Let user choose which instance to keep
                        keep_choices = [f"{category}: '{translation}'" for category, translation, _ in instances]
                        keep_choice = self.questionary.select(
                            "Which instance would you like to keep?",
                            choices=keep_choices
                        ).ask()
                        
                        keep_idx = keep_choices.index(keep_choice)
                        keep_category = instances[keep_idx][0]
                        
                        # Delete all other instances
                        for category, _, _ in instances:
                            if category != keep_category:
                                cursor.execute('''
                                DELETE FROM entities
                                WHERE untranslated = ? AND category = ?
                                ''', (untranslated, category))
                        
                        conn.commit()
                        print(f"Kept '{untranslated}' in '{keep_category}' and deleted other instances.")
                    
                    elif action == "Rename instances to be distinct":
                        for category, translation, _ in instances:
                            new_translation = self.questionary.text(
                                f"Enter new translation for '{untranslated}' in '{category}' (current: '{translation}'):",
                                default=translation
                            ).ask()
                            
                            if new_translation != translation:
                                cursor.execute('''
                                UPDATE entities
                                SET translation = ?
                                WHERE untranslated = ? AND category = ?
                                ''', (new_translation, untranslated, category))
                                print(f"Updated translation to '{new_translation}' for '{untranslated}' in '{category}'.")
                        
                        conn.commit()
                    
                    elif action == "Get LLM suggestions for better translations":
                        # Get suggestions from LLM for each instance
                        for category, translation, _ in instances:
                            # Create a node object similar to what get_translation_options expects
                            node = {
                                'untranslated': untranslated,
                                'translation': translation,
                                'category': category
                            }
                            
                            print(f"\nGetting suggestions for '{untranslated}' in '{category}'...")
                            advice = self.translator.get_translation_options(node, [])
                            
                            print("\nLLM suggests:")
                            print(f"  \"{advice['message']}\"\n")
                            
                            # Add options to choose from
                            translation_options = advice['options'] + ["Keep current translation", "Custom translation"]
                            
                            chosen_translation = self.questionary.select(
                                f"Choose a translation for '{untranslated}' in '{category}':",
                                choices=translation_options
                            ).ask()
                            
                            if chosen_translation == "Keep current translation":
                                print(f"Keeping current translation '{translation}'.")
                            elif chosen_translation == "Custom translation":
                                custom_val = self.questionary.text(
                                    "Enter your custom translation:",
                                    default=translation
                                ).ask()
                                
                                if custom_val and custom_val != translation:
                                    cursor.execute('''
                                    UPDATE entities
                                    SET translation = ?
                                    WHERE untranslated = ? AND category = ?
                                    ''', (custom_val, untranslated, category))
                                    print(f"Updated translation to '{custom_val}' for '{untranslated}' in '{category}'.")
                            else:
                                # User selected one of the suggested translations
                                cursor.execute('''
                                UPDATE entities
                                SET translation = ?
                                WHERE untranslated = ? AND category = ?
                                ''', (chosen_translation, untranslated, category))
                                print(f"Updated translation to '{chosen_translation}' for '{untranslated}' in '{category}'.")
                        
                        conn.commit()
        else:
            print("No duplicate untranslated entities found.")
        
        # Now check for duplicate translations
        print("\n=== Checking for Duplicate Translations ===")
        cursor.execute('''
        SELECT translation, COUNT(*) as count, GROUP_CONCAT(untranslated) as untranslated_list, 
            GROUP_CONCAT(category) as categories
        FROM entities
        GROUP BY translation
        HAVING COUNT(*) > 1
        ''')
        
        duplicate_translations = cursor.fetchall()
        if duplicate_translations:
            print(f"Found {len(duplicate_translations)} duplicate translations:")
            
            for idx, row in enumerate(duplicate_translations, 1):
                translation, count, untranslated_list, categories = row
                print(f"\n{idx}. '{translation}' is used for {count} different entities:")
                
                # Get detailed information for each duplicate
                cursor.execute('''
                SELECT category, untranslated, last_chapter
                FROM entities
                WHERE translation = ?
                ORDER BY category
                ''', (translation,))
                
                details = cursor.fetchall()
                for i, (category, untranslated, last_chapter) in enumerate(details):
                    print(f"   {chr(97+i)}) In '{category}': '{untranslated}' (Chapter {last_chapter})")
                
                # Ask if user wants to resolve this duplicate
                resolve = self.questionary.confirm(
                    f"Do you want to resolve this duplicate translation '{translation}'?"
                ).ask()
                
                if resolve:
                    # Ask what action to take
                    action = self.questionary.select(
                        "How would you like to resolve this duplicate?",
                        choices=[
                            "Rename translations to be distinct",
                            "Get LLM suggestions for better translations",
                            "Skip for now"
                        ]
                    ).ask()
                    
                    if action == "Rename translations to be distinct":
                        for category, untranslated, _ in details:
                            new_translation = self.questionary.text(
                                f"Enter new translation for '{untranslated}' in '{category}' (current: '{translation}'):",
                                default=translation
                            ).ask()
                            
                            if new_translation != translation:
                                cursor.execute('''
                                UPDATE entities
                                SET translation = ?
                                WHERE untranslated = ? AND category = ?
                                ''', (new_translation, untranslated, category))
                                print(f"Updated translation to '{new_translation}' for '{untranslated}' in '{category}'.")
                        
                        conn.commit()
                    
                    elif action == "Get LLM suggestions for better translations":
                        # Get suggestions from LLM for each instance
                        for category, untranslated, _ in details:
                            # Create a node object similar to what get_translation_options expects
                            node = {
                                'untranslated': untranslated,
                                'translation': translation,
                                'category': category
                            }
                            
                            print(f"\nGetting suggestions for '{untranslated}' in '{category}'...")
                            advice = self.translator.get_translation_options(node, [])
                            
                            print("\nLLM suggests:")
                            print(f"  \"{advice['message']}\"\n")
                            
                            # Add options to choose from
                            translation_options = advice['options'] + ["Keep current translation", "Custom translation"]
                            
                            chosen_translation = self.questionary.select(
                                f"Choose a translation for '{untranslated}' in '{category}':",
                                choices=translation_options
                            ).ask()
                            
                            if chosen_translation == "Keep current translation":
                                print(f"Keeping current translation '{translation}'.")
                            elif chosen_translation == "Custom translation":
                                custom_val = self.questionary.text(
                                    "Enter your custom translation:",
                                    default=translation
                                ).ask()
                                
                                if custom_val and custom_val != translation:
                                    cursor.execute('''
                                    UPDATE entities
                                    SET translation = ?
                                    WHERE untranslated = ? AND category = ?
                                    ''', (custom_val, untranslated, category))
                                    print(f"Updated translation to '{custom_val}' for '{untranslated}' in '{category}'.")
                            else:
                                # User selected one of the suggested translations
                                cursor.execute('''
                                UPDATE entities
                                SET translation = ?
                                WHERE untranslated = ? AND category = ?
                                ''', (chosen_translation, untranslated, category))
                                print(f"Updated translation to '{chosen_translation}' for '{untranslated}' in '{category}'.")
                        
                        conn.commit()
        else:
            print("No duplicate translations found.")
        
        # Apply changes and update in-memory cache
        conn.close()
        self.entity_manager._load_entities()
        
        print("\nDuplicate check and resolution completed.")

    def _report_database_duplicates(self):
        """Non-interactive version of check_database_duplicates that just reports issues"""
        
        # Connect to the database
        conn = sqlite3.connect(self.entity_manager.db_path)
        cursor = conn.cursor()
        
        # Check for duplicate untranslated entities
        print("\n=== Duplicate Untranslated Entities ===")
        cursor.execute('''
        SELECT untranslated, COUNT(*) as count, GROUP_CONCAT(category) as categories
        FROM entities
        GROUP BY untranslated
        HAVING COUNT(*) > 1
        ''')
        
        duplicate_untranslated = cursor.fetchall()
        if duplicate_untranslated:
            print(f"Found {len(duplicate_untranslated)} duplicate untranslated entities:")
            for row in duplicate_untranslated:
                untranslated, count, categories = row
                print(f"  '{untranslated}' appears in {count} categories: {categories}")
                
                # List the details
                cursor.execute('''
                SELECT category, translation
                FROM entities
                WHERE untranslated = ?
                ORDER BY category
                ''', (untranslated,))
                
                details = cursor.fetchall()
                for category, translation in details:
                    print(f"    - {category}: '{translation}'")
        else:
            print("No duplicate untranslated entities found.")
        
        # Check for duplicate translations
        print("\n=== Duplicate Translations ===")
        cursor.execute('''
        SELECT translation, COUNT(*) as count, GROUP_CONCAT(untranslated) as untranslated_list
        FROM entities
        GROUP BY translation
        HAVING COUNT(*) > 1
        ''')
        
        duplicate_translations = cursor.fetchall()
        if duplicate_translations:
            print(f"Found {len(duplicate_translations)} duplicate translations:")
            for row in duplicate_translations:
                translation, count, untranslated_list = row
                print(f"  '{translation}' is used for {count} different entities:")
                
                # List the details
                cursor.execute('''
                SELECT category, untranslated
                FROM entities
                WHERE translation = ?
                ORDER BY category, untranslated
                ''', (translation,))
                
                details = cursor.fetchall()
                for category, untranslated in details:
                    print(f"    - {category}: '{untranslated}'")
        else:
            print("No duplicate translations found.")
        
        conn.close()
    
    def file_to_array(self, filename):
        """Convert a file to an array of lines"""
        with open(filename, 'r', encoding='utf-8') as file:
            lines = file.readlines()
        # Strip newline characters
        return [line.strip() for line in lines]
    
    def display_current_data(self, data):
        """Prints the current data structure to the console."""
        print("\nTotally New Entities In This Chapter:")
        if self.has_rich_ui:
            self.print_json(data=data)
        else:
            print(json.dumps(data, indent=4, ensure_ascii=False))
    
    def review_entities(self, data, untranslated_text=[]):
        """
        Using questionary to display interactive prompts.
        Returns a dictionary of edited data.
        """
        if not self.has_rich_ui:
            print("Rich UI components not available. Skipping entity review.")
            return {}
            
        edited_data = {'characters': {}, 'places': {}, 'organizations': {}, 'abilities': {}, 'titles': {}, 'equipment': {}}
        categories = ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']
        
        while True:
            # 1. Display current data
            self.display_current_data(data)
            
            # 2. Ask if user wants to make changes (yes/no)
            make_changes = self.questionary.confirm(
                "Do you want to make any changes?"
            ).ask()  # returns True/False
            
            if not make_changes:
                break
            
            # 3. Select a category
            if not data:
                print("No categories available.")
                break
            
            # Filter out empty categories
            non_empty_categories = [category for category in data if data[category]]
            # Let the user pick from a list, or choose "Exit"
            category_choice = self.questionary.select(
                "Select a category to edit:",
                choices=non_empty_categories + ["Exit"]
            ).ask()
            
            if category_choice == "Exit":
                break
            
            selected_category = category_choice
            
            # 4. Select an item within that category
            items = data[selected_category]
            if not items:
                print(f"No items in category '{selected_category}'.")
                continue
            
            # Build a list of questionary Choices, each with a title like "Frodo (Фродо)"
            # but the underlying value is just "Frodo".
            item_choices = []
            for key, item_data in items.items():
                translation = item_data.get("translation", "")
                display_title = f"{key} ({translation})" if translation else key
                item_choices.append(self.questionary.Choice(title=display_title, value=key))
            
            # Add a "Back" option
            item_choices.append("Back")
            
            item_choice = self.questionary.select(
                f"Select an item in '{selected_category}' to manage:",
                choices=item_choices
            ).ask()
            
            if item_choice == "Back":
                continue
            
            selected_item_key = item_choice
            selected_item = items[selected_item_key]
            
            # 5. Ask what the user wants to do with this item
            action = self.questionary.select(
                f"What do you want to do with '{selected_item_key}'?",
                choices=["Edit item", "Delete item", "Change category", "Go back"]
            ).ask()
            
            if action == "Go back":
                continue
            
            if action == "Delete item":
                del data[selected_category][selected_item_key]
                print(f"Item '{selected_item_key}' deleted.")
                edited_data[selected_category][selected_item_key] = {"deleted": True}
                
                # Also delete from SQLite database
                self.entity_manager.delete_entity(selected_category, selected_item_key)
                continue
            
            if action == "Change category":
                # Prompt for the new category, excluding empty categories and the current category
                non_empty_categories = [
                    cat for cat in categories
                    if cat != selected_category
                ]
                new_category = self.questionary.select(
                    "Select the new category to move this item:",
                    choices=non_empty_categories
                ).ask()
                
                if not new_category:
                    # user canceled or ctrl-c
                    continue
                
                # Remove from old category
                del data[selected_category][selected_item_key]
                edited_data[selected_category][selected_item_key] = {"deleted": True}
                # Move/add to new category (create if doesn't exist)
                data.setdefault(new_category, {})
                data[new_category][selected_item_key] = selected_item
                
                # Record the change
                edited_data.setdefault(new_category, {})
                edited_data[new_category][selected_item_key] = selected_item
                
                # Update category in SQLite database
                self.entity_manager.change_entity_category(selected_category, selected_item_key, new_category)
                
                print(f"Moved '{selected_item_key}' from '{selected_category}' to '{new_category}'.")
                continue
            
            # If user chose "Edit item"
            print(f"\nEditing item: {selected_item_key}")
            was_item_edited = False
            
            # For each field in the item
            for field, value in list(selected_item.items()):
                if field == "translation":
                    # Ask if user wants LLM translation
                    wants_llm = self.questionary.confirm(
                        f"Do you want to ask the LLM for translation options for '{selected_item_key}'?"
                    ).ask()
                    
                    if wants_llm:
                        node = selected_item.copy()
                        node['category'] = selected_category
                        node['untranslated'] = selected_item_key
                        advice = self.translator.get_translation_options(node, untranslated_text)
                        
                        print("\nLLM says:")
                        print(f"  \"{advice['message']}\"\n")
                        
                        # Add an extra "Custom" option
                        translation_options = advice['options'] + ["Custom Translation [Your Input]", "Skip"]
                        
                        # Display translations as a list
                        chosen_translation = self.questionary.select(
                            "Choose a translation option:",
                            choices=translation_options
                        ).ask()
                        
                        if chosen_translation == "Skip":
                            pass
                        elif chosen_translation == "Custom Translation [Your Input]":
                            # user types a custom translation
                            custom_val = self.questionary.text(
                                "Enter your custom translation (press Enter to cancel):"
                            ).ask()
                            if custom_val:
                                # Check if this translation already exists
                                existing = self.entity_manager.get_entity_by_translation(custom_val)
                                if existing and existing[1] != selected_item_key:
                                    # Show a warning
                                    existing_category, existing_key, _ = existing
                                    print(f"Warning: This translation is already used for '{existing_key}' in '{existing_category}'")
                                    proceed = self.questionary.confirm(
                                        "Do you want to proceed with this translation anyway?"
                                    ).ask()
                                    
                                    if not proceed:
                                        continue
                                
                                selected_item[field] = custom_val
                                selected_item["incorrect_translation"] = value
                                was_item_edited = True
                        else:
                            # Check if this translation already exists
                            existing = self.entity_manager.get_entity_by_translation(chosen_translation)
                            if existing and existing[1] != selected_item_key:
                                # Show a warning
                                existing_category, existing_key, _ = existing
                                print(f"Warning: This translation is already used for '{existing_key}' in '{existing_category}'")
                                proceed = self.questionary.confirm(
                                    "Do you want to proceed with this translation anyway?"
                                ).ask()
                                
                                if not proceed:
                                    continue
                            
                            # user picked one of the suggested translations
                            selected_item["incorrect_translation"] = value
                            selected_item[field] = chosen_translation
                            was_item_edited = True
                    
                    else:
                        # Simply prompt for updating the value
                        new_val = self.questionary.text(
                            f"{field} (current: {value}). Press Enter to keep, or type new value:",
                            default=""
                        ).ask()
                        if new_val:
                            # Check if this translation already exists
                            existing = self.entity_manager.get_entity_by_translation(new_val)
                            if existing and existing[1] != selected_item_key:
                                # Show a warning
                                existing_category, existing_key, _ = existing
                                print(f"Warning: This translation is already used for '{existing_key}' in '{existing_category}'")
                                proceed = self.questionary.confirm(
                                    "Do you want to proceed with this translation anyway?"
                                ).ask()
                                
                                if not proceed:
                                    continue
                            
                            self.logger.debug(f"selected_item_key = {selected_item_key}")
                            self.logger.debug(f"selected_item = {selected_item}")
                            selected_item[field] = new_val
                            selected_item["incorrect_translation"] = value
                            was_item_edited = True
                
                else:
                    # For non-translation fields
                    new_val = self.questionary.text(
                        f"{field} (current: {value}). Press Enter to keep, or type new value:",
                        default=""
                    ).ask()
                    
                    if new_val:
                        # If it's an int field, try to convert
                        if isinstance(value, int):
                            try:
                                selected_item[field] = int(new_val)
                            except ValueError:
                                print(f"Invalid input for {field}. Keeping original value.")
                                continue
                        else:
                            selected_item[field] = new_val
                        was_item_edited = True
            
            # Save item changes if it was edited
            if was_item_edited:
                self.logger.debug(f"selected_item_key = {selected_item_key}")
                self.logger.debug(f"selected_item = {selected_item}")
                self.logger.debug("Final data: %s", data)
                data[selected_category][selected_item_key] = selected_item
                edited_data.setdefault(selected_category, {})
                edited_data[selected_category][selected_item_key] = selected_item
                
                # Update entity in SQLite database
                translation = selected_item.get("translation", "")
                last_chapter = selected_item.get("last_chapter", "")
                incorrect_translation = selected_item.get("incorrect_translation", None)
                gender = selected_item.get("gender", None)
                
                self.entity_manager.update_entity(
                    selected_category, 
                    selected_item_key, 
                    translation=translation, 
                    last_chapter=last_chapter,
                    incorrect_translation=incorrect_translation,
                    gender=gender
                )
        
        return edited_data

    def display_results(self, end_object):
        """Display translation results to the user and save in the specified format"""
        # Get title with a default value if missing
        chapter_title = end_object.get('title', 'Untitled Chapter')
        
        # Initialize OutputFormatter if needed
        if not hasattr(self, 'output_formatter'):
            self.output_formatter = OutputFormatter(self.entity_manager.config, self.logger)
        
        # Save in the specified format
        output_path = self.output_formatter.save_output(
            end_object, 
            format=getattr(self, 'output_format', 'text'),
            book_info=getattr(self, 'book_info', None)
        )
        
        # Calculate statistics
        translated_total_words = 0
        translated_total_chars = 0
        content = end_object.get('content', [])
        
        for line in content:
            translated_total_words += len(line.split())
            translated_total_chars += len(line)
        
        # Try to copy to clipboard if available
        try:
            import pyperclip
            pyperclip.copy("\n".join(content))
            self.logger.info("Translated text copied to clipboard for pasting.")
        except ImportError:
            self.logger.info("pyperclip not available - clipboard functions disabled")
        
        # Display summary
        self.logger.info(f"TITLE: {chapter_title}")
        self.logger.info(
            "Translated. Input text is "
            + str(sum(len(line) for line in end_object.get('untranslated', [])))
            + " characters compared to "
            + str(translated_total_words)
            + " translated words ("
            + str(translated_total_chars)
            + " characters.)"
        )
        
        format_name = getattr(self, 'output_format', 'text').upper()
        print(f"Translation saved in {format_name} format to {output_path}")    

    def resolve_duplicate_entities(self, duplicates, untranslated_text):
        """
        Interactive method to resolve duplicate entities across categories.
        
        Args:
            duplicates: List of potential duplicate entities to resolve
            untranslated_text: Original text for context
            
        Returns:
            List of resolved entities with their decisions
        """
        if not self.has_rich_ui or not duplicates:
            return []
        
        resolved = []
        
        print("\n=== Entity Category Conflict Resolution ===")
        print(f"Found {len(duplicates)} potential duplicate entities across categories that need resolution.")
        
        for duplicate in duplicates:
            untranslated = duplicate['untranslated']
            translation = duplicate['translation']
            new_category = duplicate['new_category']
            existing_category = duplicate['existing_category']
            existing_translation = duplicate['existing_translation']
            
            # Find context if possible
            context = None
            if untranslated_text:
                context = self.translator.find_substring_with_context(untranslated_text, untranslated, 50)
                duplicate['context'] = context
            
            print(f"\nEntity: '{untranslated}'")
            print(f"Currently in: '{existing_category}' as '{existing_translation}'")
            print(f"Model suggests: '{new_category}' as '{translation}'")
            
            if context:
                print(f"Context: \"...{context}...\"")
            
            # Ask user what to do
            action = self.questionary.select(
                "How would you like to handle this entity?",
                choices=[
                    "Keep in existing category (reject new suggestion)",
                    "Move to new category (replace existing)",
                    "Keep in both categories (allow duplication)",
                    "Edit manually"
                ]
            ).ask()
            
            if action == "Keep in existing category (reject new suggestion)":
                # Do nothing, just record the decision
                duplicate['decision'] = 'keep_existing'
                resolved.append(duplicate)
                print(f"Decision: Keeping '{untranslated}' in '{existing_category}'")
            
            elif action == "Move to new category (replace existing)":
                # Move the entity to the new category
                result = self.entity_manager.change_entity_category(existing_category, untranslated, new_category)
                
                if result:
                    duplicate['decision'] = 'move_to_new'
                    resolved.append(duplicate)
                    print(f"Decision: Moved '{untranslated}' from '{existing_category}' to '{new_category}'")
                else:
                    print(f"Failed to move entity. See logs for details.")
            
            elif action == "Keep in both categories (allow duplication)":
                # Allow the duplication - need to add it manually as database constraints prevent this
                duplicate['decision'] = 'allow_duplicate'
                resolved.append(duplicate)
                print(f"Decision: Allowing '{untranslated}' in both '{existing_category}' and '{new_category}'")
                
                # Need to override database constraints to add this duplicate
                try:
                    conn = sqlite3.connect(self.entity_manager.db_path)
                    cursor = conn.cursor()
                    cursor.execute('''
                    INSERT INTO entities (category, untranslated, translation, last_chapter)
                    VALUES (?, ?, ?, ?)
                    ''', (new_category, untranslated, translation, duplicate.get('last_chapter', 'THIS CHAPTER')))
                    conn.commit()
                    conn.close()
                    
                    # Update memory cache
                    self.entity_manager._load_entities()
                    
                except sqlite3.Error as e:
                    self.logger.error(f"Error allowing duplicate: {e}")
                    print(f"Database error: {e}")
            
            elif action == "Edit manually":
                # Allow detailed editing
                print("\nManual Editing:")
                
                # Choose category
                target_category = self.questionary.select(
                    "Which category should this entity be in?",
                    choices=['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment']
                ).ask()
                
                # Choose translation
                custom_translation = self.questionary.text(
                    f"Enter translation for '{untranslated}' (default: '{translation}'):",
                    default=translation
                ).ask()
                
                # Apply changes
                if existing_category == target_category:
                    # Update existing entity
                    self.entity_manager.update_entity(
                        target_category, 
                        untranslated, 
                        translation=custom_translation
                    )
                    print(f"Updated '{untranslated}' in '{target_category}' with translation '{custom_translation}'")
                else:
                    # Delete from old category and add to new
                    self.entity_manager.delete_entity(existing_category, untranslated)
                    self.entity_manager.add_entity(
                        target_category,
                        untranslated,
                        custom_translation
                    )
                    print(f"Moved '{untranslated}' from '{existing_category}' to '{target_category}' with translation '{custom_translation}'")
                
                duplicate['decision'] = 'manual_edit'
                duplicate['final_category'] = target_category
                duplicate['final_translation'] = custom_translation
                resolved.append(duplicate)
        
        print("\nAll duplicate entities have been resolved.")
        return resolved
    
    def _get_book_info(self):
        """Get or create book information for EPUB output"""
        if not hasattr(self, 'output_formatter'):
            self.output_formatter = OutputFormatter(self.entity_manager.config, self.logger)
        
        return self.output_formatter.get_book_info()

    def _edit_book_info(self):
        """Edit book information for EPUB output"""
        if not hasattr(self, 'output_formatter'):
            self.output_formatter = OutputFormatter(self.entity_manager.config, self.logger)
        
        book_info = self._get_book_info()
        
        if self.has_rich_ui:
            # Use questionary for interactive editing
            print("Editing book information for EPUB output:")
            
            book_info["title"] = self.questionary.text(
                "Book title:",
                default=book_info.get("title", "Translated Book")
            ).ask()
            
            book_info["author"] = self.questionary.text(
                "Book author:",
                default=book_info.get("author", "Translator")
            ).ask()
            
            book_info["language"] = self.questionary.text(
                "Book language code (e.g., en, zh, ja):",
                default=book_info.get("language", "en")
            ).ask()
            
            book_info["description"] = self.questionary.text(
                "Book description:",
                default=book_info.get("description", "")
            ).ask()
        else:
            # Fallback to regular input
            print("Editing book information for EPUB output:")
            
            title = input(f"Book title [{book_info.get('title', 'Translated Book')}]: ")
            if title:
                book_info["title"] = title
            
            author = input(f"Book author [{book_info.get('author', 'Translator')}]: ")
            if author:
                book_info["author"] = author
            
            language = input(f"Book language code (e.g., en, zh, ja) [{book_info.get('language', 'en')}]: ")
            if language:
                book_info["language"] = language
            
            description = input(f"Book description [{book_info.get('description', '')}]: ")
            if description:
                book_info["description"] = description
        
        # Save the updated book info
        output_dir = os.path.join(self.entity_manager.config.script_dir, "output")
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        book_info_path = os.path.join(output_dir, "book_info.json")
        
        try:
            with open(book_info_path, 'w', encoding='utf-8') as f:
                json.dump(book_info, f, indent=4, ensure_ascii=False)
            print(f"Book information saved to {book_info_path}")
        except Exception as e:
            self.logger.error(f"Error saving book info: {e}")
            print(f"Error saving book info: {e}")
        
        return book_info

    def _list_queue_contents(self, summary_only=False):
        """List all items in the translation queue."""
        queue_path = os.path.join(self.entity_manager.config.script_dir, "queue.json")
        
        if not os.path.exists(queue_path):
            print("Queue file not found.")
            return
        
        try:
            with open(queue_path, 'r', encoding='utf-8') as f:
                queue = json.load(f)
                
            if not queue:
                print("Queue is empty.")
                return
                
            print(f"Queue contains {len(queue)} items:")
            
            if summary_only:
                return
                
            for i, item in enumerate(queue, 1):
                # Try to extract title from metadata
                title = None
                chapter = None
                
                if isinstance(item, list) and len(item) > 0:
                    for line in item[:5]:  # Check first few lines for metadata
                        if isinstance(line, str):
                            if line.startswith("# Title:"):
                                title = line[8:].strip()
                            elif line.startswith("# Chapter:"):
                                chapter = line[10:].strip()
                
                if title:
                    if chapter:
                        print(f"{i}. {title} (Chapter {chapter}) - {len(item)} lines")
                    else:
                        print(f"{i}. {title} - {len(item)} lines")
                else:
                    print(f"{i}. Item with {len(item)} lines")
                    
        except Exception as e:
            print(f"Error reading queue: {e}")
            
    def _process_directory(self, directory_path, sort_strategy="auto", file_pattern="*.txt"):
        """Process all text files in a directory and add them to the queue."""
        try:
            # Initialize directory processor
            processor = DirectoryProcessor(self.entity_manager.config, self.logger)
            
            print(f"Processing directory: {directory_path}")
            print(f"Sort strategy: {sort_strategy}")
            print(f"File pattern: {file_pattern}")
            
            success, num_files, message = processor.process_directory(
                directory_path, sort_strategy, file_pattern)
            
            if success:
                print(f"Success! {message}")
                
                # Show queue summary
                self._list_queue_contents(summary_only=True)
            else:
                print(f"Failed! {message}")
                
        except Exception as e:
            self.logger.error(f"Error processing directory: {e}")
            print(f"Error processing directory: {e}")
            import traceback
            traceback.print_exc()
            
    def _clear_queue(self):
        """Clear the translation queue."""
        queue_path = os.path.join(self.entity_manager.config.script_dir, "queue.json")
        
        if not os.path.exists(queue_path):
            print("Queue file not found.")
            return
        
        try:
            # Ask for confirmation
            if self.has_rich_ui:
                confirm = self.questionary.confirm("Are you sure you want to clear the entire queue?").ask()
                if not confirm:
                    print("Operation cancelled.")
                    return
            else:
                response = input("Are you sure you want to clear the entire queue? (y/n): ")
                if response.lower() != 'y':
                    print("Operation cancelled.")
                    return
            
            # Clear the queue
            with open(queue_path, 'w', encoding='utf-8') as f:
                json.dump([], f)
            
            print("Queue has been cleared.")
                
        except Exception as e:
            print(f"Error clearing queue: {e}")


class WebUserInterface(UserInterface):
    """Basic web interface implementation - could be expanded in future"""
    
    def __init__(self, translator: TranslationEngine, entity_manager: EntityManager, logger: Logger):
        super().__init__(translator, entity_manager, logger)
        # Any web-specific initialization would go here
    
    def get_input(self) -> List[str]:
        """
        In a real implementation, this would get input from a web form
        Placeholder implementation for now
        """
        raise NotImplementedError("Web interface not yet implemented")
    
    def display_results(self, results: Dict) -> None:
        """
        In a real implementation, this would display results in the web interface
        Placeholder implementation for now
        """
        raise NotImplementedError("Web interface not yet implemented")
    
    def review_entities(self, entities: Dict, untranslated_text: List[str]) -> Dict:
        """
        In a real implementation, this would provide a web form for entity review
        Placeholder implementation for now
        """
        raise NotImplementedError("Web interface not yet implemented")


class TranslationApp:
    """
    Main application class that ties everything together.
    This class creates all the necessary components and manages the workflow.
    """
    
    def __init__(self, ui_type="cli"):
        # Initialize configuration
        self.config = TranslationConfig()
        
        # Set up logger
        self.logger = Logger(self.config)
        
        # Initialize entity manager with SQLite database
        self.entity_manager = EntityManager(self.config, self.logger)
        
        # Handle migration from JSON to SQLite if needed
        self._migrate_json_to_sqlite_if_needed()
        
        # Create translation engine
        self.translator = TranslationEngine(self.config, self.logger, self.entity_manager)
        
        # Create appropriate UI
        if ui_type.lower() == "cli":
            self.ui = CommandLineInterface(self.translator, self.entity_manager, self.logger)
        elif ui_type.lower() == "web":
            self.ui = WebUserInterface(self.translator, self.entity_manager, self.logger)
        else:
            raise ValueError(f"Unsupported UI type: {ui_type}")
    
    def _migrate_json_to_sqlite_if_needed(self):
        """
        Check if we need to migrate from JSON to SQLite by looking for an entities.json file
        and a possibly empty SQLite database.
        """
        
        json_path = os.path.join(self.config.script_dir, "entities.json")
        
        # Check if the JSON file exists and SQLite DB is empty
        if os.path.exists(json_path):
            try:
                # Check if the database is empty
                conn = sqlite3.connect(self.entity_manager.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM entities")
                count = cursor.fetchone()[0]
                conn.close()
                
                if count == 0:
                    # Database is empty, but JSON file exists - perform migration
                    self.logger.info("Found entities.json but empty SQLite database. Migrating data...")
                    
                    # Import the JSON data
                    success = self.entity_manager.import_from_json("entities.json")
                    
                    if success:
                        self.logger.info("Successfully migrated entities from JSON to SQLite")
                        # Optionally, rename the original JSON file to indicate it's been migrated
                        backup_path = os.path.join(self.config.script_dir, "entities.json.bak")
                        try:
                            os.rename(json_path, backup_path)
                            self.logger.info(f"Renamed original JSON file to {backup_path}")
                        except OSError as e:
                            self.logger.warning(f"Could not rename JSON file: {e}")
                    else:
                        self.logger.error("Failed to migrate data from JSON to SQLite")
                else:
                    self.logger.info(f"SQLite database already contains {count} entities, no migration needed")
                    
            except sqlite3.Error as e:
                self.logger.error(f"Error checking database during migration: {e}")
    
    def run(self):
        """Run the full translation process"""
        try:
            return self.ui.run_translation()
        except Exception as e:
            self.logger.error(f"Error during translation: {e}")
            raise


def main():
    """Entry point function"""
    app = TranslationApp()
    app.run()


if __name__ == "__main__":
    main()
