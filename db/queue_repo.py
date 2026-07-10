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

                # Insert queue item. The position is computed inside the INSERT
                # (single statement) — a separate SELECT MAX + INSERT raced under
                # concurrency and the loser's row violated the UNIQUE(position)
                # constraint (silently swallowed in non-strict mode).
                if priority:
                    pos_select = "SELECT COALESCE(MIN(position), 1) - 1 FROM queue"
                else:
                    pos_select = "SELECT COALESCE(MAX(position), -1) + 1 FROM queue"
                cursor.execute(f'''
            INSERT INTO queue (book_id, chapter_number, title, source, content, metadata, position, created_date, retranslation_reason)
            SELECT ?, ?, ?, ?, ?, ?, ({pos_select}), ?, ?
            ''', (book_id, chapter_number, title or "Untitled", source, content_json, metadata_json, created_date, reason))

                queue_id = cursor.lastrowid
                cursor.execute("SELECT position FROM queue WHERE id = ?", (queue_id,))
                row = cursor.fetchone()
                next_position = row[0] if row else None

            self.logger.info(f"Added item to queue (ID: {queue_id}, position: {next_position}) for book '{book['title']}'")
            return queue_id

        except Exception as e:
            self.logger.error(f"Error adding to queue: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return None

    # Only "queued" rows are available for claim/list/count. "processing"
    # means another worker has claimed the row until it finishes or releases.
    _QUEUED_STATUS = "(q.status IS NULL OR q.status = 'queued')"

    @staticmethod
    def _row_to_queue_item(row):
        content_json = row[5]
        try:
            content = json.loads(content_json)
        except (json.JSONDecodeError, TypeError):
            content = content_json

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

    def get_next_queue_item(self, book_id=None):
        """
        Peek at the next queued item (lowest position) without claiming it.

        Prefer claim_next_queue_item() before starting work so concurrent
        consumers cannot take the same row.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                if book_id:
                    cursor.execute(f'''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE q.book_id = ? AND {self._QUEUED_STATUS}
                ORDER BY q.position ASC
                LIMIT 1
                ''', (book_id,))
                else:
                    cursor.execute(f'''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE {self._QUEUED_STATUS}
                ORDER BY q.position ASC
                LIMIT 1
                ''')
                row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_queue_item(row)
        except Exception as e:
            self.logger.error(f"Error getting next queue item: {e}")
            return None

    def claim_next_queue_item(self, book_id=None, worker_id=None):
        """
        Atomically claim the next queued item for processing.

        Sets status='processing' in the same transaction as the SELECT so two
        workers (web + CLI, or two processes) cannot both take the same row.
        On success the caller must either remove_from_queue (done) or
        release_queue_item (failure/cancel). Returns None if empty.
        """
        now = datetime.datetime.now().isoformat()
        worker = (worker_id or "worker")[:64]
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                # Immediate lock for SQLite writers; MySQL relies on
                # InnoDB row locks from the UPDATE … WHERE status.
                if getattr(self.backend, "name", None) == "sqlite":
                    try:
                        cursor.execute("BEGIN IMMEDIATE")
                    except Exception:
                        pass

                if book_id:
                    cursor.execute(f'''
                SELECT q.id FROM queue q
                WHERE q.book_id = ? AND {self._QUEUED_STATUS}
                ORDER BY q.position ASC
                LIMIT 1
                ''', (book_id,))
                else:
                    cursor.execute(f'''
                SELECT q.id FROM queue q
                WHERE {self._QUEUED_STATUS}
                ORDER BY q.position ASC
                LIMIT 1
                ''')
                row = cursor.fetchone()
                if not row:
                    return None
                qid = row[0]

                cursor.execute(
                    "UPDATE queue SET status = 'processing', claimed_at = ?, claimed_by = ? "
                    "WHERE id = ? AND (status IS NULL OR status = 'queued')",
                    (now, worker, qid),
                )
                if cursor.rowcount != 1:
                    # Lost the race to another claimer.
                    return None

                cursor.execute('''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, q.content,
                       q.metadata, q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE q.id = ?
                ''', (qid,))
                full = cursor.fetchone()
            if not full:
                return None
            return self._row_to_queue_item(full)
        except Exception as e:
            self.logger.error(f"Error claiming next queue item: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return None

    def release_queue_item(self, queue_id):
        """Return a claimed item to the queue (status=queued) after cancel/error."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE queue SET status = 'queued', claimed_at = NULL, claimed_by = NULL "
                    "WHERE id = ? AND status = 'processing'",
                    (queue_id,),
                )
                return cursor.rowcount > 0
        except Exception as e:
            self.logger.error(f"Error releasing queue item {queue_id}: {e}")
            if self.strict_writes:
                raise
            return False

    def release_stale_queue_claims(self, max_age_hours=6):
        """Re-queue items stuck in 'processing' longer than max_age_hours.

        Called on app start so a crashed worker doesn't leave chapters stranded.
        """
        try:
            cutoff = (
                datetime.datetime.now() - datetime.timedelta(hours=max_age_hours)
            ).isoformat()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE queue SET status = 'queued', claimed_at = NULL, claimed_by = NULL "
                    "WHERE status = 'processing' AND (claimed_at IS NULL OR claimed_at < ?)",
                    (cutoff,),
                )
                n = cursor.rowcount
            if n:
                self.logger.info(f"Released {n} stale queue claim(s)")
            return n
        except Exception as e:
            self.logger.error(f"Error releasing stale queue claims: {e}")
            return 0

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

                # Only list claimable rows — in-flight claims stay hidden so
                # the admin UI doesn't offer cancel on something mid-translate.
                if book_id:
                    cursor.execute(f'''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, {content_cols}
                       q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE q.book_id = ? AND {self._QUEUED_STATUS}
                ORDER BY q.position ASC
                ''', (book_id,))
                else:
                    cursor.execute(f'''
                SELECT q.id, q.book_id, q.chapter_number, q.title, q.source, {content_cols}
                       q.position, q.created_date, b.title as book_title,
                       q.retranslation_reason
                FROM queue q
                JOIN books b ON q.book_id = b.id
                WHERE {self._QUEUED_STATUS}
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
                cursor.execute(
                    "SELECT DISTINCT book_id FROM queue "
                    "WHERE status IS NULL OR status = 'queued'")
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
                    cursor.execute(
                        "SELECT COUNT(*) FROM queue WHERE book_id = ? "
                        "AND (status IS NULL OR status = 'queued')",
                        (book_id,))
                else:
                    cursor.execute(
                        "SELECT COUNT(*) FROM queue "
                        "WHERE status IS NULL OR status = 'queued'")

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
