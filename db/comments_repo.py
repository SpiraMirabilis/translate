import datetime
from typing import Optional


class CommentsRepo:
    """Chapter comments, commenters, bans, email suppressions, and reply-notification records."""

    COMMENT_DEPTH_CAP = 5

    COMMENT_PUBLIC_FIELDS = (
        'id', 'book_id', 'chapter_number', 'parent_id', 'depth', 'root_id',
        'commenter_uuid', 'display_name', 'body', 'status',
        'edited_at', 'deleted_at', 'created_at',
    )

    COMMENT_ADMIN_FIELDS = COMMENT_PUBLIC_FIELDS + (
        'email', 'ip', 'user_agent', 'automod_state', 'automod_reason',
    )

    @staticmethod
    def _now() -> str:
        return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    def _row_to_dict(self, cursor, row):
        if row is None:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))

    def is_banned(self, *, uuid=None, email=None, ip=None) -> bool:
        """Return True if any of the provided identifiers is banned."""
        if not (uuid or email or ip):
            return False
        with self._conn() as conn:
            cursor = conn.cursor()
            clauses, vals = [], []
            if uuid:
                clauses.append('(kind = ? AND value = ?)'); vals.extend(('uuid', uuid))
            if email:
                clauses.append('(kind = ? AND value = ?)'); vals.extend(('email', email.lower()))
            if ip:
                clauses.append('(kind = ? AND value = ?)'); vals.extend(('ip', ip))
            cursor.execute(f"SELECT 1 FROM comment_bans WHERE {' OR '.join(clauses)} LIMIT 1", vals)
            hit = cursor.fetchone() is not None
        return hit

    def is_commenter_trusted(self, uuid: str) -> bool:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT is_trusted FROM commenters WHERE uuid = ?', (uuid,))
            row = cursor.fetchone()
        return bool(row and row[0])

    def bump_commenter(self, uuid: str, display_name: str, email: str):
        """UPSERT a commenter row; refresh last_seen and increment comment_count."""
        now = self._now()
        email_norm = (email or '').strip().lower() or None
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT comment_count FROM commenters WHERE uuid = ?', (uuid,))
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    'INSERT INTO commenters (uuid, display_name, email, is_trusted, '
                    'first_seen, last_seen, comment_count) VALUES (?, ?, ?, 0, ?, ?, 1)',
                    (uuid, display_name, email_norm, now, now),
                )
            else:
                cursor.execute(
                    'UPDATE commenters SET display_name = ?, email = ?, last_seen = ?, '
                    'comment_count = comment_count + 1 WHERE uuid = ?',
                    (display_name, email_norm, now, uuid),
                )

    def get_comment(self, comment_id: int) -> Optional[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM comments WHERE id = ?', (comment_id,))
            row = cursor.fetchone()
            result = self._row_to_dict(cursor, row)
        return result

    def create_comment(self, data: dict) -> int:
        """
        Insert a new comment. Computes depth/root_id from parent, applies
        trust + ban logic to determine initial status.

        Required keys: book_id, chapter_number, commenter_uuid, display_name,
        body, ip. Optional: parent_id, email, user_agent.
        """
        book_id = int(data['book_id'])
        chapter_number = int(data['chapter_number'])
        parent_id = data.get('parent_id')
        uuid = data['commenter_uuid']
        display_name = data['display_name'][:40]
        email = (data.get('email') or '').strip().lower() or None
        body = data['body'][:4000]
        ip = data['ip']
        user_agent = (data.get('user_agent') or '')[:256] or None
        notify_replies = 1 if data.get('notify_replies') else 0

        depth = 0
        root_id = None
        if parent_id:
            parent = self.get_comment(int(parent_id))
            if parent is None or parent['book_id'] != book_id or parent['chapter_number'] != chapter_number:
                raise ValueError("parent comment not found in this chapter")
            depth = min(int(parent['depth']) + 1, self.COMMENT_DEPTH_CAP)
            root_id = parent['root_id'] or parent['id']

        if self.is_banned(uuid=uuid, email=email, ip=ip):
            status = 'blocked'
        elif self.is_commenter_trusted(uuid):
            status = 'approved'
        else:
            status = 'pending'

        now = self._now()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO comments (book_id, chapter_number, parent_id, depth, root_id, '
                'commenter_uuid, display_name, email, body, status, ip, user_agent, '
                'notify_replies, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (book_id, chapter_number, parent_id, depth, root_id, uuid,
                 display_name, email, body, status, ip, user_agent, notify_replies, now),
            )
            new_id = cursor.lastrowid

        self.bump_commenter(uuid, display_name, email or '')
        return new_id

    def list_comments_for_chapter(self, book_id: int, chapter_number: int,
                                  viewer_uuid: Optional[str] = None) -> list:
        """
        Return comments visible to the viewer:
          - status='approved' (visible to all)
          - status='deleted' (rendered as [removed] to preserve thread)
          - status='pending' or 'blocked' (only if commenter_uuid == viewer_uuid)
        """
        with self._conn() as conn:
            cursor = conn.cursor()
            if viewer_uuid:
                cursor.execute(
                    'SELECT * FROM comments WHERE book_id = ? AND chapter_number = ? '
                    "AND (status IN ('approved', 'deleted') OR commenter_uuid = ?) "
                    'ORDER BY COALESCE(root_id, id), created_at',
                    (book_id, chapter_number, viewer_uuid),
                )
            else:
                cursor.execute(
                    'SELECT * FROM comments WHERE book_id = ? AND chapter_number = ? '
                    "AND status IN ('approved', 'deleted') "
                    'ORDER BY COALESCE(root_id, id), created_at',
                    (book_id, chapter_number),
                )
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows

    def count_comments_visible(self, book_id: int, chapter_number: int,
                               viewer_uuid: Optional[str] = None) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            if viewer_uuid:
                cursor.execute(
                    'SELECT COUNT(*) FROM comments WHERE book_id = ? AND chapter_number = ? '
                    "AND status != 'deleted' "
                    "AND (status = 'approved' OR commenter_uuid = ?)",
                    (book_id, chapter_number, viewer_uuid),
                )
            else:
                cursor.execute(
                    'SELECT COUNT(*) FROM comments WHERE book_id = ? AND chapter_number = ? '
                    "AND status = 'approved'",
                    (book_id, chapter_number),
                )
            n = cursor.fetchone()[0]
        return n

    def count_pending_comments(self, book_id: Optional[int] = None) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            if book_id is not None:
                cursor.execute(
                    "SELECT COUNT(*) FROM comments WHERE status = 'pending' AND book_id = ?",
                    (book_id,),
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM comments WHERE status = 'pending'")
            n = cursor.fetchone()[0]
        return n

    def list_comments_admin(self, *, status: Optional[str] = None,
                            book_id: Optional[int] = None,
                            chapter_number: Optional[int] = None,
                            limit: int = 200, offset: int = 0) -> list:
        clauses, vals = [], []
        if status:
            clauses.append('status = ?'); vals.append(status)
        if book_id is not None:
            clauses.append('book_id = ?'); vals.append(book_id)
        if chapter_number is not None:
            clauses.append('chapter_number = ?'); vals.append(chapter_number)
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        vals.extend((int(limit), int(offset)))
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f'SELECT * FROM comments{where} ORDER BY created_at DESC LIMIT ? OFFSET ?', vals)
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows

    def update_comment(self, comment_id: int, *, body: Optional[str] = None,
                       status: Optional[str] = None, soft_delete: bool = False,
                       automod_state: Optional[str] = None,
                       automod_reason: Optional[str] = None) -> bool:
        """
        Update a comment. Returns True on success.

          - body: replace body and set edited_at; if previous status was 'approved',
                  demote to 'pending' (caller may override via explicit status).
          - status: explicit status override.
          - soft_delete: replace body with [removed], set deleted_at, status='deleted'.
          - automod_state / automod_reason: record automod verdict metadata.

        When status transitions to 'approved', the matching commenter is also
        flipped to is_trusted=1 (first-comment approval grants future trust).
        """
        existing = self.get_comment(comment_id)
        if existing is None:
            return False

        sets, vals = [], []
        new_status = None
        new_body = None
        now = self._now()

        if soft_delete:
            sets.append('body = ?'); vals.append('[removed]')
            sets.append('status = ?'); vals.append('deleted')
            sets.append('deleted_at = ?'); vals.append(now)
            new_status = 'deleted'
        else:
            if body is not None:
                new_body = body[:4000]
                sets.append('body = ?'); vals.append(new_body)
                sets.append('edited_at = ?'); vals.append(now)
                # Demote to pending if it was approved and caller didn't pin a status
                if existing['status'] == 'approved' and status is None:
                    sets.append('status = ?'); vals.append('pending')
                    new_status = 'pending'
            if status is not None:
                sets.append('status = ?'); vals.append(status)
                new_status = status
            if automod_state is not None:
                sets.append('automod_state = ?'); vals.append(automod_state)
            if automod_reason is not None:
                sets.append('automod_reason = ?'); vals.append(automod_reason[:255])

        if not sets:
            return False

        vals.append(comment_id)
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE comments SET {', '.join(sets)} WHERE id = ?", vals)

        # Trust escalation on approval
        if new_status == 'approved':
            self._mark_uuid_trusted(existing['commenter_uuid'])
        return True

    def set_comment_status(self, comment_id: int, status: str) -> bool:
        return self.update_comment(comment_id, status=status)

    def _mark_uuid_trusted(self, uuid: str):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE commenters SET is_trusted = 1 WHERE uuid = ?', (uuid,))

    def hard_delete_comment(self, comment_id: int) -> bool:
        """
        Hard-delete a comment. Refuses if children exist (forces soft delete to
        preserve thread integrity).
        """
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM comments WHERE parent_id = ?', (comment_id,))
            children = cursor.fetchone()[0]
            if children:
                return False
            cursor.execute('DELETE FROM comments WHERE id = ?', (comment_id,))
        return True

    def add_ban(self, kind: str, value: str, reason: Optional[str] = None) -> int:
        if kind not in ('uuid', 'email', 'ip'):
            raise ValueError(f"invalid ban kind: {kind}")
        if kind == 'email':
            value = value.strip().lower()
        else:
            value = value.strip()
        now = self._now()
        with self._conn() as conn:
            cursor = conn.cursor()
            # Pre-check to keep behavior identical across SQLite/MySQL (avoid INSERT OR IGNORE)
            cursor.execute('SELECT id FROM comment_bans WHERE kind = ? AND value = ?', (kind, value))
            existing = cursor.fetchone()
            if existing:
                return int(existing[0])
            cursor.execute(
                'INSERT INTO comment_bans (kind, value, reason, cf_pushed, created_at) '
                'VALUES (?, ?, ?, 0, ?)',
                (kind, value, reason, now),
            )
            ban_id = cursor.lastrowid
            # Revoke trust for any commenter matching this identifier
            if kind == 'uuid':
                cursor.execute('UPDATE commenters SET is_trusted = 0 WHERE uuid = ?', (value,))
            elif kind == 'email':
                cursor.execute('UPDATE commenters SET is_trusted = 0 WHERE email = ?', (value,))
        return ban_id

    def remove_ban(self, kind: str, value: str) -> bool:
        if kind == 'email':
            value = value.strip().lower()
        else:
            value = value.strip()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM comment_bans WHERE kind = ? AND value = ?', (kind, value))
            rc = cursor.rowcount
        return rc > 0

    def remove_ban_by_id(self, ban_id: int) -> Optional[dict]:
        """Remove a ban by id, returning the row that was deleted (for CF cleanup)."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM comment_bans WHERE id = ?', (ban_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cursor.description]
            ban = dict(zip(cols, row))
            cursor.execute('DELETE FROM comment_bans WHERE id = ?', (ban_id,))
        return ban

    def list_bans(self) -> list:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM comment_bans ORDER BY created_at DESC')
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows

    def mark_cf_pushed(self, ban_id: int):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE comment_bans SET cf_pushed = 1 WHERE id = ?', (ban_id,))

    def is_email_suppressed(self, email: str) -> bool:
        if not email:
            return False
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM email_suppressions WHERE email = ? LIMIT 1',
                           (email.strip().lower(),))
            hit = cursor.fetchone() is not None
        return hit

    def add_email_suppression(self, email: str, reason: str = 'unsubscribe') -> bool:
        if not email:
            return False
        norm = email.strip().lower()
        now = self._now()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM email_suppressions WHERE email = ?', (norm,))
            if cursor.fetchone():
                return False
            cursor.execute(
                'INSERT INTO email_suppressions (email, reason, created_at) VALUES (?, ?, ?)',
                (norm, reason, now),
            )
        return True

    def remove_email_suppression(self, email: str) -> bool:
        if not email:
            return False
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM email_suppressions WHERE email = ?', (email.strip().lower(),))
            rc = cursor.rowcount
        return rc > 0

    def list_email_suppressions(self) -> list:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM email_suppressions ORDER BY created_at DESC')
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        return rows

    def was_notified(self, comment_id: int, recipient_email: str) -> bool:
        if not recipient_email:
            return False
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT 1 FROM email_notifications WHERE comment_id = ? AND recipient_email = ? LIMIT 1',
                (comment_id, recipient_email.strip().lower()),
            )
            hit = cursor.fetchone() is not None
        return hit

    def record_notification(self, comment_id: int, recipient_email: str) -> bool:
        """Insert an idempotency row. Returns True on insert, False if duplicate.

        The UNIQUE(comment_id, recipient_email) constraint is the actual
        guarantee against duplicate sends — this method is the only place
        that writes to it. Caller MUST send the email AFTER this returns
        True; if the email send fails, the next call will see the row and
        skip, which is the safe direction (one missed retry > duplicate).
        Actually we send first, log second, since postfix is local and
        reliable; see notify_reply for the ordering rationale.
        """
        if not recipient_email:
            return False
        norm = recipient_email.strip().lower()
        now = self._now()
        with self._conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    'INSERT INTO email_notifications (comment_id, recipient_email, sent_at) '
                    'VALUES (?, ?, ?)',
                    (comment_id, norm, now),
                )
                return True
            except Exception:
                # Duplicate — UNIQUE constraint violation
                conn.rollback()
                return False
            finally:
                conn.close()
