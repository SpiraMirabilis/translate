import re
import datetime
import traceback
import unicodedata
from itertools import zip_longest
from db.core import DEFAULT_CATEGORIES, INCLUDE_SIMILAR_PREFIX

# Sentinel: distinguishes "book_id not passed" (legacy all-books behavior)
# from an explicit book_id=None (global entities only).
_UNSCOPED = object()

# Sentinel: distinguishes "no note argument" from an explicit note=None (clear it).
_UNSET = object()


class EntitiesRepo:
    """Entity dictionary: cache loading, CRUD, text matching, and import/export."""

    def get_entities_snapshot(self, book_id=None):
        """Build and return this book's entity dict WITHOUT touching self.entities.

        The translation path uses this instead of the shared cache. `self.entities`
        holds one book at a time, so two concurrent per-book jobs would otherwise
        swap the glossary out from under each other between chunks — book A's
        chapter prompted with book B's entities. That corruption is semantic, not
        structural, so `_entities_lock` cannot prevent it; giving each run its own
        snapshot can.

        Callers that genuinely want the shared cache refreshed (the admin Entities
        and Dictionary pages) should use _load_entities()/reload_entities().
        """
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

            self.logger.debug(f"Loaded {sum(len(cat) for cat in entities.values())} entities from database")
            return entities

        except Exception as e:
            self.logger.error(f"Error loading entities from database: {e}")
            # Return default empty structure on error
            return default_entities

    def _load_entities(self, book_id=None):
        """Load entities from the database into the shared in-memory cache.

        Thin wrapper over get_entities_snapshot() so there is one query
        implementation. The translation path deliberately does NOT call this —
        see get_entities_snapshot().
        """
        entities = self.get_entities_snapshot(book_id)
        with self._entities_lock:
            self.entities = entities
        return entities

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

                # One upfront query maps (untranslated, book_id) -> id; the old
                # per-entity SELECT-then-write was 2×N round-trips across every
                # book's entities on each save.
                cursor.execute("SELECT id, untranslated, book_id FROM entities")
                id_by_key = {(row[1], row[2]): row[0] for row in cursor.fetchall()}

                processed_entities = set()
                updates = []
                inserts = []

                # For each category and entity in memory cache
                for category, entities in snapshot.items():
                    for untranslated, entity_data in entities.items():
                        translation = entity_data.get('translation', '')
                        last_chapter = entity_data.get('last_chapter', '')
                        incorrect_translation = entity_data.get('incorrect_translation', None)
                        gender = entity_data.get('gender', None)
                        book_id = entity_data.get('book_id', None)  # Include book_id
                        # `note` is deliberately NOT flushed from the cache: every
                        # note write must go through set_entity_note to land in
                        # entity_note_revisions, and a bulk executemany can't do
                        # that. Omitting the column leaves the stored note intact
                        # (the cache is loaded from it in the first place).

                        # Skip duplicates within the snapshot
                        entity_key = (untranslated, book_id)
                        if entity_key in processed_entities:
                            continue
                        processed_entities.add(entity_key)

                        entity_id = id_by_key.get(entity_key)
                        if entity_id is not None:
                            updates.append((category, translation, last_chapter,
                                            incorrect_translation, gender, entity_id))
                        else:
                            inserts.append((category, untranslated, translation, last_chapter,
                                            incorrect_translation, gender, book_id))

                if updates:
                    cursor.executemany('''
                        UPDATE entities
                        SET category = ?, translation = ?, last_chapter = ?, incorrect_translation = ?, gender = ?
                        WHERE id = ?
                        ''', updates)
                if inserts:
                    cursor.executemany('''
                        INSERT INTO entities
                        (category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', inserts)
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
                if do_count:
                    # do_count=False = prompt regeneration; advancing
                    # last_chapter there double-counted the chapter.
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

    def add_entity(self, category, untranslated, translation, book_id=None, last_chapter=None, incorrect_translation=None, gender=None, origin_chapter=None, note=None, note_author='human', note_chapter=None, note_reason=None, gender_author='human', gender_chapter=None, gender_reason=None):
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
            gender: Entity gender (for gender-tracked categories). On an entity
                that already exists this goes through set_entity_gender, so
                changing one is recorded in entity_gender_revisions like any
                other change; on a brand-new row it is simply the row's initial
                value and no revision is written.
            note: Translation guidance. Written through set_entity_note, so
                attaching a note here is recorded in entity_note_revisions like
                any later change — that is what makes a note's whole life
                reconstructable at an arbitrary chapter.
            note_author: 'model' | 'human' | 'script' — who wrote this note.
            note_chapter: Chapter the note was written at; defaults to
                last_chapter, then origin_chapter.
            note_reason: Optional free text stored with the revision.
            gender_author / gender_chapter / gender_reason: the same three
                annotations for a gender change.
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
                    # Update existing — preserve origin_chapter and gender if not explicitly provided.
                    # `note` and `gender` are deliberately absent from the SET list:
                    # leaving the columns alone preserves them, and any actual write
                    # happens below through set_entity_note / set_entity_gender so it
                    # lands in that column's history.
                    existing_id = same_cat[0]
                    effective_origin = origin_chapter if origin_chapter is not None else (same_cat[1] if same_cat[1] is not None else last_chapter)
                    cursor.execute('''
                UPDATE entities
                SET category = ?, translation = ?, last_chapter = ?, incorrect_translation = ?, origin_chapter = ?
                WHERE id = ?
                ''', (category, translation, last_chapter, incorrect_translation, effective_origin, existing_id))
                    entity_id = existing_id
                    if gender is not None:
                        self.set_entity_gender(
                            entity_id, gender, author=gender_author,
                            chapter_number=(gender_chapter if gender_chapter is not None
                                            else (last_chapter if last_chapter is not None else effective_origin)),
                            reason=gender_reason, cursor=cursor)
                    else:
                        # Keep the cache mirror below honest about the untouched gender.
                        cursor.execute('SELECT gender FROM entities WHERE id = ?', (existing_id,))
                        existing = cursor.fetchone()
                        if existing:
                            gender = existing[0]
                else:
                    # Insert new entity — fall back to last_chapter if origin_chapter not specified
                    effective_origin = origin_chapter if origin_chapter is not None else last_chapter
                    cursor.execute('''
                INSERT INTO entities
                (category, untranslated, translation, book_id, last_chapter, incorrect_translation, gender, origin_chapter)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (category, untranslated, translation, book_id, last_chapter, incorrect_translation, gender, effective_origin))
                    entity_id = cursor.lastrowid

                if note is not None:
                    self.set_entity_note(
                        entity_id, note, author=note_author,
                        chapter_number=(note_chapter if note_chapter is not None
                                        else (last_chapter if last_chapter is not None else effective_origin)),
                        reason=note_reason, cursor=cursor)
                else:
                    # Keep the cache mirror below honest about the untouched note.
                    cursor.execute('SELECT note FROM entities WHERE id = ?', (entity_id,))
                    row = cursor.fetchone()
                    note = row[0] if row else None
            
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

    def update_entity(self, category, untranslated, note_author='human',
                      note_chapter=None, note_reason=None, **kwargs):
        """
        Update an existing entity with new values.

        If book_id is provided along with other fields, it's used to identify which entity
        to update (WHERE clause) while other fields are updated.
        If book_id is the ONLY field being updated, it changes the entity's book assignment.

        A `note` in kwargs is diverted to set_entity_note and a `gender` to
        set_entity_gender, so each is recorded in that column's revision history
        — no write anywhere may bypass them. Both revisions are annotated with
        the note_author/note_chapter/note_reason arguments (this method's callers
        are human-driven CLI edits, where the two changes share an author).

        Returns True if the entity was updated, False if it wasn't found.
        """
        note_write = kwargs.pop('note', _UNSET)
        gender_write = kwargs.pop('gender', _UNSET)
        if note_write is not _UNSET or gender_write is not _UNSET:
            entity_id = self.get_entity_id(kwargs.get('book_id'), untranslated, category)
            if entity_id:
                if note_write is not _UNSET:
                    self.set_entity_note(entity_id, note_write, author=note_author,
                                         chapter_number=note_chapter, reason=note_reason)
                if gender_write is not _UNSET:
                    self.set_entity_gender(entity_id, gender_write, author=note_author,
                                           chapter_number=note_chapter, reason=note_reason)
            else:
                self.logger.warning(
                    f"update_entity: '{untranslated}' not found; note/gender not written")
                if not kwargs:
                    return False
            # With the note/gender removed, a lone book_id was an identifier for
            # the row we just wrote — not a request to move the entity to another
            # book (which is what book_id-alone means to the SQL below).
            if not kwargs or set(kwargs) == {'book_id'}:
                return True
        if not kwargs:
            return True
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
                    if key in ['translation', 'last_chapter', 'incorrect_translation', 'category']:
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
                        if key in ['translation', 'last_chapter', 'incorrect_translation']:
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

    def delete_entity(self, category, untranslated, book_id=_UNSCOPED):
        """
        Delete an entity from the database.

        book_id scoping (default is the legacy unscoped behavior, which hits
        EVERY book sharing the source term — avoid it from book contexts):
          - omitted: delete across all books (legacy)
          - None: delete the global (book_id IS NULL) row only
          - int: delete that book's row; if the book has none, fall back to
            the global row (the row that book's translations actually see)
        Returns True if the entity was deleted, False if it wasn't found.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                if book_id is _UNSCOPED:
                    cursor.execute('''
            DELETE FROM entities
            WHERE category = ? AND untranslated = ?
            ''', (category, untranslated))
                elif book_id is None:
                    cursor.execute(
                        "DELETE FROM entities WHERE category = ? AND untranslated = ? AND book_id IS NULL",
                        (category, untranslated))
                else:
                    cursor.execute(
                        "DELETE FROM entities WHERE category = ? AND untranslated = ? AND book_id = ?",
                        (category, untranslated, book_id))
                    if cursor.rowcount == 0:
                        cursor.execute(
                            "DELETE FROM entities WHERE category = ? AND untranslated = ? AND book_id IS NULL",
                            (category, untranslated))

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

    def change_entity_category(self, old_category, untranslated, new_category,
                               book_id=_UNSCOPED):
        """
        Move an entity from one category to another.

        book_id scoping matches delete_entity: omitted = all books (legacy),
        None = global row only, int = that book's row with global fallback.
        Returns True if the entity was moved, False otherwise.
        """
        # (scope_sql, scope_params) suffix applied to every query below.
        if book_id is _UNSCOPED:
            scopes = [("", ())]
        elif book_id is None:
            scopes = [(" AND book_id IS NULL", ())]
        else:
            scopes = [(" AND book_id = ?", (book_id,)), (" AND book_id IS NULL", ())]

        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                for scope_sql, scope_params in scopes:
                    # Check if entity exists in the source category
                    cursor.execute(
                        "SELECT translation, last_chapter, incorrect_translation, gender "
                        "FROM entities WHERE category = ? AND untranslated = ?" + scope_sql,
                        (old_category, untranslated) + scope_params)
                    entity_data = cursor.fetchone()
                    if entity_data:
                        break
                else:
                    self.logger.warning(f"Entity '{untranslated}' not found in category '{old_category}'")
                    return False

                # Check if entity already exists in the target category
                cursor.execute(
                    "SELECT id FROM entities WHERE category = ? AND untranslated = ?" + scope_sql,
                    (new_category, untranslated) + scope_params)
                if cursor.fetchone():
                    self.logger.warning(f"Entity '{untranslated}' already exists in target category '{new_category}'")
                    return False

                # Update the category
                cursor.execute(
                    "UPDATE entities SET category = ? WHERE category = ? AND untranslated = ?" + scope_sql,
                    (new_category, old_category, untranslated) + scope_params)
            
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

    def get_entity_by_translation(self, translation, book_id=_UNSCOPED):
        """
        Find an entity by its translation.
        Returns a tuple (category, untranslated, entity_data) if found, None otherwise.

        This is useful for finding duplicates by translation rather than by untranslated text.
        Pass book_id (int) to only consider that book's entities plus globals —
        an unscoped lookup reports "duplicates" from unrelated books.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                if book_id is _UNSCOPED:
                    cursor.execute('''
            SELECT category, untranslated, last_chapter, incorrect_translation, gender
            FROM entities
            WHERE translation = ?
            ''', (translation,))
                elif book_id is None:
                    cursor.execute(
                        "SELECT category, untranslated, last_chapter, incorrect_translation, gender "
                        "FROM entities WHERE translation = ? AND book_id IS NULL",
                        (translation,))
                else:
                    # Book rows first so a book-scoped entity shadows a global one.
                    cursor.execute(
                        "SELECT category, untranslated, last_chapter, incorrect_translation, gender "
                        "FROM entities WHERE translation = ? AND (book_id = ? OR book_id IS NULL) "
                        "ORDER BY book_id IS NULL",
                        (translation, book_id))

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
                
                # Import each entity. These legacy JSON imports are global
                # (book_id NULL) entities; NULL never hits a UNIQUE conflict on
                # either backend, so an INSERT … ON CONFLICT upsert silently
                # duplicated the whole set on every re-import. Explicit
                # update-else-insert instead.
                count = 0
                for category, entities in json_data.items():
                    for untranslated, entity_data in entities.items():
                        translation = entity_data.get('translation', '')
                        last_chapter = entity_data.get('last_chapter', '')
                        incorrect_translation = entity_data.get('incorrect_translation', None)
                        gender = entity_data.get('gender', None)

                        cursor.execute(
                            "UPDATE entities SET category = ?, translation = ?, "
                            "last_chapter = ?, incorrect_translation = ?, gender = ? "
                            "WHERE book_id IS NULL AND untranslated = ?",
                            (category, translation, last_chapter,
                             incorrect_translation, gender, untranslated))
                        if cursor.rowcount == 0:
                            cursor.execute(
                                "INSERT INTO entities (category, untranslated, translation, "
                                "last_chapter, incorrect_translation, gender) "
                                "VALUES (?, ?, ?, ?, ?, ?)",
                                (category, untranslated, translation, last_chapter,
                                 incorrect_translation, gender))
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

    # ------------------------------------------------------------------
    # ID-based accessors (added in B4 for the web layer; additive only —
    # root scripts keep using the category/untranslated-keyed methods above)
    # ------------------------------------------------------------------

    _ENTITY_COLUMNS = ("id", "category", "untranslated", "translation",
                       "last_chapter", "gender", "incorrect_translation",
                       "book_id", "origin_chapter", "note")

    def get_entity_by_id(self, entity_id):
        """Return a single entity row as a dict, or None if it doesn't exist."""
        with self._conn(dict_rows=True) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT " + ", ".join(self._ENTITY_COLUMNS) +
                " FROM entities WHERE id = ?",
                (entity_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def update_entity_by_id(self, entity_id, note_author='human', note_chapter=None,
                            note_reason=None, gender_author='human', gender_chapter=None,
                            gender_reason=None, **fields):
        """Update arbitrary entity columns by primary key.

        A `note` field is diverted to set_entity_note and a `gender` field to
        set_entity_gender (see update_entity), so each change lands in that
        column's revision history; note_* / gender_* annotate those revisions.

        Only known columns are accepted; unknown keyword names raise
        ValueError (catching typos rather than silently dropping them).
        Explicit None values are written as SQL NULL (e.g. book_id=None
        moves an entity to global scope).

        Returns True when a row was updated. Note the backend nuance
        inherited from the previous inline SQL: MySQL reports rowcount 0
        for an UPDATE that leaves values unchanged, while SQLite counts
        matched rows.
        """
        unknown = set(fields) - set(self._ENTITY_COLUMNS) | ({"id"} & set(fields))
        if unknown:
            raise ValueError(f"Unknown entity column(s): {sorted(unknown)}")
        wrote_history = False
        note_write = fields.pop('note', _UNSET)
        if note_write is not _UNSET:
            self.set_entity_note(entity_id, note_write, author=note_author,
                                 chapter_number=note_chapter, reason=note_reason)
            wrote_history = True
        gender_write = fields.pop('gender', _UNSET)
        if gender_write is not _UNSET:
            self.set_entity_gender(entity_id, gender_write, author=gender_author,
                                   chapter_number=gender_chapter, reason=gender_reason)
            wrote_history = True
        if not fields:
            return wrote_history
        with self._conn() as conn:
            cursor = conn.cursor()
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            cursor.execute(
                f"UPDATE entities SET {set_clause} WHERE id = ?",
                list(fields.values()) + [entity_id],
            )
            return cursor.rowcount > 0

    def substitute_in_entity_notes(self, book_id, old_translation, new_translation,
                                   chapter_numbers=None, word_boundary=False,
                                   dry_run=False, cursor=None):
        """Apply an entity-translation substitution to entity *notes* as well.

        A note is written at extraction time, so it freezes the terminology that
        was current then. Renaming an entity therefore leaves the old English
        stranded inside the notes of the entities extracted alongside it — and
        those notes are fed back into later translations, re-seeding the very
        term that was just corrected. Every substitution path (both entity
        modals via /entities/propagate, correct_entity_translation.py and
        bulk_correct_entities.py) runs this alongside its chapter sweep.

        ``chapter_numbers`` scopes the rewrite to entities whose ``origin_chapter``
        is one of the chapters the chapter sweep ran over. ``None`` means the
        sweep was book-wide, so every note in the book is eligible — including
        entities with no origin_chapter, which no chapter set can ever match.

        Only book-scoped entities are touched; global entities (book_id IS NULL)
        are shared across books and are left alone.

        Pass ``cursor`` (from a ``dict_rows`` connection) to join the caller's
        transaction, so the note writes commit — or roll back — with the chapter
        writes. ``dry_run`` counts without writing, for the CLIs' --dry-run.

        Returns the number of notes changed.
        """
        from chapter_text_ops import substitute_in_lines

        if not old_translation or old_translation == new_translation:
            return 0

        scope = None
        if chapter_numbers is not None:
            scope = set()
            for n in chapter_numbers:
                try:
                    scope.add(int(n))
                except (TypeError, ValueError):
                    continue

        def _in_scope(origin_chapter):
            if scope is None:
                return True
            try:
                return int(origin_chapter) in scope
            except (TypeError, ValueError):
                # NULL / unparseable origin_chapter can't match a chapter set.
                return False

        def _run(cur):
            cur.execute(
                "SELECT id, note, origin_chapter FROM entities "
                "WHERE book_id = ? AND note IS NOT NULL AND note != ''",
                (book_id,),
            )
            rows = cur.fetchall()

            changed = 0
            for row in rows:
                if not _in_scope(row["origin_chapter"]):
                    continue
                new_note, n = substitute_in_lines(
                    [row["note"]], old_translation, new_translation, word_boundary
                )
                if not n:
                    continue
                changed += 1
                if not dry_run:
                    # Through the note choke point so a terminology sweep is
                    # revertible from the same audit trail as everything else.
                    self.set_entity_note(
                        row["id"], new_note[0], author='script',
                        reason=f"Substitution: {old_translation} -> {new_translation}",
                        cursor=cur,
                    )
            return changed

        if cursor is not None:
            return _run(cursor)
        with self._conn(dict_rows=True) as conn:
            return _run(conn.cursor())

    # ------------------------------------------------------------------
    # Entity notes: single write choke point + revision history
    # ------------------------------------------------------------------

    def get_entity_id(self, book_id, untranslated, category=None):
        """Primary key of a book's entity by its untranslated text, or None.

        Book-scoped rows win over global (book_id IS NULL) ones, matching how
        the glossary resolves an entity during translation.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                params = [untranslated, book_id]
                cat_sql = ""
                if category:
                    cat_sql = " AND category = ?"
                    params.append(category)
                cursor.execute(
                    "SELECT id FROM entities WHERE untranslated = ? "
                    "AND (book_id = ? OR book_id IS NULL)" + cat_sql +
                    " ORDER BY CASE WHEN book_id IS NULL THEN 1 ELSE 0 END, id LIMIT 1",
                    params,
                )
                row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            self.logger.error(f"Error looking up entity id for '{untranslated}': {e}")
            return None

    def set_entity_note(self, entity_id, new_note, *, author='model',
                        chapter_number=None, reason=None, shrink=False,
                        cursor=None):
        """Write an entity's note, snapshotting the note it replaces.

        The only sanctioned way to change a note. Nothing about a note is
        locked — the translation model may revise its own or a human's — so the
        safety net is that every change is recoverable: the prior value lands in
        entity_note_revisions and revert_note_revision puts it back.

        author is 'model' (the note_updates channel), 'human' (review panel or
        the entities page) or 'script' (bulk sweeps).

        Returns the revision id, or None when the note was already identical
        (a no-op writes nothing and records nothing).
        """
        try:
            # Filled in by _run so the cache mirror below can see them — they are
            # local to the closure otherwise.
            touched = {}

            def _run(cur):
                cur.execute(
                    "SELECT note, book_id, category, untranslated FROM entities WHERE id = ?",
                    (entity_id,))
                row = cur.fetchone()
                if row is None:
                    self.logger.warning(f"set_entity_note: no entity with id {entity_id}")
                    return None
                # Callers may hand us a dict-row cursor (substitute_in_entity_notes
                # opens one), where positional access raises KeyError: 0.
                if isinstance(row, dict):
                    previous_note, book_id, category, untranslated = (
                        row['note'], row['book_id'], row['category'], row['untranslated'])
                else:
                    previous_note, book_id, category, untranslated = row[0], row[1], row[2], row[3]
                touched.update(category=category, untranslated=untranslated)
                if (previous_note or '') == (new_note or ''):
                    return None
                cur.execute("UPDATE entities SET note = ? WHERE id = ?", (new_note, entity_id))
                cur.execute("""
                INSERT INTO entity_note_revisions
                (entity_id, book_id, previous_note, new_note, author, chapter_number,
                 reason, shrink, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (entity_id, book_id, previous_note, new_note, author, chapter_number,
                      reason, 1 if shrink else 0,
                      datetime.datetime.now().isoformat()))
                return cur.lastrowid

            if cursor is not None:
                revision_id = _run(cursor)
            else:
                with self._conn() as conn:
                    revision_id = _run(conn.cursor())

            if revision_id is not None:
                # Mirror into the shared cache (keyed by category/untranslated —
                # cache entries carry no id) so the admin pages don't show a
                # stale note until the next reload.
                cached = getattr(self, 'entities', {}).get(touched.get('category'), {})
                entry = cached.get(touched.get('untranslated'))
                if isinstance(entry, dict):
                    if new_note:
                        entry['note'] = new_note
                    else:
                        entry.pop('note', None)
            return revision_id
        except Exception as e:
            self.logger.error(f"Error setting entity note for {entity_id}: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return None

    def list_note_revisions(self, book_id=None, entity_id=None, limit=50):
        """Note-change history, newest first, joined to the entity it belongs to.

        Scope by entity_id (one entity's timeline) or book_id (the book's recent
        changes feed). Passing neither returns the newest changes across books.
        """
        try:
            where, params = [], []
            if entity_id is not None:
                where.append("r.entity_id = ?")
                params.append(entity_id)
            if book_id is not None:
                where.append("r.book_id = ?")
                params.append(book_id)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            with self._conn(dict_rows=True) as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                SELECT r.id, r.entity_id, r.book_id, r.previous_note, r.new_note,
                       r.author, r.chapter_number, r.reason, r.shrink, r.created_at,
                       e.untranslated, e.translation, e.category, e.note AS current_note
                FROM entity_note_revisions r
                LEFT JOIN entities e ON e.id = r.entity_id
                {clause}
                ORDER BY r.id DESC LIMIT ?
                """, params + [int(limit)])
                rows = cursor.fetchall()
            out = []
            for row in rows:
                rev = dict(row)
                rev["shrink"] = bool(rev.get("shrink"))
                # True while this revision is still the note's current value —
                # what the audit panel offers a Revert button for.
                rev["is_current"] = (rev.get("current_note") or '') == (rev.get("new_note") or '')
                out.append(rev)
            return out
        except Exception as e:
            self.logger.error(f"Error listing note revisions: {e}")
            return []

    def revert_note_revision(self, revision_id):
        """Restore the note this revision replaced.

        The revert itself is recorded as a further revision (author 'human'), so
        the trail stays complete and a revert can itself be undone.
        Returns the new revision id, or None if there was nothing to do.
        """
        try:
            with self._conn(dict_rows=True) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT entity_id, previous_note FROM entity_note_revisions WHERE id = ?",
                    (revision_id,))
                row = cursor.fetchone()
            if row is None:
                return None
            row = dict(row)
            return self.set_entity_note(
                row["entity_id"], row["previous_note"], author='human',
                reason=f"Reverted revision {revision_id}")
        except Exception as e:
            self.logger.error(f"Error reverting note revision {revision_id}: {e}")
            if self.strict_writes:
                raise
            return None

    # The genders an entity may carry. A blank/None gender means "not recorded",
    # which is a legitimate state (an unnamed voice, a category that doesn't
    # track gender) and not the same as neutral.
    VALID_GENDERS = ('male', 'female', 'neutral')

    def set_entity_gender(self, entity_id, new_gender, *, author='model',
                          chapter_number=None, reason=None, cursor=None):
        """Write an entity's gender, snapshotting the value it replaces.

        The sanctioned way to CHANGE a gender that is already recorded. Gender
        rides into every prompt that mentions the entity and decides its
        pronouns, so a wrong value quietly corrupts every later chapter — and
        unlike a mistranslation nobody sees it in the glossary. Each change
        therefore lands in entity_gender_revisions and revert_gender_revision
        puts it back.

        Unlike notes this is deliberately NOT point-in-time. There is no
        genders_as_of: the current gender is the only truth, and a character who
        genuinely changes gender in the story is handled by correcting the
        record and saying so in the entity's note — which is versioned per
        chapter and rewinds on retranslation.

        author is 'model' (the note_updates channel), 'human' (review panel or
        the entities page) or 'script' (bulk sweeps).

        Returns the revision id, or None when nothing was written (unchanged
        value, unknown entity, or a gender outside VALID_GENDERS).
        """
        normalized = (new_gender or '').strip().lower() or None
        if normalized is not None and normalized not in self.VALID_GENDERS:
            self.logger.warning(
                f"set_entity_gender: '{new_gender}' is not one of "
                f"{self.VALID_GENDERS} — ignored")
            return None
        try:
            touched = {}

            def _run(cur):
                cur.execute(
                    "SELECT gender, book_id, category, untranslated FROM entities WHERE id = ?",
                    (entity_id,))
                row = cur.fetchone()
                if row is None:
                    self.logger.warning(f"set_entity_gender: no entity with id {entity_id}")
                    return None
                if isinstance(row, dict):
                    previous, book_id, category, untranslated = (
                        row['gender'], row['book_id'], row['category'], row['untranslated'])
                else:
                    previous, book_id, category, untranslated = row[0], row[1], row[2], row[3]
                touched.update(category=category, untranslated=untranslated)
                if ((previous or '').strip().lower() or None) == normalized:
                    return None
                cur.execute("UPDATE entities SET gender = ? WHERE id = ?", (normalized, entity_id))
                cur.execute("""
                INSERT INTO entity_gender_revisions
                (entity_id, book_id, previous_gender, new_gender, author, chapter_number,
                 reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (entity_id, book_id, previous, normalized, author, chapter_number,
                      reason, datetime.datetime.now().isoformat()))
                return cur.lastrowid

            if cursor is not None:
                revision_id = _run(cursor)
            else:
                with self._conn() as conn:
                    revision_id = _run(conn.cursor())

            if revision_id is not None:
                # Mirror into the shared cache (keyed by category/untranslated) so
                # the admin pages don't show a stale gender until the next reload.
                cached = getattr(self, 'entities', {}).get(touched.get('category'), {})
                entry = cached.get(touched.get('untranslated'))
                if isinstance(entry, dict):
                    if normalized:
                        entry['gender'] = normalized
                    else:
                        entry.pop('gender', None)
            return revision_id
        except Exception as e:
            self.logger.error(f"Error setting entity gender for {entity_id}: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return None

    def list_gender_revisions(self, book_id=None, entity_id=None, limit=50):
        """Gender-change history, newest first, joined to the entity it belongs to.

        Same scoping rules as list_note_revisions: entity_id for one entity's
        timeline, book_id for the book's feed, neither for everything.
        """
        try:
            where, params = [], []
            if entity_id is not None:
                where.append("r.entity_id = ?")
                params.append(entity_id)
            if book_id is not None:
                where.append("r.book_id = ?")
                params.append(book_id)
            clause = ("WHERE " + " AND ".join(where)) if where else ""
            with self._conn(dict_rows=True) as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                SELECT r.id, r.entity_id, r.book_id, r.previous_gender, r.new_gender,
                       r.author, r.chapter_number, r.reason, r.created_at,
                       e.untranslated, e.translation, e.category, e.gender AS current_gender
                FROM entity_gender_revisions r
                LEFT JOIN entities e ON e.id = r.entity_id
                {clause}
                ORDER BY r.id DESC LIMIT ?
                """, params + [int(limit)])
                rows = cursor.fetchall()
            out = []
            for row in rows:
                rev = dict(row)
                # True while this revision is still the gender's current value —
                # what the audit panel offers a Revert button for.
                rev["is_current"] = ((rev.get("current_gender") or '') ==
                                     (rev.get("new_gender") or ''))
                out.append(rev)
            return out
        except Exception as e:
            self.logger.error(f"Error listing gender revisions: {e}")
            return []

    def revert_gender_revision(self, revision_id):
        """Restore the gender this revision replaced.

        The revert is itself recorded (author 'human'), so the trail stays
        complete and a revert can be undone. Returns the new revision id, or
        None if there was nothing to do — including the case where the value it
        would restore is blank, which is a legitimate "never recorded" state.
        """
        try:
            with self._conn(dict_rows=True) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT entity_id, previous_gender FROM entity_gender_revisions WHERE id = ?",
                    (revision_id,))
                row = cursor.fetchone()
            if row is None:
                return None
            row = dict(row)
            return self.set_entity_gender(
                row["entity_id"], row["previous_gender"], author='human',
                reason=f"Reverted revision {revision_id}")
        except Exception as e:
            self.logger.error(f"Error reverting gender revision {revision_id}: {e}")
            if self.strict_writes:
                raise
            return None

    def notes_as_of(self, book_id, chapter, entity_ids=None, key_by='id'):
        """Every entity's note as it stood at the END of `chapter`.

        Returns {entity_id: note_or_None}, or {untranslated: note_or_None} with
        key_by='untranslated' (what the prompt builder wants — the glossary it
        assembles is keyed by source text, not row id). None means the entity
        carried no note at that point — either it had not been written yet, or
        the entity itself did not exist.

        A note written *during* chapter C counts as in force at C: asking for
        chapter 20 gives the glossary as it read once chapter 20 was done, which
        is what a "notes for chapters 1-20" view wants. (The prompt for chapter C
        itself was of course built from the state at C-1.)

        How the rewind works: revisions are chronological by id and each row
        carries both sides of the change, so the note in force at chapter C is
        `previous_note` of the earliest revision made *after* C. Revisions with
        no chapter (human edits, script sweeps) are treated as belonging to the
        present and are not rewound — a correction you made by hand stays applied
        to the historical view, which is nearly always what you want when the
        point is to read the book's terminology as of chapter C.

        Where history is missing the answer is the closest note in time rather
        than a guess: notes written before the revision history existed have no
        creation row, so the earliest thing known about them is the first
        revision's `previous_note`, and an entity with no revisions at all
        reports its current note. The one hard rule that still applies is the
        `origin_chapter` floor — an entity that did not exist yet had no note.
        """
        try:
            params = [book_id]
            id_sql = ""
            if entity_ids:
                id_sql = " AND id IN (" + ",".join("?" * len(entity_ids)) + ")"
                params.extend(entity_ids)
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, note, origin_chapter, untranslated FROM entities "
                    "WHERE (book_id = ? OR book_id IS NULL)" + id_sql,
                    params)
                current = {row[0]: (row[1], row[2], row[3]) for row in cursor.fetchall()}

                # Narrow the revision scan to the entities actually asked
                # about when that is a short list (the reader's per-chapter
                # glossary asks about ~50 of them). Rows for other entities are
                # discarded by the `entity_id not in current` guard below
                # anyway, so this changes cost, not semantics. Long id lists
                # keep the book-wide scan rather than build a huge IN clause.
                rev_sql = ("SELECT entity_id, chapter_number, previous_note "
                           "FROM entity_note_revisions WHERE book_id = ?")
                rev_params = [book_id]
                if entity_ids and len(entity_ids) <= 500:
                    rev_sql += " AND entity_id IN (" + ",".join("?" * len(entity_ids)) + ")"
                    rev_params.extend(entity_ids)
                cursor.execute(rev_sql + " ORDER BY id", rev_params)
                revisions = cursor.fetchall()

            rewound = {}
            for entity_id, chapter_number, previous_note in revisions:
                if entity_id in rewound or entity_id not in current:
                    continue  # earliest post-chapter revision wins
                if chapter_number is not None and chapter_number > chapter:
                    rewound[entity_id] = previous_note

            out = {}
            for entity_id, (note, origin_chapter, untranslated) in current.items():
                value = rewound.get(entity_id, note)
                if value and origin_chapter is not None and origin_chapter > chapter:
                    # The entity itself post-dates the requested point.
                    value = None
                out[untranslated if key_by == 'untranslated' else entity_id] = value or None
            return out
        except Exception as e:
            self.logger.error(f"Error building notes as of chapter {chapter}: {e}")
            return {}

    def has_note_revisions_after(self, book_id, chapter):
        """True when this book's notes were revised in a LATER chapter.

        Which is to say: translating `chapter` now is a retranslation running
        behind the glossary's own timeline, so the notes in force at the time
        are not the notes on the rows today.
        """
        if not book_id or not chapter:
            return False
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM entity_note_revisions "
                    "WHERE book_id = ? AND chapter_number > ? LIMIT 1",
                    (book_id, chapter))
                return cursor.fetchone() is not None
        except Exception as e:
            self.logger.error(f"Error checking note revisions after chapter {chapter}: {e}")
            return False

    def delete_entity_by_id(self, entity_id):
        """Delete an entity by primary key. Returns True if a row was deleted."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
            return cursor.rowcount > 0

    def list_gendered_entities(self, book_id, categories):
        """Entities in the given categories with a usable gender and a
        non-empty translation — the pronoun-repair candidate set.

        Returns a list of dicts: {id, untranslated, translation, gender}.
        """
        cats = list(categories) or ["characters"]
        placeholders = ",".join("?" for _ in cats)
        with self._conn(dict_rows=True) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, untranslated, translation, gender
                FROM entities
                WHERE book_id = ? AND category IN ({placeholders})
                      AND gender IN ('male', 'female', 'neutral')
                      AND translation IS NOT NULL AND translation != ''
                """,
                (book_id, *cats),
            )
            return [dict(r) for r in cursor.fetchall()]

    def count_entities_by_category(self, book_id):
        """Entity counts per category for a book (book-scoped + global rows).

        Returns {category: count}.
        """
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT category, COUNT(*) FROM entities "
                "WHERE book_id = ? OR book_id IS NULL GROUP BY category",
                (book_id,),
            )
            return {row[0]: row[1] for row in cursor.fetchall()}
