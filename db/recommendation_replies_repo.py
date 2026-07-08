import datetime


class RecommendationRepliesRepo:
    """Email replies from requesters, ingested from the editor mbox.

    A reply with recommendation_id NULL is "unmatched" — it looked
    rec-correlated to the daemon but its plus-tag signature failed to verify
    (or no matching request exists). Surfaced in the admin UI's Unmatched tab
    so nothing is silently lost.
    """

    def insert_reply(self, data: dict):
        """Insert a reply, deduping on message_id. Returns (reply_id, inserted).

        If a row with the same non-null message_id already exists, returns
        (existing_id, False) without inserting.
        """
        message_id = data.get('message_id') or None
        with self._conn() as conn:
            cursor = conn.cursor()
            if message_id:
                cursor.execute(
                    'SELECT id FROM recommendation_replies WHERE message_id = ?',
                    (message_id,))
                existing = cursor.fetchone()
                if existing:
                    return (existing[0], False)
            cursor.execute(
                'INSERT INTO recommendation_replies (recommendation_id, from_email, '
                'from_name, subject, body, message_id, in_reply_to, correlation, '
                'received_at, is_read) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)',
                (
                    data.get('recommendation_id'),
                    data.get('from_email'),
                    data.get('from_name'),
                    data.get('subject'),
                    data.get('body') or '',
                    message_id,
                    data.get('in_reply_to'),
                    data.get('correlation') or 'unmatched',
                    data.get('received_at') or datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                ),
            )
            reply_id = cursor.lastrowid
        return (reply_id, True)

    def list_replies(self, rec_id: int) -> list:
        """List replies for a recommendation, oldest first."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM recommendation_replies WHERE recommendation_id = ? '
                'ORDER BY received_at ASC, id ASC', (rec_id,))
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows

    def list_unmatched_replies(self) -> list:
        """List replies that could not be correlated to a request."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM recommendation_replies WHERE recommendation_id IS NULL '
                'ORDER BY received_at DESC, id DESC')
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows

    def mark_replies_read(self, rec_id: int):
        """Mark all replies for a recommendation as read."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE recommendation_replies SET is_read = 1 '
                'WHERE recommendation_id = ? AND is_read = 0', (rec_id,))

    def count_unread_replies(self) -> int:
        """Total unread replies across all requests (matched only) — nav badge."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COUNT(*) FROM recommendation_replies '
                'WHERE is_read = 0 AND recommendation_id IS NOT NULL')
            count = cursor.fetchone()[0]
        return count
