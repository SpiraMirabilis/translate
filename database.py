import json
import threading
import traceback
import unicodedata
import sqlite3
import os
import datetime
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Union, Tuple
from itertools import zip_longest
import re
from db_backend import create_backend
from modules import (apply_source_ingest, fire_book_module_events,
                     resolve_module_ids)
from db.core import (DatabaseCore, normalize_categories, DEFAULT_CATEGORIES,
                     DEFAULT_GENDERED_CATEGORIES, KNOWN_CATEGORY_ATTRIBUTES,
                     INCLUDE_SIMILAR_PREFIX)
from db.wp_repo import WpStateRepo
from db.logs_repo import LogsRepo
from db.recommendations_repo import RecommendationsRepo
from db.comments_repo import CommentsRepo
from db.footnotes_repo import FootnotesRepo
from db.queue_repo import QueueRepo


class DatabaseManager(QueueRepo, FootnotesRepo, CommentsRepo, RecommendationsRepo, LogsRepo, WpStateRepo, DatabaseCore):
    """Class to manage database operations including entities, books, and chapters using SQLite"""
    
    def __init__(self, config: 'TranslationConfig', logger: 'Logger', *, strict_writes: bool = False):
        self.config = config
        self.logger = logger
        self.strict_writes = strict_writes
        self.backend = create_backend(config)
        self.db_path = self.backend.db_path  # backward compat for external callers
        self.entities = {}  # Cached entities
        # Guards self.entities against concurrent mutation: the cache is
        # shared between the translation thread, the WP publish thread, and
        # (since handlers moved to the threadpool) request workers. Reentrant
        # because CRUD methods may call _load_entities under the lock.
        self._entities_lock = threading.RLock()
        self._initialize_database()
        self._load_entities()
        self._check_legacy_queue()


    

    # Book management section 
    def create_book(self, title, author=None, language='en', description=None, source_language='zh', target_language='en'):
        """
        Create a new book in the database.
        
        Args:
            title: Book title
            author: Book author (optional)
            language: Target language code (default: en)
            description: Book description (optional)
            source_language: Source language code (default: zh)
            target_language: Target language code (default: en)
            
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
            (title, author, language, description, created_date, modified_date, source_language, target_language)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (title, author, language, description, timestamp, timestamp, source_language, target_language))
                
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
                    view_count, trad_to_simp, tags, modules
                FROM books
                WHERE id = ?
                ''', (book_id,))
                else:
                    cursor.execute('''
                SELECT id, title, author, language, description, created_date, modified_date,
                    source_language, target_language, cover_image, categories, is_public,
                    total_source_chapters, status, comments_enabled, source_url, notes,
                    view_count, trad_to_simp, tags, modules
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
                            'source_url', 'notes', 'trad_to_simp', 'tags', 'modules']:
                        set_clause.append(f"{key} = ?")
                        if key in ('is_public', 'comments_enabled'):
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
            with self._conn() as conn:
                cursor = conn.cursor()

                cursor.execute(f'''
            SELECT id, title, author, language, created_date, cover_image, categories,
                (SELECT COUNT(*) FROM chapters WHERE book_id = books.id) as chapter_count,
                description, is_public, total_source_chapters, status, comments_enabled,
                source_url, notes, view_count, modified_date, trad_to_simp, tags, modules,
                (SELECT MAX(translation_date) FROM chapters WHERE book_id = books.id) as last_chapter_date
            FROM books
            ORDER BY {clause}
            ''')

                rows = cursor.fetchall()

            result = []
            for row in rows:
                (book_id, title, author, language, created_date, cover_image, raw_cats,
                 chapter_count, description, is_public, total_source_chapters, status,
                 comments_enabled, source_url, notes, view_count, modified_date,
                 trad_to_simp, raw_tags, raw_modules, last_chapter_date) = row
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
        
    # Private Book methods

    
    # End Book management section    
    
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

    # Chapter management section
    def save_chapter(self, book_id, chapter_number, title, untranslated_content, translated_content,
                    summary=None, translation_model=None):
        """
        Save a chapter to the database.
        
        Args:
            book_id: Book ID
            chapter_number: Chapter number
            title: Chapter title
            untranslated_content: Original untranslated text (list of lines)
            translated_content: Translated text (list of lines)
            summary: Chapter summary (optional)
            translation_model: Model used for translation (optional)
            
        Returns:
            int: Chapter ID if successful, None otherwise
        """
        try:
            # Get book info to make sure it exists
            book = self.get_book(book_id=book_id)
            if not book:
                self.logger.error(f"Book with ID {book_id} not found")
                return None

            # Per-book module source transforms (trad→simp, novel543 boilerplate strip, …)
            untranslated_content = apply_source_ingest(book, untranslated_content,
                                                       self.config, self.logger, db=self)

            # Serialize content if it's a list
            if isinstance(untranslated_content, list):
                untranslated_text = json.dumps(untranslated_content, ensure_ascii=False)
            else:
                untranslated_text = untranslated_content

            if isinstance(translated_content, list):
                translated_text = json.dumps(translated_content, ensure_ascii=False)
            else:
                translated_text = translated_content
            
            # Current timestamp
            timestamp = datetime.datetime.now().isoformat()
            
            # Get current translation model if not specified
            if translation_model is None:
                translation_model = self.config.translation_model
                
            # Post-commit side effects (cache invalidation, illustration links,
            # footnote re-render) stay OUTSIDE this scope: they must only run
            # once the chapter row is durably committed.
            with self._conn() as conn:
                cursor = conn.cursor()

                # Check if chapter already exists
                cursor.execute('''
                SELECT id FROM chapters
                WHERE book_id = ? AND chapter_number = ?
                ''', (book_id, chapter_number))

                existing = cursor.fetchone()

                if existing:
                    # Update existing chapter
                    chapter_id = existing[0]

                    cursor.execute('''
                    UPDATE chapters
                    SET title = ?, untranslated_content = ?, translated_content = ?,
                        summary = ?, translation_date = ?, translation_model = ?
                    WHERE id = ?
                    ''', (title, untranslated_text, translated_text, summary, timestamp,
                        translation_model, chapter_id))

                    # Update book modified date
                    cursor.execute('''
                    UPDATE books
                    SET modified_date = ?
                    WHERE id = ?
                    ''', (timestamp, book_id))

                    self.logger.info(f"Updated chapter {chapter_number} for book ID {book_id}")
                else:
                    # Insert new chapter
                    cursor.execute('''
                    INSERT INTO chapters
                    (book_id, chapter_number, title, untranslated_content, translated_content,
                    summary, translation_date, translation_model)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (book_id, chapter_number, title, untranslated_text, translated_text,
                        summary, timestamp, translation_model))

                    chapter_id = cursor.lastrowid

                    # Update book modified date
                    cursor.execute('''
                    UPDATE books
                    SET modified_date = ?
                    WHERE id = ?
                    ''', (timestamp, book_id))

                    self.logger.info(f"Added chapter {chapter_number} to book ID {book_id}")

            self.invalidate_epub_cache(book_id)

            # Link any illustration rows referenced by this chapter's content
            # (markers in the saved source array) to this chapter_id.
            try:
                from illustrations import markers_in
                if isinstance(untranslated_content, list):
                    src_lines = untranslated_content
                elif isinstance(untranslated_content, str):
                    src_lines = untranslated_content.split('\n')
                else:
                    src_lines = []
                marker_ids = markers_in(src_lines)
                if marker_ids:
                    self.link_illustrations_to_chapter(book_id, chapter_id, marker_ids)
            except Exception as e:
                self.logger.error(f"Illustration linkage skipped for chapter {chapter_number}: {e}")

            # Re-apply persisted footnotes onto the freshly-saved content. A brand-new
            # chapter has no footnote rows, so this is a no-op for initial translation;
            # on retranslation (or any re-save) it re-anchors them and flags orphans.
            try:
                if self.get_chapter_footnotes(chapter_id):
                    self.rerender_chapter_footnotes(chapter_id)
            except Exception as e:
                self.logger.error(f"Footnote reapply skipped for chapter {chapter_number}: {e}")

            return chapter_id

        except Exception as e:
            self.logger.error(f"Error saving chapter: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return None

    def get_chapter(self, chapter_id=None, book_id=None, chapter_number=None):
        """
        Get chapter data from the database.
        
        Args:
            chapter_id: Chapter ID (optional if book_id and chapter_number are provided)
            book_id: Book ID (required if chapter_id is not provided)
            chapter_number: Chapter number (required if chapter_id is not provided)
            
        Returns:
            dict: Chapter data dictionary or None if not found
        """
        if not chapter_id and (not book_id or not chapter_number):
            self.logger.error("Either chapter_id or both book_id and chapter_number must be provided")
            return None
            
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                if chapter_id:
                    cursor.execute('''
                SELECT c.id, c.book_id, c.chapter_number, c.title, c.untranslated_content,
                    c.translated_content, c.summary, c.translation_date, c.translation_model,
                    b.title as book_title, c.is_proofread
                FROM chapters c
                JOIN books b ON c.book_id = b.id
                WHERE c.id = ?
                ''', (chapter_id,))
                else:
                    cursor.execute('''
                SELECT c.id, c.book_id, c.chapter_number, c.title, c.untranslated_content,
                    c.translated_content, c.summary, c.translation_date, c.translation_model,
                    b.title as book_title, c.is_proofread
                FROM chapters c
                JOIN books b ON c.book_id = b.id
                WHERE c.book_id = ? AND c.chapter_number = ?
                ''', (book_id, chapter_number))
                
                row = cursor.fetchone()
            
            if not row:
                return None
                
            # Deserialize JSON content
            try:
                untranslated_content = json.loads(row[4])
            except json.JSONDecodeError:
                untranslated_content = row[4].split('\n')
                
            try:
                translated_content = json.loads(row[5])
            except json.JSONDecodeError:
                translated_content = row[5].split('\n')
                
            chapter_data = {
                "id": row[0],
                "book_id": row[1],
                "chapter": row[2],
                "title": row[3],
                "untranslated": untranslated_content,
                "content": translated_content,
                "summary": row[6],
                "translation_date": row[7],
                "model": row[8],
                "book_title": row[9],
                "is_proofread": row[10],
            }
            
            return chapter_data
            
        except Exception as e:
            self.logger.error(f"Error retrieving chapter data: {e}")
            return None

    def get_chapters_bulk(self, book_id, chapter_numbers=None, include_untranslated=False):
        """
        Fetch many chapters in one query (replaces per-chapter get_chapter loops
        in book export, EPUB generation and WordPress publishing).

        Args:
            book_id: Book ID
            chapter_numbers: Iterable of chapter numbers, or None for all chapters
            include_untranslated: Also decode + include the source text

        Returns:
            list: Chapter dicts shaped like get_chapter() (minus "untranslated"
                  unless requested), ordered by chapter_number. A row whose JSON
                  content is corrupt falls back to newline-splitting like
                  get_chapter(); a row that fails entirely is skipped, not fatal.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                src_col = ", c.untranslated_content" if include_untranslated else ""
                base = f'''
            SELECT c.id, c.book_id, c.chapter_number, c.title,
                c.translated_content, c.summary, c.translation_date,
                c.translation_model, b.title as book_title, c.is_proofread{src_col}
            FROM chapters c
            JOIN books b ON c.book_id = b.id
            WHERE c.book_id = ?'''

                rows = []
                if chapter_numbers is None:
                    cursor.execute(base + " ORDER BY c.chapter_number", (book_id,))
                    rows = cursor.fetchall()
                else:
                    nums = list(chapter_numbers)
                    # Chunk the IN list to stay well under parameter limits
                    for i in range(0, len(nums), 500):
                        chunk = nums[i:i + 500]
                        placeholders = ",".join("?" * len(chunk))
                        cursor.execute(
                            base + f" AND c.chapter_number IN ({placeholders})"
                            " ORDER BY c.chapter_number",
                            [book_id] + chunk)
                        rows.extend(cursor.fetchall())

            def _decode(raw):
                try:
                    return json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    return raw.split('\n') if isinstance(raw, str) else []

            result = []
            for row in rows:
                try:
                    chapter = {
                        "id": row[0],
                        "book_id": row[1],
                        "chapter": row[2],
                        "title": row[3],
                        "content": _decode(row[4]),
                        "summary": row[5],
                        "translation_date": row[6],
                        "model": row[7],
                        "book_title": row[8],
                        "is_proofread": row[9],
                    }
                    if include_untranslated:
                        chapter["untranslated"] = _decode(row[10])
                    result.append(chapter)
                except Exception as e:
                    self.logger.error(f"Skipping corrupt chapter row in bulk fetch: {e}")
            if chapter_numbers is not None:
                result.sort(key=lambda c: c["chapter"])
            return result

        except Exception as e:
            self.logger.error(f"Error bulk-fetching chapters: {e}")
            if self.strict_writes:
                raise
            return []

    def list_chapters(self, book_id, limit=None, offset=0):
        """
        List chapters for a specific book (all of them unless limit is given).

        Args:
            book_id: Book ID
            limit: Optional max rows to return (None = all, legacy behavior)
            offset: Rows to skip when limit is given

        Returns:
            list: List of chapter metadata dictionaries
        """
        try:
            # Verify book exists
            book = self.get_book(book_id=book_id)
            if not book:
                self.logger.warning(f"Book with ID {book_id} not found")
                return []
            with self._conn() as conn:
                cursor = conn.cursor()

                query = '''
            SELECT id, chapter_number, title, translation_date, translation_model, is_proofread
            FROM chapters
            WHERE book_id = ?
            ORDER BY chapter_number
            '''
                params = [book_id]
                if limit is not None:
                    query += ' LIMIT ? OFFSET ?'
                    params.extend([int(limit), int(offset)])

                cursor.execute(query, params)

                rows = cursor.fetchall()

            result = []
            for row in rows:
                chapter_id, chapter_number, title, translation_date, model, is_proofread = row
                result.append({
                    "id": chapter_id,
                    "chapter": chapter_number,
                    "title": title,
                    "translation_date": translation_date,
                    "model": model,
                    "is_proofread": is_proofread,
                })
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error listing chapters: {e}")
            return []

    def list_recent_translated_chapters(self, limit=50, book_id=None):
        """Return recently translated chapters from public books, joined with book info.

        Ordered by translation_date DESC. If book_id is given, restricted to that book
        (is_public gate still enforced). translation_date is ISO 8601, so lexicographic
        DESC matches chronological DESC.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                sql = '''
                SELECT c.id, c.book_id, c.chapter_number, c.title, c.summary,
                       c.translation_date, b.title AS book_title, b.author AS book_author
                FROM chapters c
                JOIN books b ON c.book_id = b.id
                WHERE b.is_public = 1
                  AND c.translation_date IS NOT NULL
            '''
                params = []
                if book_id is not None:
                    sql += ' AND c.book_id = ?'
                    params.append(book_id)
                sql += ' ORDER BY c.translation_date DESC LIMIT ?'
                params.append(limit)
                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "book_id": row[1],
                    "chapter": row[2],
                    "title": row[3],
                    "summary": row[4],
                    "translation_date": row[5],
                    "book_title": row[6],
                    "book_author": row[7],
                }
                for row in rows
            ]
        except Exception as e:
            self.logger.error(f"Error listing recent translated chapters: {e}")
            return []

    # Caps to prevent runaway memory use on common-substring queries.
    # match_count stays accurate; only the stored matches list is bounded.
    SEARCH_MAX_MATCHES_PER_CHAPTER = 200
    SEARCH_MAX_TOTAL_MATCHES = 2000
    SEARCH_MAX_TEXT_LEN = 500

    def search_book_chapters(self, book_id, query, scope='both', is_regex=False):
        """Search all chapters of a book for a query string.

        Args:
            book_id: Book ID
            query: Search string or regex pattern
            scope: 'translated', 'untranslated', or 'both'
            is_regex: Whether query is a regex pattern

        Returns:
            list of dicts with chapter_number, title, match_count, matches.
            match_count is the true count; matches may be truncated to bound
            response size (truncated=True is set on the chapter dict in that case).
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                SELECT chapter_number, title, untranslated_content, translated_content
                FROM chapters WHERE book_id = ? ORDER BY chapter_number
            ''', (book_id,))
                rows = cursor.fetchall()

            if is_regex:
                try:
                    pattern = re.compile(query, re.IGNORECASE)
                except re.error:
                    return []
            else:
                query_lower = query.lower()

            per_ch_cap = self.SEARCH_MAX_MATCHES_PER_CHAPTER
            total_cap = self.SEARCH_MAX_TOTAL_MATCHES
            text_cap = self.SEARCH_MAX_TEXT_LEN

            results = []
            total_collected = 0

            def _clip(line):
                if len(line) <= text_cap:
                    return line
                return line[:text_cap] + '…'

            for chapter_number, title, raw_untrans, raw_trans in rows:
                try:
                    untrans_lines = json.loads(raw_untrans) if raw_untrans else []
                except (json.JSONDecodeError, TypeError):
                    untrans_lines = raw_untrans.split('\n') if raw_untrans else []
                try:
                    trans_lines = json.loads(raw_trans) if raw_trans else []
                except (json.JSONDecodeError, TypeError):
                    trans_lines = raw_trans.split('\n') if raw_trans else []

                matches = []
                ch_match_count = 0

                def add_match(line_idx, col, length, field, line):
                    nonlocal ch_match_count, total_collected
                    ch_match_count += 1
                    if len(matches) >= per_ch_cap or total_collected >= total_cap:
                        return
                    matches.append({
                        'line': line_idx, 'col': col,
                        'length': length, 'field': field,
                        'text': _clip(line),
                    })
                    total_collected += 1

                # Search untranslated
                if scope in ('untranslated', 'both'):
                    for line_idx, line in enumerate(untrans_lines):
                        if is_regex:
                            for m in pattern.finditer(line):
                                add_match(line_idx, m.start(), m.end() - m.start(), 'untranslated', line)
                        else:
                            line_lower = line.lower()
                            start = 0
                            while True:
                                idx = line_lower.find(query_lower, start)
                                if idx == -1:
                                    break
                                add_match(line_idx, idx, len(query), 'untranslated', line)
                                start = idx + 1

                # Search translated
                if scope in ('translated', 'both'):
                    for line_idx, line in enumerate(trans_lines):
                        if is_regex:
                            for m in pattern.finditer(line):
                                add_match(line_idx, m.start(), m.end() - m.start(), 'translated', line)
                        else:
                            line_lower = line.lower()
                            start = 0
                            while True:
                                idx = line_lower.find(query_lower, start)
                                if idx == -1:
                                    break
                                add_match(line_idx, idx, len(query), 'translated', line)
                                start = idx + 1

                if ch_match_count > 0:
                    entry = {
                        'chapter_number': chapter_number,
                        'title': title or f'Chapter {chapter_number}',
                        'match_count': ch_match_count,
                        'matches': matches,
                    }
                    if len(matches) < ch_match_count:
                        entry['truncated'] = True
                    results.append(entry)

            return results
        except Exception as e:
            self.logger.error(f"Error searching chapters: {e}")
            return []

    # In-memory undo snapshot: { book_id: { 'snapshots': [(ch_id, old_content), ...], 'query': str, 'replacement': str } }
    _replace_undo = {}

    def replace_in_chapters(self, book_id, query, replacement, chapter_numbers=None, is_regex=False):
        """Replace text in translated content of chapters.

        Saves a snapshot of affected chapters before modifying, enabling undo.

        Returns:
            dict with affected_chapters, total_replacements, and can_undo flag
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                sql = 'SELECT id, chapter_number, translated_content FROM chapters WHERE book_id = ?'
                params = [book_id]
                if chapter_numbers:
                    placeholders = ','.join('?' * len(chapter_numbers))
                    sql += f' AND chapter_number IN ({placeholders})'
                    params.extend(chapter_numbers)
                sql += ' ORDER BY chapter_number'

                cursor.execute(sql, params)
                rows = cursor.fetchall()

                if is_regex:
                    try:
                        pattern = re.compile(query, re.IGNORECASE)
                    except re.error:
                        return {'affected_chapters': 0, 'total_replacements': 0, 'can_undo': False}

                affected = 0
                total = 0
                snapshots = []

                for ch_id, ch_num, raw_content in rows:
                    try:
                        lines = json.loads(raw_content) if raw_content else []
                    except (json.JSONDecodeError, TypeError):
                        lines = raw_content.split('\n') if raw_content else []

                    ch_replacements = 0
                    new_lines = []
                    for line in lines:
                        if is_regex:
                            new_line, count = pattern.subn(replacement, line)
                        else:
                            count = 0
                            new_line = line
                            lower_line = new_line.lower()
                            query_lower = query.lower()
                            pos = 0
                            result_parts = []
                            while True:
                                idx = lower_line.find(query_lower, pos)
                                if idx == -1:
                                    result_parts.append(new_line[pos:])
                                    break
                                result_parts.append(new_line[pos:idx])
                                result_parts.append(replacement)
                                count += 1
                                pos = idx + len(query)
                            if count > 0:
                                new_line = ''.join(result_parts)

                        new_lines.append(new_line)
                        ch_replacements += count

                    if ch_replacements > 0:
                        # Snapshot the original content before overwriting
                        snapshots.append((ch_id, raw_content))
                        cursor.execute(
                            'UPDATE chapters SET translated_content = ? WHERE id = ?',
                            (json.dumps(new_lines, ensure_ascii=False), ch_id)
                        )
                        affected += 1
                        total += ch_replacements

            if affected > 0:
                self.invalidate_epub_cache(book_id)

            # Store undo snapshot (one level, keyed by book)
            if snapshots:
                DatabaseManager._replace_undo[book_id] = {
                    'snapshots': snapshots,
                    'query': query,
                    'replacement': replacement,
                    'affected_chapters': affected,
                    'total_replacements': total,
                }

            return {'affected_chapters': affected, 'total_replacements': total, 'can_undo': len(snapshots) > 0}

        except Exception as e:
            self.logger.error(f"Error replacing in chapters: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return {'affected_chapters': 0, 'total_replacements': 0, 'can_undo': False}

    def undo_replace(self, book_id):
        """Undo the last replace_in_chapters operation for a book.

        Returns:
            dict with status and number of chapters restored, or None if nothing to undo
        """
        undo = DatabaseManager._replace_undo.pop(book_id, None)
        if not undo:
            return None

        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                for ch_id, old_content in undo['snapshots']:
                    cursor.execute(
                        'UPDATE chapters SET translated_content = ? WHERE id = ?',
                        (old_content, ch_id)
                    )
            self.invalidate_epub_cache(book_id)
            return {'restored_chapters': len(undo['snapshots'])}
        except Exception as e:
            self.logger.error(f"Error undoing replace: {e}")
            return None

    def has_replace_undo(self, book_id):
        """Check if an undo snapshot exists for a book."""
        return book_id in DatabaseManager._replace_undo

    def delete_chapter(self, chapter_id=None, book_id=None, chapter_number=None):
        """
        Delete a chapter from the database.
        
        Args:
            chapter_id: Chapter ID (optional if book_id and chapter_number are provided)
            book_id: Book ID (required if chapter_id is not provided)
            chapter_number: Chapter number (required if chapter_id is not provided)
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not chapter_id and (not book_id or not chapter_number):
            self.logger.error("Either chapter_id or both book_id and chapter_number must be provided")
            return False
            
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                
                # Get chapter details first (for logging)
                if chapter_id:
                    cursor.execute('''
                SELECT book_id, chapter_number, title FROM chapters WHERE id = ?
                ''', (chapter_id,))
                else:
                    cursor.execute('''
                SELECT id, title FROM chapters WHERE book_id = ? AND chapter_number = ?
                ''', (book_id, chapter_number))
                    
                chapter = cursor.fetchone()
                
                if not chapter:
                    self.logger.warning("Chapter not found")
                    return False
                    
                # Delete the chapter
                if chapter_id:
                    cursor.execute("DELETE FROM chapters WHERE id = ?", (chapter_id,))
                else:
                    cursor.execute('''
                DELETE FROM chapters WHERE book_id = ? AND chapter_number = ?
                ''', (book_id, chapter_number))
                
                # Update book modified date
                timestamp = datetime.datetime.now().isoformat()
                
                if chapter_id:
                    book_id = chapter[0]
                
                cursor.execute('''
            UPDATE books
            SET modified_date = ?
            WHERE id = ?
            ''', (timestamp, book_id))
            self.invalidate_epub_cache(book_id)

            if chapter_id:
                self.logger.info(f"Deleted chapter {chapter[1]}: '{chapter[2]}' from book ID {chapter[0]}")
            else:
                self.logger.info(f"Deleted chapter {chapter_number} (ID: {chapter[0]}): '{chapter[1]}' from book ID {book_id}")

            return True

        except Exception as e:
            self.logger.error(f"Error deleting chapter: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def renumber_chapter(self, book_id, chapter_number, new_chapter_number):
        """
        Move a chapter to a new chapter_number, updating all related tables.

        Returns:
            (True, None) on success, (False, reason) on failure. Reasons:
              - "not_found": source chapter doesn't exist
              - "target_exists": target chapter_number already in use
              - "invalid": new_chapter_number invalid (<1)
        """
        if not isinstance(new_chapter_number, int) or new_chapter_number < 1:
            return False, "invalid"
        if new_chapter_number == chapter_number:
            return True, None

        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM chapters WHERE book_id = ? AND chapter_number = ?",
                    (book_id, chapter_number),
                )
                if not cursor.fetchone():
                    return False, "not_found"

                cursor.execute(
                    "SELECT id FROM chapters WHERE book_id = ? AND chapter_number = ?",
                    (book_id, new_chapter_number),
                )
                if cursor.fetchone():
                    return False, "target_exists"

                cursor.execute(
                    "UPDATE chapters SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                cursor.execute(
                    "UPDATE wp_publish_state SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                cursor.execute(
                    "UPDATE comments SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                cursor.execute(
                    "UPDATE api_calls SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                cursor.execute(
                    "UPDATE reader_log SET chapter_number = ? WHERE book_id = ? AND chapter_number = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
                # activity_log uses column name `chapter`, not `chapter_number`
                cursor.execute(
                    "UPDATE activity_log SET chapter = ? WHERE book_id = ? AND chapter = ?",
                    (new_chapter_number, book_id, chapter_number),
                )
        except Exception as e:
            self.logger.error(f"Error renumbering chapter {chapter_number} -> {new_chapter_number}: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False, str(e)
        self.invalidate_epub_cache(book_id)
        self.logger.info(f"Renumbered chapter {chapter_number} -> {new_chapter_number} for book ID {book_id}")
        return True, None







    # Footnote management section
    #
    # Footnotes are persisted here so they survive retranslation. The inline
    # "[n]" marker + definition block in chapter content is a derived rendering
    # re-applied on every save by anchor (the English term the marker hugs); see
    # footnotes.py and rerender_chapter_footnotes below.

















    # ------------------------------------------------------------------
    # Activity log
    # ------------------------------------------------------------------




    # ------------------------------------------------------------------
    # Reader view log
    # ------------------------------------------------------------------





    # End Queue management section

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
    # WordPress publish state
    # ------------------------------------------------------------------






    # ------------------------------------------------------------------
    # API call logging
    # ------------------------------------------------------------------






    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------







    # ------------------------------------------------------------------
    # Comments / commenters / bans
    # ------------------------------------------------------------------

















    # --- bans ---






    # --- per-book toggle ---

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

    # --- email suppressions ---





    # --- notification idempotency log ---


