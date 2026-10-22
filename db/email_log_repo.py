import datetime


class EmailLogRepo:
    """Outbound email log: one row per send attempt.

    status is 'sent' | 'failed' | 'suppressed' | 'bounced'. A 'sent' row
    becomes 'bounced' when the mail monitor ingests a bounce notice quoting
    its Message-ID (record_bounce). The Message-ID is generated per send and
    is unguessable, which is what lets a bounce be trusted enough to suppress
    the address.
    """

    @staticmethod
    def _log_now() -> str:
        return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    def log_email(self, data: dict) -> int:
        """Insert one send attempt. Returns the row id."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO email_log (sent_at, recipient, kind, subject, message_id, '
                'recommendation_id, comment_id, status, detail) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    data.get('sent_at') or self._log_now(),
                    (data.get('recipient') or '').strip().lower(),
                    data.get('kind') or 'other',
                    data.get('subject'),
                    data.get('message_id'),
                    data.get('recommendation_id'),
                    data.get('comment_id'),
                    data.get('status') or 'sent',
                    (data.get('detail') or None) and str(data['detail'])[:500],
                ),
            )
            return cursor.lastrowid

    def list_email_log(self, recipient: str = None, recommendation_id: int = None,
                       limit: int = 200) -> list:
        """Newest first, filtered by recipient and/or request."""
        where, params = [], []
        if recipient:
            where.append('recipient = ?')
            params.append(recipient.strip().lower())
        if recommendation_id is not None:
            where.append('recommendation_id = ?')
            params.append(int(recommendation_id))
        sql = 'SELECT * FROM email_log'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY sent_at DESC, id DESC LIMIT ?'
        params.append(int(limit))
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows

    def record_bounce(self, message_id: str, recipient: str, status: str,
                      diagnostic: str = None, bounced_at: str = None) -> list:
        """Mark the send that `message_id` identifies as bounced.

        Matches on Message-ID *and* recipient, so a bounce for one address can
        never mark another's row. Returns the matched rows (after update);
        empty means the bounce doesn't correspond to anything we logged.
        """
        if not message_id or not recipient:
            return []
        norm = recipient.strip().lower()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE email_log SET status = ?, bounced_at = ?, bounce_status = ?, '
                'bounce_diagnostic = ? WHERE message_id = ? AND recipient = ?',
                ('bounced', bounced_at or self._log_now(), (status or '')[:20] or None,
                 (diagnostic or '')[:1000] or None, message_id, norm))
            cursor.execute(
                'SELECT * FROM email_log WHERE message_id = ? AND recipient = ?',
                (message_id, norm))
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows
