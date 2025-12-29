import json
import unicodedata
import sqlite3
import os
import datetime
from typing import Dict, List, Optional, Any, Union, Tuple
from itertools import zip_longest
import re

class DatabaseManager:
    """Class to manage database operations including entities, books, and chapters using SQLite"""
    
    def __init__(self, config: 'TranslationConfig', logger: 'Logger'):
        self.config = config
        self.logger = logger
        self.db_path = os.path.join(self.config.script_dir, "database.db")
        self.entities = {}  # Cached entities
        self._initialize_database()
        self._load_entities()
        self._check_legacy_queue()
    
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
                book_id INTEGER,
                UNIQUE(category, untranslated, book_id)
            )
            ''')
        
            
            # Create indices for faster lookups
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON entities(category)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_untranslated ON entities(untranslated)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_book_id ON entities(book_id)')
            
            # Create books table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT,
                language TEXT DEFAULT 'en',
                description TEXT,
                created_date TEXT,
                modified_date TEXT,
                prompt_template TEXT,
                source_language TEXT DEFAULT 'zh',
                target_language TEXT DEFAULT 'en',
                UNIQUE(title)
            )
            ''')
            
            # Create chapters table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                chapter_number INTEGER NOT NULL,
                title TEXT NOT NULL,
                untranslated_content TEXT NOT NULL,
                translated_content TEXT NOT NULL,
                summary TEXT,
                translation_date TEXT,
                translation_model TEXT,
                UNIQUE(book_id, chapter_number),
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            )
            ''')
            
            # Create indices for chapters table
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chapters_book_id ON chapters(book_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chapter_number ON chapters(chapter_number)')

            # Create queue table
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                chapter_number INTEGER,
                title TEXT NOT NULL,
                source TEXT,
                content TEXT NOT NULL,
                metadata TEXT,
                position INTEGER NOT NULL,
                created_date TEXT NOT NULL,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            )
            ''')

            # Create indices for queue table
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_queue_book_id ON queue(book_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_queue_position ON queue(position)')
            cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_position_unique ON queue(position)')

            conn.commit()
            conn.close()
            self.logger.info("Database initialized successfully")
        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
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
            
        except sqlite3.Error as e:
            self.logger.error(f"Error creating book: {e}")
            return None
    

    def get_book_prompt_template(self, book_id):
        """
        Get the prompt template for a specific book.
        Returns None if no custom template is set.
        """
        try:
            conn = sqlite3.connect(self.db_path)
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
        except sqlite3.Error as e:
            self.logger.error(f"Error retrieving book prompt template: {e}")
            return None

    def set_book_prompt_template(self, book_id, prompt_template):
        """
        Set the prompt template for a specific book.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            UPDATE books
            SET prompt_template = ?
            WHERE id = ?
            ''', (prompt_template, book_id))
            
            conn.commit()
            conn.close()
            
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Error setting book prompt template: {e}")
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if book_id:
                cursor.execute('''
                SELECT id, title, author, language, description, created_date, modified_date, 
                    source_language, target_language
                FROM books
                WHERE id = ?
                ''', (book_id,))
            else:
                cursor.execute('''
                SELECT id, title, author, language, description, created_date, modified_date, 
                    source_language, target_language
                FROM books
                WHERE title = ?
                ''', (title,))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
                
            book_info = {
                "id": row[0],
                "title": row[1],
                "author": row[2],
                "language": row[3],
                "description": row[4],
                "created_date": row[5],
                "modified_date": row[6],
                "source_language": row[7],
                "target_language": row[8]
            }
            
            return book_info
            
        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
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
                        'target_language', 'modified_date']:
                    set_clause.append(f"{key} = ?")
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
            
            self.logger.info(f"Updated book with ID {book_id}")
            return True
            
        except sqlite3.Error as e:
            self.logger.error(f"Error updating book: {e}")
            return False

    def list_books(self):
        """
        List all books in the database.
        
        Returns:
            list: List of book information dictionaries
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT id, title, author, language, created_date, 
                (SELECT COUNT(*) FROM chapters WHERE book_id = books.id) as chapter_count
            FROM books
            ORDER BY title
            ''')
            
            rows = cursor.fetchall()
            conn.close()
            
            result = []
            for row in rows:
                book_id, title, author, language, created_date, chapter_count = row
                result.append({
                    "id": book_id,
                    "title": title,
                    "author": author,
                    "language": language,
                    "created_date": created_date,
                    "chapter_count": chapter_count
                })
            
            return result
            
        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
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
            cursor.execute("PRAGMA foreign_keys = ON")
            
            # Delete book (will cascade to chapters)
            cursor.execute("DELETE FROM books WHERE id = ?", (book_id,))
            
            # Also delete book-specific entities
            cursor.execute("DELETE FROM entities WHERE book_id = ?", (book_id,))
            
            conn.commit()
            conn.close()
            
            self.logger.info(f"Deleted book '{book_title}' (ID: {book_id}) and all its chapters")
            return True
            
        except sqlite3.Error as e:
            self.logger.error(f"Error deleting book: {e}")
            return False
        
    # Private Book methods

    
    # End Book management section    
    
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
                
            conn = sqlite3.connect(self.db_path)
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
            
            return chapter_id
            
        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if chapter_id:
                cursor.execute('''
                SELECT c.id, c.book_id, c.chapter_number, c.title, c.untranslated_content, 
                    c.translated_content, c.summary, c.translation_date, c.translation_model,
                    b.title as book_title
                FROM chapters c
                JOIN books b ON c.book_id = b.id
                WHERE c.id = ?
                ''', (chapter_id,))
            else:
                cursor.execute('''
                SELECT c.id, c.book_id, c.chapter_number, c.title, c.untranslated_content, 
                    c.translated_content, c.summary, c.translation_date, c.translation_model,
                    b.title as book_title
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
                "book_title": row[9]
            }
            
            return chapter_data
            
        except sqlite3.Error as e:
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
                
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT id, chapter_number, title, translation_date, translation_model
            FROM chapters
            WHERE book_id = ?
            ORDER BY chapter_number
            ''', (book_id,))
            
            rows = cursor.fetchall()
            conn.close()
            
            result = []
            for row in rows:
                chapter_id, chapter_number, title, translation_date, model = row
                result.append({
                    "id": chapter_id,
                    "chapter": chapter_number,
                    "title": title,
                    "translation_date": translation_date,
                    "model": model
                })
            
            return result
            
        except sqlite3.Error as e:
            self.logger.error(f"Error listing chapters: {e}")
            return []

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
            conn = sqlite3.connect(self.db_path)
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
            
            if chapter_id:
                self.logger.info(f"Deleted chapter {chapter[1]}: '{chapter[2]}' from book ID {chapter[0]}")
            else:
                self.logger.info(f"Deleted chapter {chapter_number} (ID: {chapter[0]}): '{chapter[1]}' from book ID {book_id}")
                
            return True
            
        except sqlite3.Error as e:
            self.logger.error(f"Error deleting chapter: {e}")
            return False

    # Queue management section
    def add_to_queue(self, book_id, content, title=None, chapter_number=None, source=None, metadata=None):
        """
        Add an item to the translation queue.

        Args:
            book_id: Book ID (required, NOT NULL)
            content: List of content lines or string
            title: Chapter title (optional)
            chapter_number: Chapter number (optional)
            source: Source file path or description (optional)
            metadata: Additional metadata dict (optional)

        Returns:
            int: Queue item ID if successful, None otherwise
        """
        try:
            # Verify book exists
            book = self.get_book(book_id=book_id)
            if not book:
                self.logger.error(f"Book with ID {book_id} not found")
                return None

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get max position and add 1 for FIFO ordering
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

            # Insert queue item
            cursor.execute('''
            INSERT INTO queue (book_id, chapter_number, title, source, content, metadata, position, created_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (book_id, chapter_number, title or "Untitled", source, content_json, metadata_json, next_position, created_date))

            queue_id = cursor.lastrowid
            conn.commit()
            conn.close()

            self.logger.info(f"Added item to queue (ID: {queue_id}, position: {next_position}) for book '{book['title']}'")
            return queue_id

        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Build query with optional book_id filter
            if book_id:
                cursor.execute('''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE q.book_id = ?
                ORDER BY q.position ASC
                LIMIT 1
                ''', (book_id,))
            else:
                cursor.execute('''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title
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
                'book_title': row[9]
            }

        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get the position of the item being removed
            cursor.execute('SELECT position FROM queue WHERE id = ?', (queue_id,))
            row = cursor.fetchone()

            if not row:
                self.logger.warning(f"Queue item {queue_id} not found")
                conn.close()
                return False

            removed_position = row[0]

            # Delete the item
            cursor.execute('DELETE FROM queue WHERE id = ?', (queue_id,))

            # Update positions of all items with position > removed_position (decrement by 1)
            cursor.execute('UPDATE queue SET position = position - 1 WHERE position > ?', (removed_position,))

            conn.commit()
            conn.close()

            self.logger.info(f"Removed queue item {queue_id} from position {removed_position}")
            return True

        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Build query with optional book_id filter
            if book_id:
                cursor.execute('''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE q.book_id = ?
                ORDER BY q.position ASC
                ''', (book_id,))
            else:
                cursor.execute('''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title
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
                    'book_title': row[9]
                })

            return result

        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
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

        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            if book_id:
                cursor.execute('SELECT COUNT(*) FROM queue WHERE book_id = ?', (book_id,))
            else:
                cursor.execute('SELECT COUNT(*) FROM queue')

            count = cursor.fetchone()[0]
            conn.close()

            return count

        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('SELECT id FROM queue WHERE book_id = ? AND chapter_number = ?',
                          (book_id, chapter_number))

            result = cursor.fetchone()
            conn.close()

            return result is not None

        except sqlite3.Error as e:
            self.logger.error(f"Error checking duplicate in queue: {e}")
            return False

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

        # Define default entity categories
        default_entities = {
            "characters": {},
            "places": {},
            "organizations": {},
            "abilities": {},
            "titles": {},
            "equipment": {},
            "creatures": {}
        }
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get all entities grouped by category
            if book_id is not None:
                cursor.execute('''
                SELECT category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id
                FROM entities
                WHERE book_id = ? OR book_id IS NULL
                ''', (book_id,))
            else:
                cursor.execute('''
                SELECT category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id
                FROM entities
                ''')
                
            rows = cursor.fetchall()
            
            # Process results
            entities = default_entities.copy()
            for row in rows:
                category, untranslated, translation, last_chapter, incorrect_translation, gender, entity_book_id = row
                
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
                 for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']}

        # Update with new entities
        for category in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']:
            new_category_dict = new_entities.get(category, {})
            result[category].update(new_category_dict)
        
        return result
    
    def save_entities(self):
        """Save the current entities cache to the SQLite database"""
        try:
            conn = sqlite3.connect(self.db_path)
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
                    
                    # Create a unique key to track this entity
                    entity_key = (category, untranslated, book_id)
                    
                    # Skip if we've already processed this entity
                    if entity_key in processed_entities:
                        continue
                    
                    # Add to processed set
                    processed_entities.add(entity_key)
                    
                    # Look for existing entity to determine whether to insert or update
                    if book_id is not None:
                        cursor.execute('''
                        SELECT id FROM entities 
                        WHERE category = ? AND untranslated = ? AND book_id = ?
                        ''', (category, untranslated, book_id))
                    else:
                        cursor.execute('''
                        SELECT id FROM entities 
                        WHERE category = ? AND untranslated = ? AND book_id IS NULL
                        ''', (category, untranslated))
                    
                    existing = cursor.fetchone()
                    
                    if existing:
                        # Update existing entity
                        entity_id = existing[0]
                        cursor.execute('''
                        UPDATE entities 
                        SET translation = ?, last_chapter = ?, incorrect_translation = ?, gender = ?
                        WHERE id = ?
                        ''', (translation, last_chapter, incorrect_translation, gender, entity_id))
                    else:
                        # Insert new entity
                        cursor.execute('''
                        INSERT INTO entities 
                        (category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (category, untranslated, translation, last_chapter, incorrect_translation, gender, book_id))
            
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
    
    def add_entity(self, category, untranslated, translation, book_id=None, last_chapter=None, incorrect_translation=None, gender=None):
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # First check if this entity exists in any other category for this book
            if book_id is not None:
                cursor.execute('''
                SELECT category FROM entities 
                WHERE untranslated = ? AND category != ? AND book_id = ?
                ''', (untranslated, category, book_id))
            else:
                cursor.execute('''
                SELECT category FROM entities 
                WHERE untranslated = ? AND category != ? AND book_id IS NULL
                ''', (untranslated, category))
            
            existing = cursor.fetchone()
            if existing:
                self.logger.warning(f"Entity '{untranslated}' already exists in category '{existing[0]}' for the same book, not adding to '{category}'")
                conn.close()
                return False
            
            # Add or update the entity
            cursor.execute('''
            INSERT OR REPLACE INTO entities 
            (category, untranslated, translation, book_id, last_chapter, incorrect_translation, gender)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (category, untranslated, translation, book_id, last_chapter, incorrect_translation, gender))
            
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
                    
            self.entities[category][untranslated] = entity_data
            return True
                
        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Check if book_id is the only field being updated (changing book assignment)
            is_only_book_id = 'book_id' in kwargs and len(kwargs) == 1

            # Build the SET clause dynamically based on provided kwargs
            set_clause = []
            values = []
            where_book_id = None

            for key, value in kwargs.items():
                if key in ['translation', 'last_chapter', 'incorrect_translation', 'gender']:
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
                for key, value in kwargs.items():
                    if key in ['translation', 'last_chapter', 'incorrect_translation', 'gender']:
                        self.entities[category][untranslated][key] = value
                    elif key == 'book_id':
                        if is_only_book_id:
                            # Changing book assignment
                            if value is None:
                                # Remove book_id from cache if setting to None (making global)
                                if 'book_id' in self.entities[category][untranslated]:
                                    del self.entities[category][untranslated]['book_id']
                            else:
                                self.entities[category][untranslated]['book_id'] = value
                        # If not is_only_book_id, book_id was used for WHERE clause, don't update cache

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
        # Define default entity categories
        default_entities = {
            "characters": {},
            "places": {},
            "organizations": {},
            "abilities": {},
            "titles": {},
            "equipment": {},
            "creatures": {}
        }

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Build SQL query with filters
            query = '''
                SELECT category, untranslated, translation, last_chapter,
                       incorrect_translation, gender, book_id
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
                cat, untranslated, translation, last_chapter, incorrect_translation, gender, entity_book_id = row

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

                # Add to our entities dictionary
                entities[cat][untranslated] = entity_data

            self.logger.debug(f"Loaded {sum(len(cat) for cat in entities.values())} entities for review")
            return entities

        except sqlite3.Error as e:
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
            conn = sqlite3.connect(self.db_path)
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

        except sqlite3.Error as e:
            self.logger.error(f"Error finding chapters using entity: {e}")
            return []
