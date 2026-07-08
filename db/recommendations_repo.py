import datetime


class RecommendationsRepo:
    """Reader recommendation submissions (recommendations table)."""

    def create_recommendation(self, data: dict) -> int:
        """Insert a new recommendation and return its id."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO recommendations (novel_title, author, source_url, source_language, '
                'description, requester_name, requester_email, notes, status, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    data['novel_title'],
                    data.get('author'),
                    data['source_url'],
                    data.get('source_language', 'zh'),
                    data.get('description'),
                    data['requester_name'],
                    data['requester_email'],
                    data.get('notes'),
                    'new',
                    datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                ),
            )
            rec_id = cursor.lastrowid
        return rec_id

    # Correlated subqueries add reply_count / unread_reply_count to each row so
    # the admin UI can render per-request badges without a second round-trip.
    _REC_REPLY_COUNTS = (
        '(SELECT COUNT(*) FROM recommendation_replies rr '
        'WHERE rr.recommendation_id = r.id) AS reply_count, '
        '(SELECT COUNT(*) FROM recommendation_replies rr '
        'WHERE rr.recommendation_id = r.id AND rr.is_read = 0) AS unread_reply_count'
    )

    def list_recommendations(self, status: str = None) -> list:
        """List recommendations, optionally filtered by status."""
        with self._conn() as conn:
            cursor = conn.cursor()
            select = f'SELECT r.*, {self._REC_REPLY_COUNTS} FROM recommendations r'
            if status:
                cursor.execute(
                    f'{select} WHERE r.status = ? ORDER BY r.created_at DESC', (status,)
                )
            else:
                cursor.execute(f'{select} ORDER BY r.created_at DESC')
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows

    def get_recommendation(self, rec_id: int):
        """Fetch a single recommendation by id."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f'SELECT r.*, {self._REC_REPLY_COUNTS} FROM recommendations r WHERE r.id = ?',
                (rec_id,))
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))

    def update_recommendation(self, rec_id: int, updates: dict):
        """Update fields on a recommendation."""
        allowed = {'status', 'admin_notes', 'reviewed_at'}
        parts, vals = [], []
        for k, v in updates.items():
            if k in allowed:
                parts.append(f'{k} = ?')
                vals.append(v)
        if not parts:
            return
        vals.append(rec_id)
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f'UPDATE recommendations SET {", ".join(parts)} WHERE id = ?', vals)

    def delete_recommendation(self, rec_id: int):
        """Delete a recommendation."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM recommendations WHERE id = ?', (rec_id,))

    def count_recommendations(self, status: str = None) -> int:
        """Count recommendations, optionally filtered by status."""
        with self._conn() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute('SELECT COUNT(*) FROM recommendations WHERE status = ?', (status,))
            else:
                cursor.execute('SELECT COUNT(*) FROM recommendations')
            count = cursor.fetchone()[0]
        return count
