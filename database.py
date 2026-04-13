import json
import unicodedata
import sqlite3
import os
import datetime
from typing import Dict, List, Optional, Any, Union, Tuple
from itertools import zip_longest
import re
from db_backend import create_backend

DEFAULT_CATEGORIES = [
    'characters', 'places', 'organizations', 'abilities',
    'titles', 'equipment', 'creatures'
]

class DatabaseManager:
    """Class to manage database operations including entities, books, and chapters using SQLite"""
    
    def __init__(self, config: 'TranslationConfig', logger: 'Logger'):
        self.config = config
        self.logger = logger
        self.backend = create_backend(config)
        self.db_path = self.backend.db_path  # backward compat for external callers
        self.entities = {}  # Cached entities
        self._initialize_database()
        self._load_entities()
        self._check_legacy_queue()

    def get_connection(self):
        """Return a new database connection via the configured backend."""
        return self.backend.get_connection()
    
    def _initialize_database(self):
        """Initialize the database with proper schema if it doesn't exist"""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()

            # Create all tables using backend-specific DDL
            for ddl in self.backend.create_tables_ddl():
                try:
                    cursor.execute(ddl)
                except Exception:
                    # Index may already exist (MySQL raises on IF NOT EXISTS for some index forms)
                    pass

            # Migrations: add columns if missing
            entity_cols = self.backend.get_table_columns(conn, 'entities')
            if 'origin_chapter' not in entity_cols:
                cursor.execute("ALTER TABLE entities ADD COLUMN origin_chapter INTEGER")
                self.logger.info("Added origin_chapter column to entities table")
            # Backfill: set origin_chapter = last_chapter for entities missing it
            # Only copy values that are actually numeric (SQLite allows text in INTEGER columns, MySQL doesn't)
            if self.backend.name == 'mysql':
                cursor.execute("UPDATE entities SET origin_chapter = CAST(last_chapter AS SIGNED) WHERE origin_chapter IS NULL AND last_chapter IS NOT NULL AND last_chapter REGEXP '^[0-9]+$'")
            else:
                cursor.execute("UPDATE entities SET origin_chapter = last_chapter WHERE origin_chapter IS NULL AND last_chapter IS NOT NULL")
            if cursor.rowcount > 0:
                self.logger.info(f"Backfilled origin_chapter for {cursor.rowcount} entities")
            if 'note' not in entity_cols:
                cursor.execute("ALTER TABLE entities ADD COLUMN note TEXT")
                self.logger.info("Added note column to entities table")

            chapter_cols = self.backend.get_table_columns(conn, 'chapters')
            if 'is_proofread' not in chapter_cols:
                if self.backend.name == 'mysql':
                    cursor.execute("ALTER TABLE chapters ADD COLUMN is_proofread DATETIME NULL")
                else:
                    cursor.execute("ALTER TABLE chapters ADD COLUMN is_proofread TEXT")
                self.logger.info("Added is_proofread column to chapters table")
            else:
                # Migrate from INTEGER (0/1) to timestamp if needed
                if self.backend.name == 'mysql':
                    # Check if column is still INT and needs conversion to DATETIME
                    cursor.execute("SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chapters' AND COLUMN_NAME = 'is_proofread'")
                    row = cursor.fetchone()
                    if row and row[0] == 'int':
                        now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                        # Add a temp DATETIME column, copy data, swap
                        cursor.execute("ALTER TABLE chapters ADD COLUMN is_proofread_new DATETIME NULL")
                        cursor.execute("UPDATE chapters SET is_proofread_new = ? WHERE is_proofread = 1", (now,))
                        cursor.execute("ALTER TABLE chapters DROP COLUMN is_proofread")
                        cursor.execute("ALTER TABLE chapters CHANGE COLUMN is_proofread_new is_proofread DATETIME NULL")
                        self.logger.info("Migrated is_proofread from INT to DATETIME (MySQL)")
                else:
                    cursor.execute("SELECT COUNT(*) FROM chapters WHERE is_proofread = '1' OR is_proofread = '0'")
                    count = cursor.fetchone()[0]
                    if count > 0:
                        now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
                        cursor.execute("UPDATE chapters SET is_proofread = ? WHERE is_proofread = '1'", (now,))
                        cursor.execute("UPDATE chapters SET is_proofread = NULL WHERE is_proofread = '0'")
                        self.logger.info("Migrated is_proofread from boolean to timestamp")

            book_cols = self.backend.get_table_columns(conn, 'books')
            if 'cover_image' not in book_cols:
                cursor.execute("ALTER TABLE books ADD COLUMN cover_image TEXT")
                self.logger.info("Added cover_image column to books table")
            if 'categories' not in book_cols:
                cursor.execute("ALTER TABLE books ADD COLUMN categories TEXT")
                self.logger.info("Added categories column to books table")
            if 'is_public' not in book_cols:
                cursor.execute("ALTER TABLE books ADD COLUMN is_public INTEGER DEFAULT 1")
                self.logger.info("Added is_public column to books table")
            if 'total_source_chapters' not in book_cols:
                cursor.execute("ALTER TABLE books ADD COLUMN total_source_chapters INTEGER")
                self.logger.info("Added total_source_chapters column to books table")
            if 'status' not in book_cols:
                cursor.execute("ALTER TABLE books ADD COLUMN status TEXT DEFAULT 'ongoing'")
                self.logger.info("Added status column to books table")

            queue_cols = self.backend.get_table_columns(conn, 'queue')
            if 'retranslation_reason' not in queue_cols:
                cursor.execute("ALTER TABLE queue ADD COLUMN retranslation_reason TEXT")
                self.logger.info("Added retranslation_reason column to queue table")

            # Create covers directory (only meaningful for local installs)
            if self.backend.name == 'sqlite':
                covers_dir = os.path.join(os.path.dirname(self.db_path), "covers")
            else:
                covers_dir = os.path.join(self.config.script_dir, "covers")
            os.makedirs(covers_dir, exist_ok=True)

            conn.commit()
            conn.close()
            self.logger.info("Database initialized successfully")
        except Exception as e:
            self.logger.error(f"Database initialization error: {e}")
            raise

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
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            
            # Check if book already exists
            cursor.execute("SELECT id FROM books WHERE title = ?", (title,))
            existing = cursor.fetchone()
            
            if existing:
                self.logger.info(f"Book '{title}' already exists with ID {existing[0]}")
                conn.close()
                return existing[0]
            
            # Current timestamp
            timestamp = datetime.datetime.now().isoformat()
            
            cursor.execute('''
            INSERT INTO books
            (title, author, language, description, created_date, modified_date, source_language, target_language)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (title, author, language, description, timestamp, timestamp, source_language, target_language))
            
            book_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            self.logger.info(f"Created new book: '{title}' with ID {book_id}")
            return book_id
            
        except Exception as e:
            self.logger.error(f"Error creating book: {e}")
            return None
    

    def get_book_prompt_template(self, book_id):
        """
        Get the prompt template for a specific book.
        Returns None if no custom template is set.
        """
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT prompt_template FROM books
            WHERE id = ?
            ''', (book_id,))
            
            result = cursor.fetchone()
            conn.close()
            
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
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
            UPDATE books
            SET prompt_template = ?
            WHERE id = ?
            ''', (prompt_template, book_id))
            
            conn.commit()
            conn.close()
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting book prompt template: {e}")
            return False

    def get_book_categories(self, book_id):
        """Get entity categories for a book. Returns DEFAULT_CATEGORIES if none set."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT categories FROM books WHERE id = ?", (book_id,))
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                return json.loads(row[0])
            return list(DEFAULT_CATEGORIES)
        except Exception as e:
            self.logger.error(f"Error getting book categories: {e}")
            return list(DEFAULT_CATEGORIES)

    def set_book_categories(self, book_id, categories):
        """Set entity categories for a book. Pass None to reset to defaults."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            value = json.dumps(categories) if categories is not None else None
            cursor.execute("UPDATE books SET categories = ? WHERE id = ?", (value, book_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            self.logger.error(f"Error setting book categories: {e}")
            return False

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
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            
            if book_id:
                cursor.execute('''
                SELECT id, title, author, language, description, created_date, modified_date,
                    source_language, target_language, cover_image, categories, is_public,
                    total_source_chapters, status
                FROM books
                WHERE id = ?
                ''', (book_id,))
            else:
                cursor.execute('''
                SELECT id, title, author, language, description, created_date, modified_date,
                    source_language, target_language, cover_image, categories, is_public,
                    total_source_chapters, status
                FROM books
                WHERE title = ?
                ''', (title,))

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            raw_cats = row[10] if len(row) > 10 else None
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
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            
            # Check if book exists
            cursor.execute("SELECT 1 FROM books WHERE id = ?", (book_id,))
            if not cursor.fetchone():
                self.logger.warning(f"Book with ID {book_id} not found")
                conn.close()
                return False
            
            # Build the SET clause dynamically based on provided kwargs
            set_clause = []
            values = []
            
            # Update modified_date automatically
            kwargs["modified_date"] = datetime.datetime.now().isoformat()
            
            for key, value in kwargs.items():
                if key in ['title', 'author', 'language', 'description', 'source_language',
                        'target_language', 'modified_date', 'cover_image', 'is_public',
                        'total_source_chapters', 'status']:
                    set_clause.append(f"{key} = ?")
                    if key == 'is_public':
                        values.append(int(value))
                    elif key == 'total_source_chapters':
                        values.append(int(value) if value is not None else None)
                    else:
                        values.append(value)
            
            if not set_clause:
                self.logger.warning("No valid fields to update")
                conn.close()
                return False
            
            # Complete the parameter list with book_id
            values.append(book_id)
            
            # Execute the update
            cursor.execute(f'''
            UPDATE books 
            SET {', '.join(set_clause)}
            WHERE id = ?
            ''', values)
            
            conn.commit()
            conn.close()

            # Invalidate cached EPUB if metadata that affects it changed
            epub_fields = {'title', 'author', 'language', 'description', 'cover_image'}
            if epub_fields & set(kwargs):
                self.invalidate_epub_cache(book_id)

            self.logger.info(f"Updated book with ID {book_id}")
            return True

        except Exception as e:
            self.logger.error(f"Error updating book: {e}")
            return False

    def list_books(self):
        """
        List all books in the database.
        
        Returns:
            list: List of book information dictionaries
        """
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT id, title, author, language, created_date, cover_image, categories,
                (SELECT COUNT(*) FROM chapters WHERE book_id = books.id) as chapter_count,
                description, is_public, total_source_chapters, status
            FROM books
            ORDER BY title
            ''')

            rows = cursor.fetchall()
            conn.close()

            result = []
            for row in rows:
                book_id, title, author, language, created_date, cover_image, raw_cats, chapter_count, description, is_public, total_source_chapters, status = row
                result.append({
                    "id": book_id,
                    "title": title,
                    "author": author,
                    "language": language,
                    "created_date": created_date,
                    "cover_image": cover_image,
                    "categories": json.loads(raw_cats) if raw_cats else None,
                    "chapter_count": chapter_count,
                    "description": description,
                    "is_public": bool(is_public) if is_public is not None else True,
                    "total_source_chapters": total_source_chapters,
                    "status": status or "ongoing",
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
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            
            # Check if book exists
            cursor.execute("SELECT title FROM books WHERE id = ?", (book_id,))
            book = cursor.fetchone()
            
            if not book:
                self.logger.warning(f"Book with ID {book_id} not found")
                conn.close()
                return False
            
            book_title = book[0]
            
            # Enable foreign key constraints
            self.backend.enable_foreign_keys(conn)
            
            # Delete book (will cascade to chapters)
            cursor.execute("DELETE FROM books WHERE id = ?", (book_id,))
            
            # Also delete book-specific entities
            cursor.execute("DELETE FROM entities WHERE book_id = ?", (book_id,))
            
            conn.commit()
            conn.close()
            self.invalidate_epub_cache(book_id)

            self.logger.info(f"Deleted book '{book_title}' (ID: {book_id}) and all its chapters")
            return True

        except Exception as e:
            self.logger.error(f"Error deleting book: {e}")
            return False
        
    # Private Book methods

    
    # End Book management section    
    
    # EPUB cache management
    def _epub_cache_dir(self):
        """Return the path to the EPUB cache directory."""
        return os.path.join(self.config.script_dir, "epub_cache")

    def invalidate_epub_cache(self, book_id):
        """Delete the cached EPUB file for a book so it will be regenerated on next export."""
        cache_path = os.path.join(self._epub_cache_dir(), f"{book_id}.epub")
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
                self.logger.info(f"Invalidated EPUB cache for book {book_id}")
            except OSError as e:
                self.logger.warning(f"Failed to remove cached EPUB for book {book_id}: {e}")

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
                
            conn = self.backend.get_connection()
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
            
            conn.commit()
            conn.close()
            self.invalidate_epub_cache(book_id)

            return chapter_id

        except Exception as e:
            self.logger.error(f"Error saving chapter: {e}")
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
            conn = self.backend.get_connection()
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
            conn.close()
            
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

    def list_chapters(self, book_id):
        """
        List all chapters for a specific book.
        
        Args:
            book_id: Book ID
            
        Returns:
            list: List of chapter metadata dictionaries
        """
        try:
            # Verify book exists
            book = self.get_book(book_id=book_id)
            if not book:
                self.logger.warning(f"Book with ID {book_id} not found")
                return []
                
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT id, chapter_number, title, translation_date, translation_model, is_proofread
            FROM chapters
            WHERE book_id = ?
            ORDER BY chapter_number
            ''', (book_id,))

            rows = cursor.fetchall()
            conn.close()

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

    def search_book_chapters(self, book_id, query, scope='both', is_regex=False):
        """Search all chapters of a book for a query string.

        Args:
            book_id: Book ID
            query: Search string or regex pattern
            scope: 'translated', 'untranslated', or 'both'
            is_regex: Whether query is a regex pattern

        Returns:
            list of dicts with chapter_number, title, match_count, matches
        """
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT chapter_number, title, untranslated_content, translated_content
                FROM chapters WHERE book_id = ? ORDER BY chapter_number
            ''', (book_id,))
            rows = cursor.fetchall()
            conn.close()

            if is_regex:
                try:
                    pattern = re.compile(query, re.IGNORECASE)
                except re.error:
                    return []
            else:
                query_lower = query.lower()

            results = []
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

                # Search untranslated
                if scope in ('untranslated', 'both'):
                    for line_idx, line in enumerate(untrans_lines):
                        if is_regex:
                            for m in pattern.finditer(line):
                                matches.append({
                                    'line': line_idx, 'col': m.start(),
                                    'length': m.end() - m.start(), 'field': 'untranslated',
                                    'text': line,
                                })
                        else:
                            line_lower = line.lower()
                            start = 0
                            while True:
                                idx = line_lower.find(query_lower, start)
                                if idx == -1:
                                    break
                                matches.append({
                                    'line': line_idx, 'col': idx,
                                    'length': len(query), 'field': 'untranslated',
                                    'text': line,
                                })
                                start = idx + 1

                # Search translated
                if scope in ('translated', 'both'):
                    for line_idx, line in enumerate(trans_lines):
                        if is_regex:
                            for m in pattern.finditer(line):
                                matches.append({
                                    'line': line_idx, 'col': m.start(),
                                    'length': m.end() - m.start(), 'field': 'translated',
                                    'text': line,
                                })
                        else:
                            line_lower = line.lower()
                            start = 0
                            while True:
                                idx = line_lower.find(query_lower, start)
                                if idx == -1:
                                    break
                                matches.append({
                                    'line': line_idx, 'col': idx,
                                    'length': len(query), 'field': 'translated',
                                    'text': line,
                                })
                                start = idx + 1

                if matches:
                    results.append({
                        'chapter_number': chapter_number,
                        'title': title or f'Chapter {chapter_number}',
                        'match_count': len(matches),
                        'matches': matches,
                    })

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
            conn = self.backend.get_connection()
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
                    conn.close()
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

            conn.commit()
            conn.close()

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
            self.logger.error(f"Error replacing in chapters: {e}")
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
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            for ch_id, old_content in undo['snapshots']:
                cursor.execute(
                    'UPDATE chapters SET translated_content = ? WHERE id = ?',
                    (old_content, ch_id)
                )
            conn.commit()
            conn.close()
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
            conn = self.backend.get_connection()
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
                conn.close()
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
            
            conn.commit()
            conn.close()
            self.invalidate_epub_cache(book_id)

            if chapter_id:
                self.logger.info(f"Deleted chapter {chapter[1]}: '{chapter[2]}' from book ID {chapter[0]}")
            else:
                self.logger.info(f"Deleted chapter {chapter_number} (ID: {chapter[0]}): '{chapter[1]}' from book ID {book_id}")

            return True

        except Exception as e:
            self.logger.error(f"Error deleting chapter: {e}")
            return False

    # Queue management section
    def add_to_queue(self, book_id, content, title=None, chapter_number=None, source=None, metadata=None, priority=False, retranslation_reason=None):
        """
        Add an item to the translation queue.

        Args:
            book_id: Book ID (required, NOT NULL)
            content: List of content lines or string
            title: Chapter title (optional)
            chapter_number: Chapter number (optional)
            source: Source file path or description (optional)
            metadata: Additional metadata dict (optional)
            priority: If True, place at the front of the queue instead of the back
            retranslation_reason: Optional free-text reason shown to the model
                when retranslating an existing chapter. Appended to the system prompt.

        Returns:
            int: Queue item ID if successful, None otherwise
        """
        try:
            # Verify book exists
            book = self.get_book(book_id=book_id)
            if not book:
                self.logger.error(f"Book with ID {book_id} not found")
                return None

            conn = self.backend.get_connection()
            cursor = conn.cursor()

            if priority:
                # Place at front: use min(position) - 1
                cursor.execute('SELECT MIN(position) FROM queue')
                min_pos = cursor.fetchone()[0]
                next_position = (min_pos - 1) if min_pos is not None else 0
            else:
                # Place at back: use max(position) + 1
                cursor.execute('SELECT MAX(position) FROM queue')
                max_pos = cursor.fetchone()[0]
                next_position = (max_pos + 1) if max_pos is not None else 0

            # Serialize content as JSON if list (like chapters table)
            if isinstance(content, list):
                content_json = json.dumps(content, ensure_ascii=False)
            else:
                content_json = content

            # Serialize metadata if provided
            metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

            # Get current timestamp
            from datetime import datetime
            created_date = datetime.now().isoformat()

            # Normalize reason: treat empty/whitespace as None
            reason = (retranslation_reason or "").strip() or None

            # Insert queue item
            cursor.execute('''
            INSERT INTO queue (book_id, chapter_number, title, source, content, metadata, position, created_date, retranslation_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (book_id, chapter_number, title or "Untitled", source, content_json, metadata_json, next_position, created_date, reason))

            queue_id = cursor.lastrowid
            conn.commit()
            conn.close()

            self.logger.info(f"Added item to queue (ID: {queue_id}, position: {next_position}) for book '{book['title']}'")
            return queue_id

        except Exception as e:
            self.logger.error(f"Error adding to queue: {e}")
            return None

    def get_next_queue_item(self, book_id=None):
        """
        Get the next item from the queue (lowest position).

        Args:
            book_id: Optional book ID to filter by specific book

        Returns:
            dict: Queue item data or None if queue empty
        """
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()

            # Build query with optional book_id filter
            if book_id:
                cursor.execute('''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE q.book_id = ?
                ORDER BY q.position ASC
                LIMIT 1
                ''', (book_id,))
            else:
                cursor.execute('''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                ORDER BY q.position ASC
                LIMIT 1
                ''')

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            # Deserialize content (like get_chapter)
            content_json = row[5]
            try:
                content = json.loads(content_json)
            except:
                content = content_json  # Fallback to string if not valid JSON

            # Deserialize metadata if present
            metadata_json = row[6]
            metadata = None
            if metadata_json:
                try:
                    metadata = json.loads(metadata_json)
                except:
                    pass

            return {
                'id': row[0],
                'book_id': row[1],
                'chapter_number': row[2],
                'title': row[3],
                'source': row[4],
                'content': content,
                'metadata': metadata,
                'position': row[7],
                'created_date': row[8],
                'book_title': row[9],
                'retranslation_reason': row[10],
            }

        except Exception as e:
            self.logger.error(f"Error getting next queue item: {e}")
            return None

    def remove_from_queue(self, queue_id):
        """
        Remove an item from the queue and reorder remaining items.

        Args:
            queue_id: Queue item ID to remove

        Returns:
            bool: True if successful
        """
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()

            # Get the position of the item being removed
            cursor.execute('SELECT position FROM queue WHERE id = ?', (queue_id,))
            row = cursor.fetchone()

            if not row:
                self.logger.warning(f"Queue item {queue_id} not found")
                conn.close()
                return False

            removed_position = row[0]

            # Delete the item (no need to reorder — gaps in position are fine)
            cursor.execute('DELETE FROM queue WHERE id = ?', (queue_id,))

            conn.commit()
            conn.close()

            self.logger.info(f"Removed queue item {queue_id} from position {removed_position}")
            return True

        except Exception as e:
            self.logger.error(f"Error removing from queue: {e}")
            return False

    def list_queue(self, book_id=None):
        """
        List all items in the queue.

        Args:
            book_id: Optional book ID to filter by specific book

        Returns:
            list: List of queue item dicts ordered by position
        """
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()

            # Build query with optional book_id filter
            if book_id:
                cursor.execute('''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE q.book_id = ?
                ORDER BY q.position ASC
                ''', (book_id,))
            else:
                cursor.execute('''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                ORDER BY q.position ASC
                ''')

            rows = cursor.fetchall()
            conn.close()

            result = []
            for row in rows:
                # Deserialize content
                content_json = row[5]
                try:
                    content = json.loads(content_json)
                except:
                    content = content_json

                # Deserialize metadata if present
                metadata_json = row[6]
                metadata = None
                if metadata_json:
                    try:
                        metadata = json.loads(metadata_json)
                    except:
                        pass

                result.append({
                    'id': row[0],
                    'book_id': row[1],
                    'chapter_number': row[2],
                    'title': row[3],
                    'source': row[4],
                    'content': content,
                    'metadata': metadata,
                    'position': row[7],
                    'created_date': row[8],
                    'book_title': row[9],
                    'retranslation_reason': row[10],
                })

            return result

        except Exception as e:
            self.logger.error(f"Error listing queue: {e}")
            return []

    def clear_queue(self, book_id=None):
        """
        Clear the queue (all items or for specific book).

        Args:
            book_id: Optional book ID to clear queue for specific book only

        Returns:
            int: Number of items removed
        """
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()

            if book_id:
                # Clear queue for specific book
                cursor.execute('DELETE FROM queue WHERE book_id = ?', (book_id,))
                count = cursor.rowcount

                # Reorder positions after deletion
                # Get all remaining items ordered by position
                cursor.execute('SELECT id FROM queue ORDER BY position ASC')
                items = cursor.fetchall()

                # Update positions to be sequential
                for i, (item_id,) in enumerate(items):
                    cursor.execute('UPDATE queue SET position = ? WHERE id = ?', (i, item_id))
            else:
                # Clear entire queue
                cursor.execute('DELETE FROM queue')
                count = cursor.rowcount

            conn.commit()
            conn.close()

            self.logger.info(f"Cleared {count} items from queue" + (f" for book_id {book_id}" if book_id else ""))
            return count

        except Exception as e:
            self.logger.error(f"Error clearing queue: {e}")
            return 0

    def get_queue_count(self, book_id=None):
        """
        Get count of items in queue.

        Args:
            book_id: Optional book ID to count for specific book

        Returns:
            int: Number of items in queue
        """
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()

            if book_id:
                cursor.execute('SELECT COUNT(*) FROM queue WHERE book_id = ?', (book_id,))
            else:
                cursor.execute('SELECT COUNT(*) FROM queue')

            count = cursor.fetchone()[0]
            conn.close()

            return count

        except Exception as e:
            self.logger.error(f"Error getting queue count: {e}")
            return 0

    def check_duplicate_in_queue(self, book_id, chapter_number):
        """
        Check if a chapter is already in the queue.

        Args:
            book_id: Book ID
            chapter_number: Chapter number

        Returns:
            bool: True if duplicate exists
        """
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()

            cursor.execute('SELECT id FROM queue WHERE book_id = ? AND chapter_number = ?',
                          (book_id, chapter_number))

            result = cursor.fetchone()
            conn.close()

            return result is not None

        except Exception as e:
            self.logger.error(f"Error checking duplicate in queue: {e}")
            return False

    # ------------------------------------------------------------------
    # Activity log
    # ------------------------------------------------------------------

    def add_activity_log(self, type, message, book_id=None, chapter=None, book_name=None, entities=None):
        """Add an entry to the activity log. Returns the entry dict."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            created_at = datetime.datetime.now().isoformat()
            entities_json = json.dumps(entities) if entities else None
            cursor.execute(
                'INSERT INTO activity_log (type, message, book_id, chapter, book_name, entities_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (type, message, book_id, chapter, book_name, entities_json, created_at),
            )
            entry_id = cursor.lastrowid
            # Cap at 500 rows
            cursor.execute(self.backend.cap_activity_log_sql())
            conn.commit()
            conn.close()
            return {
                'id': entry_id, 'type': type, 'message': message,
                'book_id': book_id, 'chapter': chapter, 'book_name': book_name,
                'entities': entities, 'created_at': created_at,
            }
        except Exception as e:
            self.logger.error(f"Error adding activity log: {e}")
            return None

    def get_activity_log(self, limit=200):
        """Get recent activity log entries, oldest first."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT id, type, message, book_id, chapter, book_name, entities_json, created_at FROM activity_log ORDER BY id DESC LIMIT ?', (limit,))
            rows = cursor.fetchall()
            conn.close()
            entries = []
            for row in reversed(rows):  # reverse so oldest is first
                entries.append({
                    'id': row[0], 'type': row[1], 'message': row[2],
                    'book_id': row[3], 'chapter': row[4], 'book_name': row[5],
                    'entities': json.loads(row[6]) if row[6] else None,
                    'created_at': row[7],
                })
            return entries
        except Exception as e:
            self.logger.error(f"Error reading activity log: {e}")
            return []

    def clear_activity_log(self):
        """Delete all activity log entries."""
        try:
            conn = self.backend.get_connection()
            conn.execute('DELETE FROM activity_log')
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Error clearing activity log: {e}")

    # ------------------------------------------------------------------
    # Reader view log
    # ------------------------------------------------------------------

    def log_reader_view(self, book_id: int, chapter_number: int, ip: str):
        """Record a chapter view from the public reader."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO reader_log (book_id, chapter_number, ip, viewed_at) VALUES (?, ?, ?, ?)',
                (book_id, chapter_number, ip, datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Error logging reader view: {e}")

    def get_reader_log(self, book_id: int = None, limit: int = 200):
        """Return recent reader log entries, optionally filtered by book."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            if book_id is not None:
                cursor.execute(
                    'SELECT id, book_id, chapter_number, ip, viewed_at FROM reader_log WHERE book_id = ? ORDER BY id DESC LIMIT ?',
                    (book_id, limit)
                )
            else:
                cursor.execute(
                    'SELECT id, book_id, chapter_number, ip, viewed_at FROM reader_log ORDER BY id DESC LIMIT ?',
                    (limit,)
                )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            self.logger.error(f"Error reading reader log: {e}")
            return []

    def _check_legacy_queue(self):
        """Check for legacy queue.json and warn user"""
        queue_path = os.path.join(self.config.script_dir, "queue.json")
        if os.path.exists(queue_path):
            try:
                with open(queue_path, 'r', encoding='utf-8') as f:
                    legacy_queue = json.load(f)

                if legacy_queue and len(legacy_queue) > 0:
                    self.logger.warning(f"Found legacy queue.json with {len(legacy_queue)} items")
                    print("\n" + "="*60)
                    print("WARNING: Legacy queue.json detected")
                    print("="*60)
                    print(f"Found {len(legacy_queue)} items in old queue.json.")
                    print("The queue system now uses the database.")
                    print("\nYour old queue.json will NOT be processed automatically.")
                    print("\nOptions:")
                    print("  1. Process old queue first:")
                    print("     python translator.py --resume  (repeat until empty)")
                    print("  2. Clear old queue:")
                    print("     rm queue.json")
                    print("  3. Ignore - items will not be processed")
                    print("="*60 + "\n")
            except Exception as e:
                self.logger.debug(f"Error checking legacy queue: {e}")
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
            conn = self.backend.get_connection()
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
            
            conn.close()
            self.entities = entities
            self.logger.debug(f"Loaded {sum(len(cat) for cat in entities.values())} entities from database")
            return entities
                
        except Exception as e:
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
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            
            # Track which entities we've already saved to avoid duplicates
            processed_entities = set()
            
            # For each category and entity in memory cache
            for category, entities in self.entities.items():
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
            
            conn.commit()
            conn.close()
            self.logger.info("Entities saved to database successfully")
        except Exception as e:
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
            self.logger.error("all_entities is empty, querying database... we will just return a blank dict for now")
            return {}
        else:
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
                        if value.get("note"):
                            found_entities[key]["note"] = value["note"]
                    
                    # Update global entities
                    all_entities[key]["last_chapter"] = current_chapter
        return found_entities
    
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
            conn = self.backend.get_connection()
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
            if book_id:
                entity_data["book_id"] = book_id
            if note:
                entity_data["note"] = note
                    
            self.entities[category][untranslated] = entity_data
            return True
                
        except Exception as e:
            self.logger.error(f"Error adding entity to database: {e}")
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
            conn = self.backend.get_connection()
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
                conn.close()
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
                conn.close()
                return False
            
            conn.commit()
            conn.close()

            # Update the in-memory cache
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
            self.logger.error(f"Error updating entity in database: {e}")
            return False
    
    def delete_entity(self, category, untranslated):
        """
        Delete an entity from the database.
        Returns True if the entity was deleted, False if it wasn't found.
        """
        try:
            conn = self.backend.get_connection()
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
            
        except Exception as e:
            self.logger.error(f"Error deleting entity from database: {e}")
            return False
    
    def change_entity_category(self, old_category, untranslated, new_category):
        """
        Move an entity from one category to another.
        Returns True if the entity was moved, False otherwise.
        """
        try:
            conn = self.backend.get_connection()
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
            
        except Exception as e:
            self.logger.error(f"Error changing entity category in database: {e}")
            return False
    
    def get_entity_by_translation(self, translation):
        """
        Find an entity by its translation.
        Returns a tuple (category, untranslated, entity_data) if found, None otherwise.
        
        This is useful for finding duplicates by translation rather than by untranslated text.
        """
        try:
            conn = self.backend.get_connection()
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
            
            conn = self.backend.get_connection()
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
            
            conn.commit()
            conn.close()
            self.logger.info(f"Imported {count} entities from JSON file '{filepath}'")
            
            # Refresh the in-memory cache
            self._load_entities()
            return True
            
        except Exception as e:
            self.logger.error(f"Error importing entities from JSON: {e}")
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
            conn = self.backend.get_connection()
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
            conn.close()

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
            conn = self.backend.get_connection()
            cursor = conn.cursor()

            if book_id:
                cursor.execute(
                    'SELECT total_input_chars, total_output_tokens FROM token_ratios WHERE book_id = ?',
                    (book_id,)
                )
                row = cursor.fetchone()
                if row and row[0] > 0:
                    conn.close()
                    return row[1] / row[0]

            # Fall back to global row
            cursor.execute(
                'SELECT total_input_chars, total_output_tokens FROM token_ratios WHERE book_id = 0'
            )
            row = cursor.fetchone()
            conn.close()

            if row and row[0] > 0:
                return row[1] / row[0]

            return 1.2
        except Exception as e:
            self.logger.warning(f"Could not load token ratio: {e}")
            return 1.2

    def update_token_ratio(self, book_id, input_chars, output_tokens):
        """Add a chapter's char/token counts to the running totals for book and global stats."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()

            upsert_sql = self.backend.upsert_token_ratio_sql()

            if book_id:
                cursor.execute(upsert_sql, (book_id, input_chars, output_tokens))

            # Always update global aggregate
            cursor.execute(upsert_sql, (0, input_chars, output_tokens))

            conn.commit()

            # Log updated stats
            lookup_id = book_id if book_id else 0
            cursor.execute(
                'SELECT total_input_chars, total_output_tokens, sample_count FROM token_ratios WHERE book_id = ?',
                (lookup_id,)
            )
            row = cursor.fetchone()
            conn.close()

            if row and row[0] > 0:
                avg = row[1] / row[0]
                self.logger.info(f"Updated token ratio - average: {avg:.2f} over {row[2]} chapter(s)")
        except Exception as e:
            self.logger.error(f"Failed to update token ratio: {e}")

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
            conn = self.backend.get_connection()
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
            conn.close()

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

    def get_wp_state(self, book_id, chapter_number=None):
        """Get a single wp_publish_state record."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            if chapter_number is None:
                cursor.execute(
                    "SELECT id, book_id, chapter_number, wp_post_id, wp_post_type, last_published, content_hash "
                    "FROM wp_publish_state WHERE book_id = ? AND chapter_number IS NULL",
                    (book_id,),
                )
            else:
                cursor.execute(
                    "SELECT id, book_id, chapter_number, wp_post_id, wp_post_type, last_published, content_hash "
                    "FROM wp_publish_state WHERE book_id = ? AND chapter_number = ?",
                    (book_id, chapter_number),
                )
            row = cursor.fetchone()
            conn.close()
            if not row:
                return None
            return {
                "id": row[0], "book_id": row[1], "chapter_number": row[2],
                "wp_post_id": row[3], "wp_post_type": row[4],
                "last_published": row[5], "content_hash": row[6],
            }
        except Exception as e:
            self.logger.error(f"Error getting wp state: {e}")
            return None

    def save_wp_state(self, book_id, chapter_number, wp_post_id, wp_post_type, content_hash):
        """Upsert a wp_publish_state record."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            now = datetime.datetime.utcnow().isoformat()
            cursor.execute(self.backend.upsert_wp_state_sql(),
                (book_id, chapter_number, wp_post_id, wp_post_type, now, content_hash))
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Error saving wp state: {e}")

    def get_all_wp_states(self, book_id):
        """Get all wp_publish_state records for a book."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, book_id, chapter_number, wp_post_id, wp_post_type, last_published, content_hash "
                "FROM wp_publish_state WHERE book_id = ?",
                (book_id,),
            )
            rows = cursor.fetchall()
            conn.close()
            return [
                {
                    "id": r[0], "book_id": r[1], "chapter_number": r[2],
                    "wp_post_id": r[3], "wp_post_type": r[4],
                    "last_published": r[5], "content_hash": r[6],
                }
                for r in rows
            ]
        except Exception as e:
            self.logger.error(f"Error getting all wp states: {e}")
            return []

    def delete_wp_states(self, book_id):
        """Delete all wp_publish_state records for a book."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM wp_publish_state WHERE book_id = ?", (book_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Error deleting wp states: {e}")

    def delete_wp_state_single(self, book_id, chapter_number=None):
        """Delete a single wp_publish_state record."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            if chapter_number is None:
                cursor.execute(
                    "DELETE FROM wp_publish_state WHERE book_id = ? AND chapter_number IS NULL",
                    (book_id,),
                )
            else:
                cursor.execute(
                    "DELETE FROM wp_publish_state WHERE book_id = ? AND chapter_number = ?",
                    (book_id, chapter_number),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Error deleting wp state: {e}")

    # ------------------------------------------------------------------
    # API call logging
    # ------------------------------------------------------------------

    def log_api_call(self, session_id, book_id, chapter_number, chunk_index,
                     total_chunks, system_prompt, user_prompt, response_text,
                     model_name, provider, prompt_tokens=0, completion_tokens=0,
                     total_tokens=0, duration_ms=0, success=1, attempt=0):
        """Log an LLM API call. Returns the row id or None on failure."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            created_at = datetime.datetime.now().isoformat()
            cursor.execute(
                'INSERT INTO api_calls (session_id, book_id, chapter_number, chunk_index, '
                'total_chunks, system_prompt, user_prompt, response_text, model_name, provider, '
                'prompt_tokens, completion_tokens, total_tokens, duration_ms, success, attempt, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (session_id, book_id, chapter_number, chunk_index, total_chunks,
                 system_prompt, user_prompt, response_text, model_name, provider,
                 prompt_tokens, completion_tokens, total_tokens, duration_ms,
                 success, attempt, created_at),
            )
            row_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return row_id
        except Exception as e:
            self.logger.error(f"Error logging API call: {e}")
            return None

    def get_all_api_calls(self, book_id=None, limit=500):
        """Get API call logs across all books, optionally filtered by book_id."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            if book_id is not None:
                cursor.execute(
                    'SELECT ac.id, ac.session_id, ac.book_id, ac.chapter_number, ac.chunk_index, ac.total_chunks, '
                    'ac.system_prompt, ac.user_prompt, ac.response_text, ac.model_name, ac.provider, '
                    'ac.prompt_tokens, ac.completion_tokens, ac.total_tokens, ac.duration_ms, ac.success, ac.attempt, ac.created_at, '
                    'b.title as book_title '
                    'FROM api_calls ac LEFT JOIN books b ON ac.book_id = b.id '
                    'WHERE ac.book_id = ? '
                    'ORDER BY ac.created_at DESC, ac.chunk_index ASC, ac.attempt ASC LIMIT ?',
                    (book_id, limit),
                )
            else:
                cursor.execute(
                    'SELECT ac.id, ac.session_id, ac.book_id, ac.chapter_number, ac.chunk_index, ac.total_chunks, '
                    'ac.system_prompt, ac.user_prompt, ac.response_text, ac.model_name, ac.provider, '
                    'ac.prompt_tokens, ac.completion_tokens, ac.total_tokens, ac.duration_ms, ac.success, ac.attempt, ac.created_at, '
                    'b.title as book_title '
                    'FROM api_calls ac LEFT JOIN books b ON ac.book_id = b.id '
                    'ORDER BY ac.created_at DESC, ac.chunk_index ASC, ac.attempt ASC LIMIT ?',
                    (limit,),
                )
            rows = cursor.fetchall()
            conn.close()
            return [
                {
                    'id': r[0], 'session_id': r[1], 'book_id': r[2],
                    'chapter_number': r[3], 'chunk_index': r[4], 'total_chunks': r[5],
                    'system_prompt': r[6], 'user_prompt': r[7], 'response_text': r[8],
                    'model_name': r[9], 'provider': r[10],
                    'prompt_tokens': r[11], 'completion_tokens': r[12], 'total_tokens': r[13],
                    'duration_ms': r[14], 'success': r[15], 'attempt': r[16],
                    'created_at': r[17], 'book_title': r[18],
                }
                for r in rows
            ]
        except Exception as e:
            self.logger.error(f"Error getting all API calls: {e}")
            return []

    def get_api_calls(self, book_id, chapter_number=None, limit=500):
        """Get API call logs for a book, optionally filtered by chapter number."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            if chapter_number is not None:
                cursor.execute(
                    'SELECT id, session_id, book_id, chapter_number, chunk_index, total_chunks, '
                    'system_prompt, user_prompt, response_text, model_name, provider, '
                    'prompt_tokens, completion_tokens, total_tokens, duration_ms, success, attempt, created_at '
                    'FROM api_calls WHERE book_id = ? AND chapter_number = ? '
                    'ORDER BY created_at DESC, chunk_index ASC, attempt ASC LIMIT ?',
                    (book_id, chapter_number, limit),
                )
            else:
                cursor.execute(
                    'SELECT id, session_id, book_id, chapter_number, chunk_index, total_chunks, '
                    'system_prompt, user_prompt, response_text, model_name, provider, '
                    'prompt_tokens, completion_tokens, total_tokens, duration_ms, success, attempt, created_at '
                    'FROM api_calls WHERE book_id = ? '
                    'ORDER BY created_at DESC, chunk_index ASC, attempt ASC LIMIT ?',
                    (book_id, limit),
                )
            rows = cursor.fetchall()
            conn.close()
            return [
                {
                    'id': r[0], 'session_id': r[1], 'book_id': r[2],
                    'chapter_number': r[3], 'chunk_index': r[4], 'total_chunks': r[5],
                    'system_prompt': r[6], 'user_prompt': r[7], 'response_text': r[8],
                    'model_name': r[9], 'provider': r[10],
                    'prompt_tokens': r[11], 'completion_tokens': r[12], 'total_tokens': r[13],
                    'duration_ms': r[14], 'success': r[15], 'attempt': r[16],
                    'created_at': r[17],
                }
                for r in rows
            ]
        except Exception as e:
            self.logger.error(f"Error getting API calls: {e}")
            return []

    def get_api_call(self, call_id):
        """Get a single API call log entry by id."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id, session_id, book_id, chapter_number, chunk_index, total_chunks, '
                'system_prompt, user_prompt, response_text, model_name, provider, '
                'prompt_tokens, completion_tokens, total_tokens, duration_ms, success, attempt, created_at '
                'FROM api_calls WHERE id = ?',
                (call_id,),
            )
            r = cursor.fetchone()
            conn.close()
            if not r:
                return None
            return {
                'id': r[0], 'session_id': r[1], 'book_id': r[2],
                'chapter_number': r[3], 'chunk_index': r[4], 'total_chunks': r[5],
                'system_prompt': r[6], 'user_prompt': r[7], 'response_text': r[8],
                'model_name': r[9], 'provider': r[10],
                'prompt_tokens': r[11], 'completion_tokens': r[12], 'total_tokens': r[13],
                'duration_ms': r[14], 'success': r[15], 'attempt': r[16],
                'created_at': r[17],
            }
        except Exception as e:
            self.logger.error(f"Error getting API call: {e}")
            return None

    def update_api_call_response(self, call_id, response_text):
        """Update the response_text of an API call log entry."""
        try:
            conn = self.backend.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE api_calls SET response_text = ? WHERE id = ?',
                (response_text, call_id),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            self.logger.error(f"Error updating API call response: {e}")
            return False
