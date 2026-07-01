import re
import traceback
import unicodedata
from itertools import zip_longest
from db.core import DEFAULT_CATEGORIES, INCLUDE_SIMILAR_PREFIX


class EntitiesRepo:
    """Entity dictionary: cache loading, CRUD, text matching, and import/export."""

    def _load_entities(self, book_id=None):
        """Load existing entities from database into memory cache"""

        # Build default entity categories dict, using book-specific categories if available
        if book_id is not None:
            cats = self.get_book_categories(book_id)
        else:
            cats = DEFAULT_CATEGORIES
        default_entities = {cat: {} for cat in cats}

        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                # Get all entities grouped by category
                if book_id is not None:
                    cursor.execute('''
                SELECT category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id, note
                FROM entities
                WHERE book_id = ? OR book_id IS NULL
                ''', (book_id,))
                else:
                    cursor.execute('''
                SELECT category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id, note
                FROM entities
                ''')

                rows = cursor.fetchall()

                # Process results
                entities = default_entities.copy()
                for row in rows:
                    category, untranslated, translation, last_chapter, incorrect_translation, gender, entity_book_id, note = row

                    # Initialize category if needed (should be unnecessary with defaults)
                    entities.setdefault(category, {})

                    # Create entity entry
                    entity_data = {"translation": translation, "last_chapter": last_chapter}

                    # Add optional attributes if they exist
                    if incorrect_translation:
                        entity_data["incorrect_translation"] = incorrect_translation
                    if gender:
                        entity_data["gender"] = gender
                    if entity_book_id:
                        entity_data["book_id"] = entity_book_id
                    if note:
                        entity_data["note"] = note
                    
                    # Add to our entities dictionary
                    entities[category][untranslated] = entity_data
            with self._entities_lock:
                self.entities = entities
            self.logger.debug(f"Loaded {sum(len(cat) for cat in entities.values())} entities from database")
            return entities

        except Exception as e:
            self.logger.error(f"Error loading entities from database: {e}")
            # Return default empty structure on error
            with self._entities_lock:
                self.entities = default_entities
            return default_entities

    def reload_entities(self, book_id=None):
        """Public alias for _load_entities(): reload the entity cache."""
        return self._load_entities(book_id)

    def combine_json_entities(self, old_entities, new_entities):
        """
        Merges two JSON-like dictionaries, updating 'old_entities' with entries
        from 'new_entities'. The keys are entity categories, and values are dictionaries
        of untranslated-translated pairs. Entries from 'new_entities' will replace
        existing ones from 'old_entities' if they have the same keys.
        """
        # Create a copy using union of keys from both dicts
        all_categories = set(old_entities.keys()) | set(new_entities.keys())
        result = {cat: old_entities.get(cat, {}).copy() for cat in all_categories}

        # Update with new entities
        for cat in all_categories:
            new_category_dict = new_entities.get(cat, {})
            result.setdefault(cat, {}).update(new_category_dict)

        return result

    def save_entities(self):
        """Save the current entities cache to the SQLite database"""
        # Iterate a shallow snapshot so the translation thread mutating the
        # cache mid-save can't raise "dictionary changed size during iteration".
        with self._entities_lock:
            snapshot = {cat: dict(ents) for cat, ents in self.entities.items()}
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                # Track which entities we've already saved to avoid duplicates
                processed_entities = set()

                # For each category and entity in memory cache
                for category, entities in snapshot.items():
                    for untranslated, entity_data in entities.items():
                        translation = entity_data.get('translation', '')
                        last_chapter = entity_data.get('last_chapter', '')
                        incorrect_translation = entity_data.get('incorrect_translation', None)
                        gender = entity_data.get('gender', None)
                        book_id = entity_data.get('book_id', None)  # Include book_id
                        note = entity_data.get('note', None)
                        
                        # Create a unique key to track this entity
                        entity_key = (untranslated, book_id)

                        # Skip if we've already processed this entity
                        if entity_key in processed_entities:
                            continue

                        # Add to processed set
                        processed_entities.add(entity_key)

                        # Look for existing entity to determine whether to insert or update
                        if book_id is not None:
                            cursor.execute('''
                        SELECT id FROM entities
                        WHERE untranslated = ? AND book_id = ?
                        ''', (untranslated, book_id))
                        else:
                            cursor.execute('''
                        SELECT id FROM entities
                        WHERE untranslated = ? AND book_id IS NULL
                        ''', (untranslated,))
                        
                        existing = cursor.fetchone()
                        
                        if existing:
                            # Update existing entity
                            entity_id = existing[0]
                            cursor.execute('''
                        UPDATE entities
                        SET category = ?, translation = ?, last_chapter = ?, incorrect_translation = ?, gender = ?, note = ?
                        WHERE id = ?
                        ''', (category, translation, last_chapter, incorrect_translation, gender, note, entity_id))
                        else:
                            # Insert new entity
                            cursor.execute('''
                        INSERT INTO entities
                        (category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id, note)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id, note))
            self.logger.info("Entities saved to database successfully")
        except Exception as e:
            self.logger.error(f"Error saving entities to database: {e}\n{traceback.format_exc()}")
            # Consider creating a backup JSON in this case
            self.save_json_file("entities_backup.json", snapshot)
            self.logger.info("Created backup of entities in entities_backup.json")
            if self.strict_writes:
                raise

    def entities_inside_text(self, text_lines, all_entities, current_chapter, do_count=True):
        """
        Extracts entities mentioned in the given text and updates their running count and last chapter.

        Returns a two-bucket dict per call:
          - "exact":   entities whose full source form appears literally in the chapter text.
          - "similar": entities (length >= 3) NOT in "exact" whose first-2 or last-2
                      source chars appear in the chapter text. These are reference-only
                      hints for naming-style consistency (titles, honorifics, surnames).
                      Each carries a "match" field of "prefix", "suffix", or "prefix+suffix".

        Args:
            text_lines (list of str): The chapter's text content split into lines.
            all_entities (dict): The complete entities dictionary with global counts.
            current_chapter (int or str): The current chapter number.
            do_count (bool): Defaults to True. Set to False if regenerating system prompt to avoid double counting.
        """
        # Ensure combined_text is a string
        if isinstance(text_lines, list):
            combined_text = ' '.join(text_lines)
        elif isinstance(text_lines, str):
            combined_text = text_lines
        else:
            self.logger.error(f"Unexpected type for text_lines: {type(text_lines)}")
            combined_text = str(text_lines)

        self.logger.debug(f"entities_inside_text: type of combined_text = {type(combined_text)}")

        combined_text = self._normalize_text(combined_text)

        if not all_entities:
            self.logger.error("all_entities is empty, querying database... we will just return a blank dict for now")
            return {"exact": {}, "similar": {}}

        exact = {}
        similar = {}

        # Pass 1: literal substring (exact) match — current behaviour.
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
                if key not in exact:
                    exact[key] = {
                        "translation": value["translation"],
                        "last_chapter": current_chapter,
                    }
                    if value.get("note"):
                        exact[key]["note"] = value["note"]
                all_entities[key]["last_chapter"] = current_chapter

        # Build anchor sets from exact: each exact entity already gives the model
        # a translation reference for its leading and trailing bigrams. Piling on
        # other similar entries that share the same anchor is pure noise.
        exact_prefixes = set()
        exact_suffixes = set()
        for ek in exact:
            ek_norm = self._normalize_text(ek)
            if len(ek_norm) >= 2:
                exact_prefixes.add(ek_norm[:2])
                exact_suffixes.add(ek_norm[-2:])

        # Pass 2: prefix/suffix similarity match — reference-only consistency hints.
        for key, value in all_entities.items():
            if key in exact:
                continue
            key_normalized = self._normalize_text(key)
            if len(key_normalized) < 3:
                # 1- or 2-char keys collapse to the whole entity, already handled by pass 1.
                continue

            prefix = key_normalized[:2]
            suffix = key_normalized[-2:]

            prefix_in_text = prefix in combined_text
            suffix_in_text = suffix in combined_text

            if not (prefix_in_text or suffix_in_text):
                continue

            # If either of the candidate's anchors fired AND that anchor is
            # already represented by an exact entity, drop the whole candidate.
            # The model already has a translation reference for that anchor;
            # piling on more entities sharing it is noise (e.g. once we have
            # any exact ending in 真君, no other *真君 entity should appear in
            # similar regardless of which half hit).
            if (prefix_in_text and prefix in exact_prefixes) or (
                suffix_in_text and suffix in exact_suffixes
            ):
                continue

            prefix_hit = INCLUDE_SIMILAR_PREFIX and prefix_in_text
            suffix_hit = suffix_in_text

            if not (prefix_hit or suffix_hit):
                continue

            # For entities of length <= 4, prefix+suffix bigrams together cover
            # the whole entity. If both halves appear in text but the entity
            # itself didn't land in exact, the halves are non-contiguous — a
            # false positive (coincidental co-occurrence of unrelated bigrams).
            if prefix_hit and suffix_hit and len(key_normalized) <= 4:
                continue

            if prefix_hit and suffix_hit:
                match_kind = "prefix+suffix"
            elif suffix_hit:
                match_kind = "suffix"
            else:
                match_kind = "prefix"

            similar[key] = {
                "translation": value["translation"],
                "last_chapter": value.get("last_chapter", ""),
                "match": match_kind,
            }
            if value.get("note"):
                similar[key]["note"] = value["note"]

        return {"exact": exact, "similar": similar}

    def find_new_entities(self, old_data, new_data):
        """
        Return a dictionary of all entities that are present in new_data
        but do NOT exist in old_data at all (in any category).
        """
        # Build a set of all known untranslated keys across every category
        all_old_keys = set()
        for cat_entities in old_data.values():
            all_old_keys.update(cat_entities.keys())

        newly_added = {}

        for category, new_items in new_data.items():
            for entity_name, entity_info in new_items.items():
                if entity_name not in all_old_keys:
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

        if not old_translation or old_translation == new_translation:
            self.logger.debug(f"Skipping substitution for '{new_translation}' — no incorrect_translation set")
            return translated_text

        self.logger.info(f"We will update '{old_translation}' for '{new_translation}'...")

        def match_case(match):
            matched_text = match.group()
            old_words = matched_text.split()
            new_words = new_translation.split()

            transformed_words = []
            for old_w, new_w in zip_longest(old_words, new_words, fillvalue=""):
                if not new_w:
                    continue
                if not old_w:
                    transformed_words.append(new_w)
                    continue
                # Preserve user-entered casing in new_w (e.g. "HeavenNet"); only
                # adjust the first character. .capitalize()/.lower() destroy
                # internal caps.
                if old_w.isupper() and len(old_w) > 1:
                    transformed_words.append(new_w.upper())
                elif old_w[0].isupper():
                    transformed_words.append(new_w[0].upper() + new_w[1:])
                elif old_w[0].islower():
                    transformed_words.append(new_w[0].lower() + new_w[1:])
                else:
                    transformed_words.append(new_w)

            return " ".join(transformed_words).strip()
        
        # Compile pattern for case-insensitive search
        pattern = re.compile(re.escape(old_translation), re.IGNORECASE)
        for i in range(len(translated_text)):
            translated_text[i] = pattern.sub(match_case, translated_text[i])
        
        return translated_text

    def _normalize_text(self, text):
        """Normalize text for consistent comparison"""
        return unicodedata.normalize('NFC', text)

    def add_entity(self, category, untranslated, translation, book_id=None, last_chapter=None, incorrect_translation=None, gender=None, origin_chapter=None, note=None):
        """
        Add a new entity to the database.
        Returns True if successful, False if the entity already exists in a different category.
        
        Args:
            category: Entity category
            untranslated: Original untranslated text
            translation: Translated text
            book_id: Book ID (optional - if None, entity is global)
            last_chapter: Last chapter where entity was found
            incorrect_translation: Previous incorrect translation
            gender: Entity gender (for characters)
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                # Check if entity already exists for this book (regardless of category)
                if book_id is not None:
                    cursor.execute('''
                SELECT id, origin_chapter, category FROM entities
                WHERE untranslated = ? AND book_id = ?
                ''', (untranslated, book_id))
                else:
                    cursor.execute('''
                SELECT id, origin_chapter, category FROM entities
                WHERE untranslated = ? AND book_id IS NULL
                ''', (untranslated,))

                same_cat = cursor.fetchone()
                if same_cat:
                    # Update existing — preserve origin_chapter, gender, and note if not explicitly provided
                    existing_id = same_cat[0]
                    effective_origin = origin_chapter if origin_chapter is not None else (same_cat[1] if same_cat[1] is not None else last_chapter)
                    if gender is None or note is None:
                        cursor.execute('SELECT gender, note FROM entities WHERE id = ?', (existing_id,))
                        existing = cursor.fetchone()
                        if gender is None and existing:
                            gender = existing[0]
                        if note is None and existing:
                            note = existing[1]
                    cursor.execute('''
                UPDATE entities
                SET category = ?, translation = ?, last_chapter = ?, incorrect_translation = ?, gender = ?, origin_chapter = ?, note = ?
                WHERE id = ?
                ''', (category, translation, last_chapter, incorrect_translation, gender, effective_origin, note, existing_id))
                else:
                    # Insert new entity — fall back to last_chapter if origin_chapter not specified
                    effective_origin = origin_chapter if origin_chapter is not None else last_chapter
                    cursor.execute('''
                INSERT INTO entities
                (category, untranslated, translation, book_id, last_chapter, incorrect_translation, gender, origin_chapter, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (category, untranslated, translation, book_id, last_chapter, incorrect_translation, gender, effective_origin, note))
            
            # Update the in-memory cache
            entity_data = {"translation": translation}
            if last_chapter:
                entity_data["last_chapter"] = last_chapter
            if incorrect_translation:
                entity_data["incorrect_translation"] = incorrect_translation
            if gender:
                entity_data["gender"] = gender
            if book_id:
                entity_data["book_id"] = book_id
            if note:
                entity_data["note"] = note

            with self._entities_lock:
                self.entities.setdefault(category, {})
                self.entities[category][untranslated] = entity_data
            return True
                
        except Exception as e:
            self.logger.error(f"Error adding entity to database: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def update_entity(self, category, untranslated, **kwargs):
        """
        Update an existing entity with new values.

        If book_id is provided along with other fields, it's used to identify which entity
        to update (WHERE clause) while other fields are updated.
        If book_id is the ONLY field being updated, it changes the entity's book assignment.

        Returns True if the entity was updated, False if it wasn't found.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                # Check if book_id is the only field being updated (changing book assignment)
                is_only_book_id = 'book_id' in kwargs and len(kwargs) == 1

                # Build the SET clause dynamically based on provided kwargs
                set_clause = []
                values = []
                where_book_id = None

                for key, value in kwargs.items():
                    if key in ['translation', 'last_chapter', 'incorrect_translation', 'gender', 'note', 'category']:
                        set_clause.append(f"{key} = ?")
                        values.append(value)
                    elif key == 'book_id':
                        if is_only_book_id:
                            # Changing book assignment - include in SET clause
                            set_clause.append(f"{key} = ?")
                            values.append(value)
                        else:
                            # Identifying which entity to update - use in WHERE clause
                            where_book_id = value

                if not set_clause:
                    self.logger.warning("No valid fields to update")
                    return False

                # Build WHERE clause
                where_clause = "WHERE category = ? AND untranslated = ?"
                where_values = [category, untranslated]

                # Include book_id in WHERE clause only if we're not changing it
                if not is_only_book_id:
                    if where_book_id is not None:
                        where_clause += " AND book_id = ?"
                        where_values.append(where_book_id)
                    else:
                        where_clause += " AND book_id IS NULL"

                # Complete the parameter list
                values.extend(where_values)

                # Execute the update
                cursor.execute(f'''
            UPDATE entities
            SET {', '.join(set_clause)}
            {where_clause}
            ''', values)
                
                if cursor.rowcount == 0:
                    self.logger.warning(f"Entity '{untranslated}' in category '{category}' not found for update")
                    return False

            # Update the in-memory cache
            with self._entities_lock:
                if category in self.entities and untranslated in self.entities[category]:
                    new_category = kwargs.get('category')
                    for key, value in kwargs.items():
                        if key in ['translation', 'last_chapter', 'incorrect_translation', 'gender', 'note']:
                            self.entities[category][untranslated][key] = value
                        elif key == 'book_id':
                            if is_only_book_id:
                                # Changing book assignment
                                if value is None:
                                    if 'book_id' in self.entities[category][untranslated]:
                                        del self.entities[category][untranslated]['book_id']
                                else:
                                    self.entities[category][untranslated]['book_id'] = value
                    # If category is changing, move the entity in the cache
                    if new_category and new_category != category:
                        entity_data = self.entities[category].pop(untranslated)
                        self.entities.setdefault(new_category, {})[untranslated] = entity_data

            return True
            
        except Exception as e:
            self.logger.error(f"Error updating entity in database: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def rename_entity_untranslated(self, category, old_untranslated, new_untranslated, book_id=None):
        """Rename an entity's `untranslated` key. Used by trad→simp key conversion.

        Returns: 'renamed' on success, 'not_found' if the source row is missing,
        'unchanged' if old == new, 'conflict' if the destination key already
        exists for this book (caller must resolve), 'error' on DB failure.
        """
        if old_untranslated == new_untranslated:
            return 'unchanged'

        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                book_clause = "book_id = ?" if book_id is not None else "book_id IS NULL"
                book_params = (book_id,) if book_id is not None else ()

                cursor.execute(
                    f"SELECT 1 FROM entities WHERE untranslated = ? AND {book_clause}",
                    (new_untranslated,) + book_params,
                )
                if cursor.fetchone():
                    return 'conflict'

                cursor.execute(
                    f"UPDATE entities SET untranslated = ? "
                    f"WHERE category = ? AND untranslated = ? AND {book_clause}",
                    (new_untranslated, category, old_untranslated) + book_params,
                )
                if cursor.rowcount == 0:
                    return 'not_found'

            with self._entities_lock:
                if category in self.entities and old_untranslated in self.entities[category]:
                    self.entities[category][new_untranslated] = self.entities[category].pop(old_untranslated)

            return 'renamed'

        except Exception as e:
            self.logger.error(f"Error renaming entity key '{old_untranslated}' → '{new_untranslated}': {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return 'error'

    def delete_entity(self, category, untranslated):
        """
        Delete an entity from the database.
        Returns True if the entity was deleted, False if it wasn't found.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
            DELETE FROM entities 
            WHERE category = ? AND untranslated = ?
            ''', (category, untranslated))
                
                if cursor.rowcount == 0:
                    self.logger.warning(f"Entity '{untranslated}' in category '{category}' not found for deletion")
                    return False
            
            # Update the in-memory cache
            with self._entities_lock:
                if category in self.entities and untranslated in self.entities[category]:
                    del self.entities[category][untranslated]

            return True
            
        except Exception as e:
            self.logger.error(f"Error deleting entity from database: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def change_entity_category(self, old_category, untranslated, new_category):
        """
        Move an entity from one category to another.
        Returns True if the entity was moved, False otherwise.
        """
        try:
            with self._conn() as conn:
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
                    return False
                
                # Check if entity already exists in the target category
                cursor.execute('''
            SELECT id FROM entities 
            WHERE category = ? AND untranslated = ?
            ''', (new_category, untranslated))
                
                if cursor.fetchone():
                    self.logger.warning(f"Entity '{untranslated}' already exists in target category '{new_category}'")
                    return False
                
                # Update the category
                cursor.execute('''
            UPDATE entities 
            SET category = ?
            WHERE category = ? AND untranslated = ?
            ''', (new_category, old_category, untranslated))
            
            # Update the in-memory cache
            with self._entities_lock:
                if old_category in self.entities and untranslated in self.entities[old_category]:
                    entity_data_dict = self.entities[old_category][untranslated]
                    del self.entities[old_category][untranslated]

                    self.entities.setdefault(new_category, {})
                    self.entities[new_category][untranslated] = entity_data_dict

            return True
            
        except Exception as e:
            self.logger.error(f"Error changing entity category in database: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def get_entity_by_translation(self, translation):
        """
        Find an entity by its translation.
        Returns a tuple (category, untranslated, entity_data) if found, None otherwise.
        
        This is useful for finding duplicates by translation rather than by untranslated text.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
            SELECT category, untranslated, last_chapter, incorrect_translation, gender 
            FROM entities 
            WHERE translation = ?
            ''', (translation,))
                
                rows = cursor.fetchall()
            
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
            
        except Exception as e:
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
            with self._conn() as conn:
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
                        
                        cursor.execute(self.backend.upsert_entity_sql(),
                            (category, untranslated, translation, last_chapter, incorrect_translation, gender))
                        count += 1
            self.logger.info(f"Imported {count} entities from JSON file '{filepath}'")
            
            # Refresh the in-memory cache
            self._load_entities()
            return True
            
        except Exception as e:
            self.logger.error(f"Error importing entities from JSON: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def get_all_entities_for_review(self, book_id=None, category=None):
        """
        Load all entities from database for review purposes.

        Args:
            book_id: Filter by book ID (None = all books, including global entities)
            category: Filter by specific category (None = all categories)

        Returns:
            Dict mapping categories to dictionaries of {untranslated: entity_data}
            Each entity_data contains: translation, last_chapter, incorrect_translation,
            gender, book_id, category
        """
        # Build default categories from book config or global defaults
        if book_id is not None:
            cats = self.get_book_categories(book_id)
        else:
            cats = DEFAULT_CATEGORIES
        default_entities = {cat: {} for cat in cats}

        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                # Build SQL query with filters
                query = '''
                SELECT category, untranslated, translation, last_chapter,
                       incorrect_translation, gender, book_id, note
                FROM entities
                WHERE 1=1
            '''
                params = []

                # Add book_id filter
                if book_id is not None:
                    query += ' AND (book_id = ? OR book_id IS NULL)'
                    params.append(book_id)

                # Add category filter
                if category is not None:
                    query += ' AND category = ?'
                    params.append(category)

                # Order for predictable listing
                query += ' ORDER BY category, untranslated'

                cursor.execute(query, params)
                rows = cursor.fetchall()

            # Process results
            entities = default_entities.copy()
            for row in rows:
                cat, untranslated, translation, last_chapter, incorrect_translation, gender, entity_book_id, note = row

                # Initialize category if needed
                entities.setdefault(cat, {})

                # Create entity entry
                entity_data = {
                    "translation": translation,
                    "last_chapter": last_chapter,
                    "category": cat
                }

                # Add optional attributes if they exist
                if incorrect_translation:
                    entity_data["incorrect_translation"] = incorrect_translation
                if gender:
                    entity_data["gender"] = gender
                if entity_book_id:
                    entity_data["book_id"] = entity_book_id
                if note:
                    entity_data["note"] = note

                # Add to our entities dictionary
                entities[cat][untranslated] = entity_data

            self.logger.debug(f"Loaded {sum(len(cat) for cat in entities.values())} entities for review")
            return entities

        except Exception as e:
            self.logger.error(f"Error loading entities for review: {e}")
            return default_entities

    def find_chapters_using_entity(self, untranslated_text, book_id=None):
        """
        Find all chapters that contain a specific entity.

        Args:
            untranslated_text: The untranslated entity text to search for
            book_id: Optional book_id to limit search scope

        Returns:
            List of chapter metadata dicts containing: chapter_id, book_id,
            chapter_number, title, book_title
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                # Search in both untranslated and translated content
                if book_id is not None:
                    cursor.execute('''
                SELECT c.id, c.book_id, c.chapter_number, c.title, b.title as book_title
                FROM chapters c
                JOIN books b ON c.book_id = b.id
                WHERE c.book_id = ?
                AND (c.untranslated_content LIKE ? OR c.translated_content LIKE ?)
                ORDER BY c.chapter_number
                ''', (book_id, f'%{untranslated_text}%', f'%{untranslated_text}%'))
                else:
                    cursor.execute('''
                SELECT c.id, c.book_id, c.chapter_number, c.title, b.title as book_title
                FROM chapters c
                JOIN books b ON c.book_id = b.id
                WHERE c.untranslated_content LIKE ? OR c.translated_content LIKE ?
                ORDER BY b.title, c.chapter_number
                ''', (f'%{untranslated_text}%', f'%{untranslated_text}%'))

                rows = cursor.fetchall()

            results = []
            for row in rows:
                results.append({
                    "chapter_id": row[0],
                    "book_id": row[1],
                    "chapter_number": row[2],
                    "chapter_title": row[3],
                    "book_title": row[4]
                })

            return results

        except Exception as e:
            self.logger.error(f"Error finding chapters using entity: {e}")
            return []
