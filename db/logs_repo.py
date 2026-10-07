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

    # The list view never carries the three text columns. They are ~95% of a
    # 4.5 GB table, a single call's prompt runs to tens of KB, and the page only
    # shows them for the session someone expands (get_api_call_session).
    _API_CALL_META = ('ac.id, ac.session_id, ac.book_id, ac.chapter_number, ac.chunk_index, '
                      'ac.total_chunks, ac.model_name, ac.provider, ac.prompt_tokens, '
                      'ac.completion_tokens, ac.total_tokens, ac.duration_ms, ac.success, '
                      'ac.attempt, ac.created_at, b.title')
    _API_CALL_META_KEYS = ('id', 'session_id', 'book_id', 'chapter_number', 'chunk_index',
                           'total_chunks', 'model_name', 'provider', 'prompt_tokens',
                           'completion_tokens', 'total_tokens', 'duration_ms', 'success',
                           'attempt', 'created_at', 'book_title')
    _API_CALL_SCAN_BATCH = 500

    def list_api_call_sessions(self, book_id=None, chapter_number=None, before=None, limit=50):
        """One page of API-call sessions, newest first, metadata only.

        Returns ``(sessions, next_before)``. A session sorts by its newest call's
        id, and a page holds the sessions whose newest id is below ``before``;
        ``next_before`` is the cursor for the page after, or None at the end.

        Ordering is by id, not created_at: id is the primary key, so the scan
        walks an index instead of filesorting the table, and calls are inserted
        as they happen, so the two orders agree. The scan has to be by call and
        not by session because concurrent jobs interleave their calls — which is
        also why a session reached through an older call is checked against its
        true newest id: if that id is at or above ``before``, an earlier page
        already showed it.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                where, params = [], []
                if book_id is not None:
                    where.append('book_id = ?')
                    params.append(book_id)
                    if chapter_number is not None:
                        where.append('chapter_number = ?')
                        params.append(chapter_number)

                kept = []            # [(session_id, newest_id)] in newest-first order
                seen = set()
                cursor_id = before
                while len(kept) <= limit:
                    clauses = list(where)
                    args = list(params)
                    if cursor_id is not None:
                        clauses.append('id < ?')
                        args.append(cursor_id)
                    sql = 'SELECT id, session_id FROM api_calls'
                    if clauses:
                        sql += ' WHERE ' + ' AND '.join(clauses)
                    sql += ' ORDER BY id DESC LIMIT ?'
                    cursor.execute(sql, (*args, self._API_CALL_SCAN_BATCH))
                    batch = cursor.fetchall()
                    if not batch:
                        break
                    cursor_id = batch[-1][0]

                    fresh = []
                    for row_id, sid in batch:
                        if sid not in seen:
                            seen.add(sid)
                            fresh.append((sid, row_id))
                    if before is not None and fresh:
                        marks = ','.join('?' * len(fresh))
                        cursor.execute(
                            f'SELECT session_id, MAX(id) FROM api_calls '
                            f'WHERE session_id IN ({marks}) GROUP BY session_id',
                            [sid for sid, _ in fresh])
                        newest = dict(cursor.fetchall())
                        fresh = [(sid, rid) for sid, rid in fresh
                                 if newest.get(sid, rid) < before]
                    kept.extend(fresh)
                    if len(batch) < self._API_CALL_SCAN_BATCH:
                        break

                page = kept[:limit]
                next_before = page[-1][1] if len(kept) > limit else None
                if not page:
                    return [], None

                marks = ','.join('?' * len(page))
                cursor.execute(
                    f'SELECT {self._API_CALL_META} FROM api_calls ac '
                    f'LEFT JOIN books b ON ac.book_id = b.id '
                    f'WHERE ac.session_id IN ({marks}) '
                    f'ORDER BY ac.chunk_index ASC, ac.attempt ASC, ac.id ASC',
                    [sid for sid, _ in page])
                rows = [dict(zip(self._API_CALL_META_KEYS, r)) for r in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"Error listing API call sessions: {e}")
            return [], None

        calls_by_session = {}
        for row in rows:
            calls_by_session.setdefault(row['session_id'], []).append(row)
        sessions = []
        for sid, newest_id in page:
            calls = calls_by_session.get(sid)
            if not calls:
                continue
            head = max(calls, key=lambda c: c['id'])
            sessions.append({
                'session_id': sid,
                'book_id': head['book_id'],
                'book_title': head['book_title'] or '',
                'chapter_number': head['chapter_number'],
                'model_name': head['model_name'],
                'provider': head['provider'],
                'created_at': head['created_at'],
                'total_chunks': head['total_chunks'],
                'calls': calls,
            })
        return sessions, next_before

    def get_api_call_session(self, session_id):
        """Every call of one session, prompts and response included."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f'SELECT {self._API_CALL_META}, ac.system_prompt, ac.user_prompt, '
                    f'ac.response_text FROM api_calls ac '
                    f'LEFT JOIN books b ON ac.book_id = b.id '
                    f'WHERE ac.session_id = ? '
                    f'ORDER BY ac.chunk_index ASC, ac.attempt ASC, ac.id ASC',
                    (session_id,))
                keys = self._API_CALL_META_KEYS + ('system_prompt', 'user_prompt', 'response_text')
                return [dict(zip(keys, r)) for r in cursor.fetchall()]
        except Exception as e:
            self.logger.error(f"Error getting API call session {session_id}: {e}")
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
