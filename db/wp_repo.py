import datetime
import traceback


class WpStateRepo:
    """WordPress publish-state tracking (wp_state table)."""

    def get_wp_state(self, book_id, chapter_number=None):
        """Get a single wp_publish_state record."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                if chapter_number is None:
                    cursor.execute(
                        "SELECT id, book_id, chapter_number, wp_post_id, wp_post_type, last_published, content_hash "
                        "FROM wp_publish_state WHERE book_id = ? AND chapter_number IS NULL",
                        (book_id,),
                    )
                else:
                    cursor.execute(
                        "SELECT id, book_id, chapter_number, wp_post_id, wp_post_type, last_published, content_hash "
                        "FROM wp_publish_state WHERE book_id = ? AND chapter_number = ?",
                        (book_id, chapter_number),
                    )
                row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "book_id": row[1], "chapter_number": row[2],
                "wp_post_id": row[3], "wp_post_type": row[4],
                "last_published": row[5], "content_hash": row[6],
            }
        except Exception as e:
            self.logger.error(f"Error getting wp state: {e}")
            return None

    def save_wp_state(self, book_id, chapter_number, wp_post_id, wp_post_type, content_hash):
        """Upsert a wp_publish_state record."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                now = datetime.datetime.utcnow().isoformat()
                cursor.execute(self.backend.upsert_wp_state_sql(),
                    (book_id, chapter_number, wp_post_id, wp_post_type, now, content_hash))
        except Exception as e:
            self.logger.error(f"Error saving wp state: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise

    def get_all_wp_states(self, book_id):
        """Get all wp_publish_state records for a book."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, book_id, chapter_number, wp_post_id, wp_post_type, last_published, content_hash "
                    "FROM wp_publish_state WHERE book_id = ?",
                    (book_id,),
                )
                rows = cursor.fetchall()
            return [
                {
                    "id": r[0], "book_id": r[1], "chapter_number": r[2],
                    "wp_post_id": r[3], "wp_post_type": r[4],
                    "last_published": r[5], "content_hash": r[6],
                }
                for r in rows
            ]
        except Exception as e:
            self.logger.error(f"Error getting all wp states: {e}")
            return []

    def delete_wp_states(self, book_id):
        """Delete all wp_publish_state records for a book."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM wp_publish_state WHERE book_id = ?", (book_id,))
        except Exception as e:
            self.logger.error(f"Error deleting wp states: {e}")

    def delete_wp_state_single(self, book_id, chapter_number=None):
        """Delete a single wp_publish_state record."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                if chapter_number is None:
                    cursor.execute(
                        "DELETE FROM wp_publish_state WHERE book_id = ? AND chapter_number IS NULL",
                        (book_id,),
                    )
                else:
                    cursor.execute(
                        "DELETE FROM wp_publish_state WHERE book_id = ? AND chapter_number = ?",
                        (book_id, chapter_number),
                    )
        except Exception as e:
            self.logger.error(f"Error deleting wp state: {e}")
