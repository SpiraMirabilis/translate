import json
import os
import datetime
import traceback
from modules import fire_book_module_events, resolve_module_ids
from db.core import normalize_categories, DEFAULT_CATEGORIES


class BooksRepo:
    """Book CRUD, categories/tags, prompt templates, module settings, EPUB cache, token ratios."""

    # Book management section 
    def create_book(self, title, author=None, language='en', description=None, source_language='zh', target_language='en', is_original=False):
        """
        Create a new book in the database.

        Args:
            title: Book title
            author: Book author (optional)
            language: Target language code (default: en)
            description: Book description (optional)
            source_language: Source language code (default: zh)
            target_language: Target language code (default: en)
            is_original: True for original works written in the web editor
                (no source text / translation pipeline)

        Returns:
            int: Book ID if successful, None otherwise
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                # Check if book already exists
                cursor.execute("SELECT id FROM books WHERE title = ?", (title,))
                existing = cursor.fetchone()

                if existing:
                    self.logger.info(f"Book '{title}' already exists with ID {existing[0]}")
                    return existing[0]

                # Current timestamp
                timestamp = datetime.datetime.now().isoformat()

                cursor.execute('''
            INSERT INTO books
            (title, author, language, description, created_date, modified_date, source_language, target_language, is_original)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (title, author, language, description, timestamp, timestamp, source_language, target_language, int(bool(is_original))))
                
                book_id = cursor.lastrowid
            
            self.logger.info(f"Created new book: '{title}' with ID {book_id}")
            return book_id
            
        except Exception as e:
            self.logger.error(f"Error creating book: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return None

    def get_book_prompt_template(self, book_id):
        """
        Get the prompt template for a specific book.
        Returns None if no custom template is set.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
            SELECT prompt_template FROM books
            WHERE id = ?
            ''', (book_id,))
                
                result = cursor.fetchone()
            
            if result and result[0]:
                return result[0]
            return None
        except Exception as e:
            self.logger.error(f"Error retrieving book prompt template: {e}")
            return None

    def set_book_prompt_template(self, book_id, prompt_template):
        """
        Set the prompt template for a specific book.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
            UPDATE books
            SET prompt_template = ?
            WHERE id = ?
            ''', (prompt_template, book_id))
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting book prompt template: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def _get_raw_book_categories(self, book_id):
        """Return the raw stored categories value (parsed JSON) or None if unset."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT categories FROM books WHERE id = ?", (book_id,))
            row = cursor.fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return None

    def get_book_categories_meta(self, book_id):
        """Get entity categories with per-category attributes for a book.

        Returns a list of {"name": str, "attributes": [str, ...]} dicts,
        falling back to DEFAULT_CATEGORIES (characters gendered) when unset.
        """
        try:
            return normalize_categories(self._get_raw_book_categories(book_id))
        except Exception as e:
            self.logger.error(f"Error getting book category metadata: {e}")
            return normalize_categories(None)

    def get_book_categories(self, book_id):
        """Get entity category names for a book. Returns DEFAULT_CATEGORIES if none set.

        Backward-compatible: returns a plain list of names regardless of whether the
        book stores legacy string lists or the newer attribute-carrying objects.
        """
        return [c['name'] for c in self.get_book_categories_meta(book_id)]

    def get_book_gendered_categories(self, book_id):
        """Return the names of categories that carry the 'gender' attribute for a book."""
        return [c['name'] for c in self.get_book_categories_meta(book_id)
                if 'gender' in c['attributes']]

    def is_gendered_category(self, book_id, category):
        """True if `category` is gender-tracked for this book."""
        if not category:
            return False
        return category in self.get_book_gendered_categories(book_id)

    def set_book_categories(self, book_id, categories):
        """Set entity categories for a book. Pass None to reset to defaults.

        Accepts either a list of names or a list of {"name", "attributes"} dicts;
        the value is normalized to the attribute-carrying form before storage.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                if categories is None:
                    value = None
                else:
                    value = json.dumps(normalize_categories(categories))
                cursor.execute("UPDATE books SET categories = ? WHERE id = ?", (value, book_id))
            return True
        except Exception as e:
            self.logger.error(f"Error setting book categories: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def get_book_tags(self, book_id):
        """Get the tag list for a book. Returns [] if unset."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT tags FROM books WHERE id = ?", (book_id,))
                row = cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return []
        except Exception as e:
            self.logger.error(f"Error getting book tags: {e}")
            return []

    def set_book_tags(self, book_id, tags):
        """Set tags for a book. Pass None or [] to clear."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                value = json.dumps(tags) if tags else None
                cursor.execute("UPDATE books SET tags = ? WHERE id = ?", (value, book_id))
            return True
        except Exception as e:
            self.logger.error(f"Error setting book tags: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def get_all_tags(self):
        """Return the deduped, sorted union of tags across all books."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT tags FROM books WHERE tags IS NOT NULL")
                seen = set()
                for row in cursor.fetchall():
                    raw = row[0]
                    if not raw:
                        continue
                    try:
                        for tag in json.loads(raw):
                            if isinstance(tag, str) and tag.strip():
                                seen.add(tag)
                    except (ValueError, TypeError):
                        continue
            return sorted(seen)
        except Exception as e:
            self.logger.error(f"Error listing all tags: {e}")
            return []

    def get_book(self, book_id=None, title=None):
        """
        Get book information from the database.
        
        Args:
            book_id: Book ID (optional if title is provided)
            title: Book title (optional if book_id is provided)
            
        Returns:
            dict: Book information dictionary or None if not found
        """
        if not book_id and not title:
            self.logger.error("Either book_id or title must be provided")
            return None
            
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                if book_id:
                    cursor.execute('''
                SELECT id, title, author, language, description, created_date, modified_date,
                    source_language, target_language, cover_image, categories, is_public,
                    total_source_chapters, status, comments_enabled, source_url, notes,
                    view_count, trad_to_simp, tags, modules, is_original
                FROM books
                WHERE id = ?
                ''', (book_id,))
                else:
                    cursor.execute('''
                SELECT id, title, author, language, description, created_date, modified_date,
                    source_language, target_language, cover_image, categories, is_public,
                    total_source_chapters, status, comments_enabled, source_url, notes,
                    view_count, trad_to_simp, tags, modules, is_original
                FROM books
                WHERE title = ?
                ''', (title,))

                row = cursor.fetchone()

            if not row:
                return None

            raw_cats = row[10] if len(row) > 10 else None
            raw_tags = row[19] if len(row) > 19 else None
            raw_modules = row[20] if len(row) > 20 else None
            try:
                modules = json.loads(raw_modules) if raw_modules else None
            except (json.JSONDecodeError, TypeError):
                modules = None
            book_info = {
                "id": row[0],
                "title": row[1],
                "author": row[2],
                "language": row[3],
                "description": row[4],
                "created_date": row[5],
                "modified_date": row[6],
                "source_language": row[7],
                "target_language": row[8],
                "cover_image": row[9] if len(row) > 9 else None,
                "categories": json.loads(raw_cats) if raw_cats else None,
                "is_public": bool(row[11]) if len(row) > 11 else True,
                "total_source_chapters": row[12] if len(row) > 12 else None,
                "status": row[13] if len(row) > 13 else "ongoing",
                "comments_enabled": bool(row[14]) if len(row) > 14 and row[14] is not None else True,
                "source_url": row[15] if len(row) > 15 else None,
                "notes": row[16] if len(row) > 16 else None,
                "view_count": row[17] if len(row) > 17 and row[17] is not None else 0,
                "trad_to_simp": row[18] if len(row) > 18 else None,
                "tags": json.loads(raw_tags) if raw_tags else [],
                "modules": modules,
                "is_original": bool(row[21]) if len(row) > 21 and row[21] is not None else False,
            }

            return book_info
            
        except Exception as e:
            self.logger.error(f"Error getting book information: {e}")
            return None

    def update_book(self, book_id, **kwargs):
        """
        Update book information.
        
        Args:
            book_id: Book ID to update
            **kwargs: Fields to update (title, author, language, description, etc.)
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # If this update can change which modules are enabled, snapshot the
            # enabled set beforehand so we can fire add/remove events on the diff.
            module_trigger = ('modules' in kwargs) or ('source_url' in kwargs)
            before_ids = resolve_module_ids(self.get_book(book_id=book_id)) if module_trigger else set()
            with self._conn() as conn:
                cursor = conn.cursor()

                # Check if book exists
                cursor.execute("SELECT 1 FROM books WHERE id = ?", (book_id,))
                if not cursor.fetchone():
                    self.logger.warning(f"Book with ID {book_id} not found")
                    return False

                # Build the SET clause dynamically based on provided kwargs
                set_clause = []
                values = []

                # Update modified_date automatically
                kwargs["modified_date"] = datetime.datetime.now().isoformat()

                for key, value in kwargs.items():
                    if key in ['title', 'author', 'language', 'description', 'source_language',
                            'target_language', 'modified_date', 'cover_image', 'is_public',
                            'total_source_chapters', 'status', 'comments_enabled',
                            'source_url', 'notes', 'trad_to_simp', 'tags', 'modules',
                            'is_original']:
                        set_clause.append(f"{key} = ?")
                        if key in ('is_public', 'comments_enabled', 'is_original'):
                            values.append(int(bool(value)))
                        elif key == 'total_source_chapters':
                            values.append(int(value) if value is not None else None)
                        elif key == 'trad_to_simp':
                            if value is None:
                                values.append(None)
                            else:
                                values.append(int(bool(value)))
                        elif key in ('tags', 'modules'):
                            values.append(json.dumps(value) if value else None)
                        else:
                            values.append(value)

                if not set_clause:
                    self.logger.warning("No valid fields to update")
                    return False

                # Complete the parameter list with book_id
                values.append(book_id)

                # Execute the update
                cursor.execute(f'''
            UPDATE books
            SET {', '.join(set_clause)}
            WHERE id = ?
            ''', values)

            # Invalidate cached EPUB if metadata that affects it changed
            epub_fields = {'title', 'author', 'language', 'description', 'cover_image'}
            if epub_fields & set(kwargs):
                self.invalidate_epub_cache(book_id)

            # Fire module add/remove lifecycle events if the enabled set changed.
            if module_trigger:
                book_after = self.get_book(book_id=book_id)
                after_ids = resolve_module_ids(book_after)
                if before_ids != after_ids:
                    fire_book_module_events(self, book_after, before_ids, after_ids,
                                            self.config, self.logger)

            self.logger.info(f"Updated book with ID {book_id}")
            return True

        except Exception as e:
            self.logger.error(f"Error updating book: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def get_module_settings(self, book_id, module_id=None):
        """Return stored per-book module settings.

        With ``module_id``: returns ``{setting_key: value}`` for that one module.
        Without it: returns ``{module_id: {setting_key: value}}`` for the whole
        book (used to attach all module settings to the run ``ctx`` in one query).
        Values are JSON-decoded; malformed rows are skipped. Only *stored* values
        are returned — callers merge schema defaults via
        ``TranslationModule.resolve_settings``.
        """
        result = {}
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                if module_id is not None:
                    cursor.execute(
                        "SELECT setting_key, value_json FROM book_module_settings "
                        "WHERE book_id = ? AND module_id = ?",
                        (book_id, module_id))
                    for skey, raw in cursor.fetchall():
                        try:
                            result[skey] = json.loads(raw) if raw is not None else None
                        except (json.JSONDecodeError, TypeError):
                            continue
                else:
                    cursor.execute(
                        "SELECT module_id, setting_key, value_json FROM book_module_settings "
                        "WHERE book_id = ?",
                        (book_id,))
                    for mid, skey, raw in cursor.fetchall():
                        try:
                            val = json.loads(raw) if raw is not None else None
                        except (json.JSONDecodeError, TypeError):
                            continue
                        result.setdefault(mid, {})[skey] = val
        except Exception as e:
            self.logger.error(f"Error loading module settings for book {book_id}: {e}")
        return result

    def set_module_settings(self, book_id, module_id, settings):
        """Authoritatively replace the stored settings for one book+module.

        ``settings`` is a ``{setting_key: value}`` dict; values are JSON-encoded.
        All prior rows for this (book, module) are deleted first, so the passed
        dict is the complete new state (omitted keys are cleared). Dual-backend
        safe (plain ``?`` placeholders via ``self.backend``).
        """
        if settings is None:
            settings = {}
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM book_module_settings WHERE book_id = ? AND module_id = ?",
                    (book_id, module_id))
                for skey, value in settings.items():
                    cursor.execute(
                        "INSERT INTO book_module_settings "
                        "(book_id, module_id, setting_key, value_json) VALUES (?, ?, ?, ?)",
                        (book_id, module_id, skey, json.dumps(value)))
            return True
        except Exception as e:
            self.logger.error(
                f"Error saving module settings for book {book_id}/{module_id}: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def list_books(self, order_by: str = 'title'):
        """
        List all books in the database.

        Args:
            order_by: One of 'title' (default), 'popular', 'updated', or
                'newly_added'. Unknown values fall back to 'title'.

        Returns:
            list: List of book information dictionaries
        """
        if self.backend.name == 'sqlite':
            title_clause = 'title COLLATE NOCASE ASC'
        else:
            title_clause = 'title ASC'
        ORDER_CLAUSES = {
            'title':   title_clause,
            'popular': 'view_count DESC, ' + title_clause,
            'updated': "COALESCE(modified_date, created_date, '') DESC, " + title_clause,
            'newly_added': 'id DESC',
        }
        clause = ORDER_CLAUSES.get(order_by, ORDER_CLAUSES['title'])
        try:
            # Publicly-visible chapters only (drafts/scheduled excluded) — the
            # public Library uses these instead of the raw admin counts.
            now = datetime.datetime.now().isoformat()
            pub = "published_at IS NOT NULL AND published_at <= ?"
            with self._conn() as conn:
                cursor = conn.cursor()

                cursor.execute(f'''
            SELECT id, title, author, language, created_date, cover_image, categories,
                (SELECT COUNT(*) FROM chapters WHERE book_id = books.id) as chapter_count,
                description, is_public, total_source_chapters, status, comments_enabled,
                source_url, notes, view_count, modified_date, trad_to_simp, tags, modules,
                (SELECT MAX(translation_date) FROM chapters WHERE book_id = books.id) as last_chapter_date,
                is_original,
                (SELECT COUNT(*) FROM chapters WHERE book_id = books.id AND {pub}) as published_chapter_count,
                (SELECT MAX(translation_date) FROM chapters WHERE book_id = books.id AND {pub}) as last_published_date
            FROM books
            ORDER BY {clause}
            ''', (now, now))

                rows = cursor.fetchall()

            result = []
            for row in rows:
                (book_id, title, author, language, created_date, cover_image, raw_cats,
                 chapter_count, description, is_public, total_source_chapters, status,
                 comments_enabled, source_url, notes, view_count, modified_date,
                 trad_to_simp, raw_tags, raw_modules, last_chapter_date, is_original,
                 published_chapter_count, last_published_date) = row
                try:
                    modules = json.loads(raw_modules) if raw_modules else None
                except (json.JSONDecodeError, TypeError):
                    modules = None
                try:
                    categories = json.loads(raw_cats) if raw_cats else None
                except (json.JSONDecodeError, TypeError):
                    categories = None
                try:
                    tags = json.loads(raw_tags) if raw_tags else []
                except (json.JSONDecodeError, TypeError):
                    tags = []
                result.append({
                    "id": book_id,
                    "title": title,
                    "author": author,
                    "language": language,
                    "created_date": created_date,
                    "modified_date": modified_date,
                    "last_chapter_date": last_chapter_date,
                    "cover_image": cover_image,
                    "categories": categories,
                    "chapter_count": chapter_count,
                    "description": description,
                    "is_public": bool(is_public) if is_public is not None else True,
                    "total_source_chapters": total_source_chapters,
                    "status": status or "ongoing",
                    "comments_enabled": bool(comments_enabled) if comments_enabled is not None else True,
                    "source_url": source_url,
                    "notes": notes,
                    "view_count": view_count or 0,
                    "trad_to_simp": trad_to_simp,
                    "tags": tags,
                    "modules": modules,
                    "is_original": bool(is_original) if is_original is not None else False,
                    "published_chapter_count": published_chapter_count or 0,
                    "last_published_date": last_published_date,
                })

            return result

        except Exception as e:
            self.logger.error(f"Error listing books: {e}")
            return []

    def delete_book(self, book_id):
        """
        Delete a book and all its chapters from the database.
        
        Args:
            book_id: Book ID to delete
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                # Check if book exists
                cursor.execute("SELECT title FROM books WHERE id = ?", (book_id,))
                book = cursor.fetchone()
                
                if not book:
                    self.logger.warning(f"Book with ID {book_id} not found")
                    return False
                
                book_title = book[0]
                
                # Enable foreign key constraints
                self.backend.enable_foreign_keys(conn)
                
                # Delete book (will cascade to chapters)
                cursor.execute("DELETE FROM books WHERE id = ?", (book_id,))
                
                # Also delete book-specific entities
                cursor.execute("DELETE FROM entities WHERE book_id = ?", (book_id,))
            self.invalidate_epub_cache(book_id)

            self.logger.info(f"Deleted book '{book_title}' (ID: {book_id}) and all its chapters")
            return True

        except Exception as e:
            self.logger.error(f"Error deleting book: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    # EPUB cache management
    def _epub_cache_dir(self):
        """Return the path to the EPUB cache directory."""
        return os.path.join(self.config.script_dir, "epub_cache")

    def invalidate_epub_cache(self, book_id):
        """Invalidate a book's cached EPUB so it will be regenerated on next export.

        Removes both the local on-disk cache file and, when Spaces/CDN is enabled,
        every EPUB blob under the book's ``epub/{book_id}`` prefix in object storage.
        """
        cache_path = os.path.join(self._epub_cache_dir(), f"{book_id}.epub")
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
                self.logger.info(f"Invalidated EPUB cache for book {book_id}")
            except OSError as e:
                self.logger.warning(f"Failed to remove cached EPUB for book {book_id}: {e}")

        # Best-effort purge of this book's EPUB objects from Spaces/CDN.
        try:
            import spaces
            if spaces.is_enabled(self.config):
                spaces.delete_prefix(self.config, f"epub/{book_id}")
        except Exception as e:
            self.logger.warning(f"Failed to purge Spaces EPUB objects for book {book_id}: {e}")

    def get_book_comments_enabled(self, book_id: int) -> bool:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT comments_enabled FROM books WHERE id = ?', (book_id,))
            row = cursor.fetchone()
        if row is None:
            return False
        return bool(row[0])

    def set_book_comments_enabled(self, book_id: int, enabled: bool):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE books SET comments_enabled = ? WHERE id = ?', (1 if enabled else 0, book_id))

    def get_token_ratio(self, book_id=None):
        """Return the average output-tokens-per-input-char ratio for progress estimation.

        Prefers book-specific data when book_id is provided; falls back to the
        global aggregate (book_id=0).  Returns 1.0 when no data is available.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                if book_id:
                    cursor.execute(
                        'SELECT total_input_chars, total_output_tokens FROM token_ratios WHERE book_id = ?',
                        (book_id,)
                    )
                    row = cursor.fetchone()
                    if row and row[0] > 0:
                        return row[1] / row[0]

                # Fall back to global row
                cursor.execute(
                    'SELECT total_input_chars, total_output_tokens FROM token_ratios WHERE book_id = 0'
                )
                row = cursor.fetchone()

            if row and row[0] > 0:
                return row[1] / row[0]

            return 1.2
        except Exception as e:
            self.logger.warning(f"Could not load token ratio: {e}")
            return 1.2

    def update_token_ratio(self, book_id, input_chars, output_tokens):
        """Add a chapter's char/token counts to the running totals for book and global stats."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                upsert_sql = self.backend.upsert_token_ratio_sql()

                if book_id:
                    cursor.execute(upsert_sql, (book_id, input_chars, output_tokens))

                # Always update global aggregate
                cursor.execute(upsert_sql, (0, input_chars, output_tokens))

                # Log updated stats
                lookup_id = book_id if book_id else 0
                cursor.execute(
                    'SELECT total_input_chars, total_output_tokens, sample_count FROM token_ratios WHERE book_id = ?',
                    (lookup_id,)
                )
                row = cursor.fetchone()

            if row and row[0] > 0:
                avg = row[1] / row[0]
                self.logger.info(f"Updated token ratio - average: {avg:.2f} over {row[2]} chapter(s)")
        except Exception as e:
            self.logger.error(f"Failed to update token ratio: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
