import json
import datetime
import traceback
from modules import apply_source_ingest


class QueueRepo:
    """Translation queue management (translation_queue table)."""

    def shift_queue_chapter_numbers(self, book_id, from_chapter, delta=1, exclude_queue_id=None):
        """Bulk-shift queue.chapter_number for one book.

        Used by the chapter-conflict 'insert_shift' resolution to make room
        at `from_chapter` (typically existing_chapter_number + 1) for the
        incoming item. Returns rowcount, or -1 on error.
        """
        if not isinstance(delta, int) or delta == 0:
            return 0
        if not isinstance(from_chapter, int) or from_chapter < 1:
            return 0
        with self._conn() as conn:
            cursor = conn.cursor()
            try:
                if exclude_queue_id is not None:
                    cursor.execute(
                        "UPDATE queue SET chapter_number = chapter_number + ? "
                        "WHERE book_id = ? AND chapter_number IS NOT NULL "
                        "AND chapter_number >= ? AND id != ?",
                        (delta, book_id, from_chapter, exclude_queue_id),
                    )
                else:
                    cursor.execute(
                        "UPDATE queue SET chapter_number = chapter_number + ? "
                        "WHERE book_id = ? AND chapter_number IS NOT NULL "
                        "AND chapter_number >= ?",
                        (delta, book_id, from_chapter),
                    )
                affected = cursor.rowcount
            except Exception as e:
                conn.rollback()
                self.logger.error(
                    f"shift_queue_chapter_numbers failed (book={book_id}, "
                    f"from={from_chapter}, delta={delta}): {e}"
                )
                return -1
        return affected

    def update_queue_chapter_number(self, queue_id, new_chapter_number):
        """Update a single queue row's chapter_number. Returns True on success."""
        if not isinstance(new_chapter_number, int) or new_chapter_number < 1:
            return False
        with self._conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "UPDATE queue SET chapter_number = ? WHERE id = ?",
                    (new_chapter_number, queue_id),
                )
            except Exception as e:
                conn.rollback()
                self.logger.error(
                    f"update_queue_chapter_number failed (queue_id={queue_id}, "
                    f"new={new_chapter_number}): {e}"
                )
                return False
        return True

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

            # Per-book module source transforms (trad→simp, novel543 boilerplate strip, …)
            content = apply_source_ingest(book, content, self.config, self.logger, db=self,
                                          chapter_number=chapter_number)
            with self._conn() as conn:
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

            self.logger.info(f"Added item to queue (ID: {queue_id}, position: {next_position}) for book '{book['title']}'")
            return queue_id

        except Exception as e:
            self.logger.error(f"Error adding to queue: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
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
            with self._conn() as conn:
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

            if not row:
                return None

            # Deserialize content (like get_chapter)
            content_json = row[5]
            try:
                content = json.loads(content_json)
            except (json.JSONDecodeError, TypeError):
                content = content_json  # Fallback to string if not valid JSON

            # Deserialize metadata if present
            metadata_json = row[6]
            metadata = None
            if metadata_json:
                try:
                    metadata = json.loads(metadata_json)
                except (json.JSONDecodeError, TypeError):
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
            with self._conn() as conn:
                cursor = conn.cursor()

                # Get the position of the item being removed
                cursor.execute('SELECT position FROM queue WHERE id = ?', (queue_id,))
                row = cursor.fetchone()

                if not row:
                    self.logger.warning(f"Queue item {queue_id} not found")
                    return False

                removed_position = row[0]

                # Delete the item (no need to reorder — gaps in position are fine)
                cursor.execute('DELETE FROM queue WHERE id = ?', (queue_id,))

            self.logger.info(f"Removed queue item {queue_id} from position {removed_position}")
            return True

        except Exception as e:
            self.logger.error(f"Error removing from queue: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def list_queue(self, book_id=None, include_content=False):
        """
        List all items in the queue.

        Args:
            book_id: Optional book ID to filter by specific book
            include_content: When False (default), the large per-item content
                and metadata columns are not fetched or decoded — they are
                returned as None. List/UI callers never use them; fetching them
                made the queue page download every queued chapter's full text.
                Pass True only when the actual chapter content is needed.

        Returns:
            list: List of queue item dicts ordered by position
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                content_cols = "q.content, q.metadata," if include_content else ""

                # Build query with optional book_id filter
                if book_id:
                    cursor.execute(f'''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, {content_cols}
                       q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE q.book_id = ?
                ORDER BY q.position ASC
                ''', (book_id,))
                else:
                    cursor.execute(f'''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, {content_cols}
                       q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                ORDER BY q.position ASC
                ''')

                rows = cursor.fetchall()

            result = []
            for row in rows:
                if include_content:
                    # Deserialize content
                    content_json = row[5]
                    try:
                        content = json.loads(content_json)
                    except (json.JSONDecodeError, TypeError):
                        content = content_json

                    # Deserialize metadata if present
                    metadata_json = row[6]
                    metadata = None
                    if metadata_json:
                        try:
                            metadata = json.loads(metadata_json)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    tail = row[7:]
                else:
                    content = None
                    metadata = None
                    tail = row[5:]

                result.append({
                    'id': row[0],
                    'book_id': row[1],
                    'chapter_number': row[2],
                    'title': row[3],
                    'source': row[4],
                    'content': content,
                    'metadata': metadata,
                    'position': tail[0],
                    'created_date': tail[1],
                    'book_title': tail[2],
                    'retranslation_reason': tail[3],
                })

            return result

        except Exception as e:
            self.logger.error(f"Error listing queue: {e}")
            return []

    def get_queued_book_ids(self):
        """
        Return the distinct book IDs that currently have queued items.

        Cheap index-friendly scan used to populate the queue page's book
        filter without re-fetching the entire (content-laden) queue.

        Returns:
            list[int]: Distinct book IDs present in the queue.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT DISTINCT book_id FROM queue')
                rows = cursor.fetchall()
            return [row[0] for row in rows]
        except Exception as e:
            self.logger.error(f"Error getting queued book ids: {e}")
            return []

    def clear_queue(self, book_id=None):
        """
        Clear the queue (all items or for specific book).

        Args:
            book_id: Optional book ID to clear queue for specific book only

        Returns:
            int: Number of items removed

        Raises:
            Exception: re-raises any database error so callers can surface it
            (the API turns this into a 500 instead of falsely reporting success).
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                if book_id:
                    # Clear queue for specific book. Leave the remaining items'
                    # positions untouched — gaps are fine (same as remove_from_queue),
                    # and a sequential 0..n reassignment would collide with the
                    # UNIQUE constraint on queue.position mid-loop, raising a
                    # duplicate-key error that rolls back the DELETE.
                    cursor.execute('DELETE FROM queue WHERE book_id = ?', (book_id,))
                    count = cursor.rowcount
                else:
                    # Clear entire queue
                    cursor.execute('DELETE FROM queue')
                    count = cursor.rowcount

            self.logger.info(f"Cleared {count} items from queue" + (f" for book_id {book_id}" if book_id else ""))
            return count

        except Exception as e:
            self.logger.error(f"Error clearing queue: {e}")
            raise

    def get_queue_count(self, book_id=None):
        """
        Get count of items in queue.

        Args:
            book_id: Optional book ID to count for specific book

        Returns:
            int: Number of items in queue
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                if book_id:
                    cursor.execute('SELECT COUNT(*) FROM queue WHERE book_id = ?', (book_id,))
                else:
                    cursor.execute('SELECT COUNT(*) FROM queue')

                count = cursor.fetchone()[0]

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
            with self._conn() as conn:
                cursor = conn.cursor()

                cursor.execute('SELECT id FROM queue WHERE book_id = ? AND chapter_number = ?',
                              (book_id, chapter_number))

                result = cursor.fetchone()

            return result is not None

        except Exception as e:
            self.logger.error(f"Error checking duplicate in queue: {e}")
            return False
