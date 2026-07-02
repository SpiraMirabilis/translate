import json
import datetime
import traceback


def _word_count(lines):
    """Whitespace word count across a chapter's line array."""
    return sum(len(line.split()) for line in lines if isinstance(line, str))


class ChapterRevisionsRepo:
    """Saved-version history for chapters edited in the web editors.

    Revisions are written by the write editor's save path: every explicit save
    stores a 'manual' revision; autosaves store time-coalesced 'auto' revisions.
    Content is a JSON line array, same encoding as chapters.translated_content.
    """

    # Newest revisions kept per (book, chapter), by kind.
    REVISIONS_KEEP_AUTO = 20
    REVISIONS_KEEP_MANUAL = 50

    def add_chapter_revision(self, book_id, chapter_number, title, content_lines, kind='manual'):
        """Insert a revision snapshot and prune old ones. Returns revision id or None."""
        try:
            if not isinstance(content_lines, list):
                content_lines = str(content_lines).split('\n')
            timestamp = datetime.datetime.now().isoformat()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                INSERT INTO chapter_revisions
                (book_id, chapter_number, title, content, word_count, kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (book_id, chapter_number, title,
                      json.dumps(content_lines, ensure_ascii=False),
                      _word_count(content_lines), kind, timestamp))
                revision_id = cursor.lastrowid
            self.prune_chapter_revisions(book_id, chapter_number)
            return revision_id
        except Exception as e:
            self.logger.error(f"Error adding chapter revision: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return None

    def list_chapter_revisions(self, book_id, chapter_number, limit=50):
        """Return revision metadata (no content), newest first."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                SELECT id, kind, title, word_count, created_at
                FROM chapter_revisions
                WHERE book_id = ? AND chapter_number = ?
                ORDER BY id DESC LIMIT ?
                ''', (book_id, chapter_number, int(limit)))
                rows = cursor.fetchall()
            return [
                {"id": row[0], "kind": row[1], "title": row[2],
                 "word_count": row[3], "created_at": row[4]}
                for row in rows
            ]
        except Exception as e:
            self.logger.error(f"Error listing chapter revisions: {e}")
            return []

    def get_chapter_revision(self, revision_id):
        """Return one full revision with content decoded to a line array, or None."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                SELECT id, book_id, chapter_number, title, content, word_count, kind, created_at
                FROM chapter_revisions WHERE id = ?
                ''', (revision_id,))
                row = cursor.fetchone()
            if not row:
                return None
            try:
                content = json.loads(row[4])
            except (json.JSONDecodeError, TypeError):
                content = row[4].split('\n') if isinstance(row[4], str) else []
            return {
                "id": row[0], "book_id": row[1], "chapter_number": row[2],
                "title": row[3], "content": content, "word_count": row[5],
                "kind": row[6], "created_at": row[7],
            }
        except Exception as e:
            self.logger.error(f"Error getting chapter revision: {e}")
            return None

    def latest_revision_time(self, book_id, chapter_number):
        """created_at of the newest revision of any kind, or None."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                SELECT MAX(created_at) FROM chapter_revisions
                WHERE book_id = ? AND chapter_number = ?
                ''', (book_id, chapter_number))
                row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            self.logger.error(f"Error getting latest revision time: {e}")
            return None

    def prune_chapter_revisions(self, book_id, chapter_number):
        """Keep only the newest N revisions per kind for a chapter.

        Selects the ids to delete Python-side: MySQL can't DELETE with a
        LIMIT subquery on the same table, and per-chapter row counts are tiny.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                doomed = []
                for kind, keep in (('auto', self.REVISIONS_KEEP_AUTO),
                                   ('manual', self.REVISIONS_KEEP_MANUAL)):
                    cursor.execute('''
                    SELECT id FROM chapter_revisions
                    WHERE book_id = ? AND chapter_number = ? AND kind = ?
                    ORDER BY id DESC
                    ''', (book_id, chapter_number, kind))
                    doomed.extend(row[0] for row in cursor.fetchall()[keep:])
                if doomed:
                    placeholders = ','.join('?' * len(doomed))
                    cursor.execute(
                        f"DELETE FROM chapter_revisions WHERE id IN ({placeholders})",
                        doomed)
            return True
        except Exception as e:
            self.logger.error(f"Error pruning chapter revisions: {e}")
            return False
