import datetime
import traceback


class PolishJobsRepo:
    """Persisted LLM-polish runs for the write editor.

    A polish request creates a `polish_jobs` row and runs the provider call in
    a background thread; the parsed suggestions land in `polish_suggestions`
    with per-row status ('open' → 'accepted'/'dismissed' as the user works
    through them). The editor re-attaches the latest job on mount, so results
    survive navigation and service restarts without a second LLM call.
    """

    # Newest jobs kept per (book, chapter); older ones (and their
    # suggestions) are pruned on job creation.
    POLISH_JOBS_KEEP = 3

    def create_polish_job(self, book_id, chapter_number, model, text_chars):
        """Insert a 'running' job row and prune old jobs. Returns id or None."""
        try:
            now = datetime.datetime.now().isoformat()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                INSERT INTO polish_jobs
                (book_id, chapter_number, status, model, text_chars, created_at)
                VALUES (?, ?, 'running', ?, ?, ?)
                ''', (book_id, chapter_number, model, text_chars, now))
                job_id = cursor.lastrowid
            self._prune_polish_jobs(book_id, chapter_number)
            return job_id
        except Exception as e:
            self.logger.error(f"Error creating polish job: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return None

    def finish_polish_job(self, job_id, suggestions, truncated=False):
        """Store parsed suggestions and mark the job done."""
        try:
            now = datetime.datetime.now().isoformat()
            with self._conn() as conn:
                cursor = conn.cursor()
                for i, s in enumerate(suggestions):
                    cursor.execute('''
                    INSERT INTO polish_suggestions
                    (job_id, ord, find_text, replace_text, reason, occurrences, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'open')
                    ''', (job_id, i, s.get("find", ""), s.get("replace", ""),
                          s.get("reason", ""), s.get("occurrences", 1)))
                cursor.execute('''
                UPDATE polish_jobs SET status = 'done', truncated = ?, finished_at = ?
                WHERE id = ?
                ''', (1 if truncated else 0, now, job_id))
            return True
        except Exception as e:
            self.logger.error(f"Error finishing polish job {job_id}: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def fail_polish_job(self, job_id, error):
        """Mark a job failed with a short error message."""
        try:
            now = datetime.datetime.now().isoformat()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                UPDATE polish_jobs SET status = 'error', error = ?, finished_at = ?
                WHERE id = ?
                ''', (str(error)[:300], now, job_id))
            return True
        except Exception as e:
            self.logger.error(f"Error failing polish job {job_id}: {e}")
            if self.strict_writes:
                raise
            return False

    def get_polish_job(self, job_id):
        """Return one job with its suggestions, or None."""
        try:
            with self._conn(dict_rows=True) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM polish_jobs WHERE id = ?", (job_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                job = self._polish_job_dict(row)
                job["suggestions"] = self._polish_suggestions(cursor, job_id)
            return job
        except Exception as e:
            self.logger.error(f"Error getting polish job {job_id}: {e}")
            return None

    def latest_polish_job(self, book_id, chapter_number):
        """Return the newest job for a chapter (with suggestions), or None."""
        try:
            with self._conn(dict_rows=True) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                SELECT * FROM polish_jobs
                WHERE book_id = ? AND chapter_number = ?
                ORDER BY id DESC LIMIT 1
                ''', (book_id, chapter_number))
                row = cursor.fetchone()
                if not row:
                    return None
                job = self._polish_job_dict(row)
                job["suggestions"] = self._polish_suggestions(cursor, job["id"])
            return job
        except Exception as e:
            self.logger.error(f"Error getting latest polish job: {e}")
            return None

    def resolve_polish_suggestion(self, suggestion_id, status):
        """Set a suggestion's status ('accepted'/'dismissed'). True if found."""
        if status not in ("open", "accepted", "dismissed"):
            raise ValueError(f"invalid suggestion status: {status}")
        try:
            now = datetime.datetime.now().isoformat()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                UPDATE polish_suggestions
                SET status = ?, resolved_at = ?
                WHERE id = ?
                ''', (status, None if status == "open" else now, suggestion_id))
                found = cursor.rowcount > 0
            return found
        except Exception as e:
            self.logger.error(f"Error resolving polish suggestion {suggestion_id}: {e}")
            if self.strict_writes:
                raise
            return False

    def dismiss_open_polish_suggestions(self, job_id):
        """Dismiss every open suggestion of a job. Returns count dismissed."""
        try:
            now = datetime.datetime.now().isoformat()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                UPDATE polish_suggestions
                SET status = 'dismissed', resolved_at = ?
                WHERE job_id = ? AND status = 'open'
                ''', (now, job_id))
                count = cursor.rowcount
            return count
        except Exception as e:
            self.logger.error(f"Error dismissing polish suggestions for job {job_id}: {e}")
            if self.strict_writes:
                raise
            return 0

    def fail_stale_polish_jobs(self):
        """Mark all 'running' jobs as errored — called at startup, where any
        still-running row is an orphan of a previous process."""
        try:
            now = datetime.datetime.now().isoformat()
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                UPDATE polish_jobs
                SET status = 'error', error = 'Interrupted by server restart', finished_at = ?
                WHERE status = 'running'
                ''', (now,))
                count = cursor.rowcount
            if count:
                self.logger.info(f"Marked {count} orphaned polish job(s) as interrupted")
            return count
        except Exception as e:
            self.logger.error(f"Error failing stale polish jobs: {e}")
            return 0

    def _prune_polish_jobs(self, book_id, chapter_number):
        """Keep only the newest POLISH_JOBS_KEEP jobs per chapter.

        Suggestions are deleted explicitly — SQLite FK cascades aren't
        enforced without PRAGMA foreign_keys.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                SELECT id FROM polish_jobs
                WHERE book_id = ? AND chapter_number = ?
                ORDER BY id DESC
                ''', (book_id, chapter_number))
                doomed = [row[0] for row in cursor.fetchall()[self.POLISH_JOBS_KEEP:]]
                if doomed:
                    placeholders = ','.join('?' * len(doomed))
                    cursor.execute(
                        f"DELETE FROM polish_suggestions WHERE job_id IN ({placeholders})",
                        doomed)
                    cursor.execute(
                        f"DELETE FROM polish_jobs WHERE id IN ({placeholders})",
                        doomed)
            return True
        except Exception as e:
            self.logger.error(f"Error pruning polish jobs: {e}")
            return False

    # -- row shaping ---------------------------------------------------

    @staticmethod
    def _polish_job_dict(row):
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "chapter_number": row["chapter_number"],
            "status": row["status"],
            "model": row["model"],
            "text_chars": row["text_chars"],
            "truncated": bool(row["truncated"]),
            "error": row["error"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }

    @staticmethod
    def _polish_suggestions(cursor, job_id):
        cursor.execute('''
        SELECT id, find_text, replace_text, reason, occurrences, status
        FROM polish_suggestions WHERE job_id = ? ORDER BY ord
        ''', (job_id,))
        return [
            {"id": r["id"], "find": r["find_text"], "replace": r["replace_text"],
             "reason": r["reason"], "occurrences": r["occurrences"], "status": r["status"]}
            for r in cursor.fetchall()
        ]
