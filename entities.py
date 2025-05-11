import json
import unicodedata
import sqlite3
import os
from typing import Dict, List, Optional, Any, Union, Tuple
from itertools import zip_longest

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
