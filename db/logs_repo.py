import json
import datetime
import traceback


class LogsRepo:
    """Activity log, reader view log / view counts, and API-call logging."""

    def add_activity_log(self, type, message, book_id=None, chapter=None, book_name=None, entities=None):
        """Add an entry to the activity log. Returns the entry dict."""
        try:
            with self._conn() as conn:
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
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id, type, message, book_id, chapter, book_name, entities_json, created_at FROM activity_log ORDER BY id DESC LIMIT ?', (limit,))
                rows = cursor.fetchall()
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
            with self._conn() as conn:
                conn.execute('DELETE FROM activity_log')
        except Exception as e:
            self.logger.error(f"Error clearing activity log: {e}")

    def log_reader_view(self, book_id: int, chapter_number: int, ip: str):
        """Record a chapter view from the public reader and bump the book's view_count."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'INSERT INTO reader_log (book_id, chapter_number, ip, viewed_at) VALUES (?, ?, ?, ?)',
                    (book_id, chapter_number, ip, datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
                )
                cursor.execute(
                    'UPDATE books SET view_count = view_count + 1 WHERE id = ?',
                    (book_id,)
                )
        except Exception as e:
            self.logger.error(f"Error logging reader view: {e}")

    def flush_reader_views(self, views, book_bumps):
        """Bulk write buffered reader views (see web/services/view_logger.py).

        Args:
            views: list of (book_id, chapter_number, ip) tuples for reader_log
            book_bumps: {book_id: count} aggregate view_count increments
                        (already includes the per-view bumps)
        """
        if not views and not book_bumps:
            return
        with self._conn() as conn:
            cursor = conn.cursor()
            if views:
                now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                cursor.executemany(
                    'INSERT INTO reader_log (book_id, chapter_number, ip, viewed_at) VALUES (?, ?, ?, ?)',
                    [(b, c, ip, now) for (b, c, ip) in views]
                )
            for book_id, count in book_bumps.items():
                cursor.execute(
                    'UPDATE books SET view_count = view_count + ? WHERE id = ?',
                    (int(count), book_id)
                )

    def increment_book_view_count(self, book_id: int):
        """Atomically bump books.view_count by 1. Does not write reader_log."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'UPDATE books SET view_count = view_count + 1 WHERE id = ?',
                    (book_id,)
                )
        except Exception as e:
            self.logger.error(f"Error incrementing view_count for book {book_id}: {e}")

    def get_reader_log(self, book_id: int = None, limit: int = 200):
        """Return recent reader log entries, optionally filtered by book."""
        try:
            with self._conn() as conn:
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
                cols = ['id', 'book_id', 'chapter_number', 'ip', 'viewed_at']
                return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            self.logger.error(f"Error reading reader log: {e}")
            return []

    def log_api_call(self, session_id, book_id, chapter_number, chunk_index,
                     total_chunks, system_prompt, user_prompt, response_text,
                     model_name, provider, prompt_tokens=0, completion_tokens=0,
                     total_tokens=0, duration_ms=0, success=1, attempt=0):
        """Log an LLM API call. Returns the row id or None on failure."""
        try:
            with self._conn() as conn:
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
            return row_id
        except Exception as e:
            self.logger.error(f"Error logging API call: {e}")
            return None

    def get_all_api_calls(self, book_id=None, limit=500):
        """Get API call logs across all books, optionally filtered by book_id."""
        try:
            with self._conn() as conn:
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
            with self._conn() as conn:
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
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT id, session_id, book_id, chapter_number, chunk_index, total_chunks, '
                    'system_prompt, user_prompt, response_text, model_name, provider, '
                    'prompt_tokens, completion_tokens, total_tokens, duration_ms, success, attempt, created_at '
                    'FROM api_calls WHERE id = ?',
                    (call_id,),
                )
                r = cursor.fetchone()
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
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'UPDATE api_calls SET response_text = ? WHERE id = ?',
                    (response_text, call_id),
                )
            return True
        except Exception as e:
            self.logger.error(f"Error updating API call response: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False
