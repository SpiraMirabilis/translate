import datetime
import traceback

_CANDIDATE_COLS = ("id", "book_id", "chapter_number", "chapter_title",
                   "term_zh", "term_en", "body", "sentence", "model",
                   "status", "created_date")

_VALID_STATUSES = ("pending", "accepted", "rejected")


class FootnoteCandidatesRepo:
    """LLM-collected cultural-referent footnote SUGGESTIONS awaiting review.

    Written by footnote_scan.py (bulk CLI) and the footnote_scan module
    (per-chapter on ingest); read by the review GUI and the CLI's
    --review/--report/--export modes. Candidates never touch chapters or the
    real footnotes table — applying approved ones stays a separate, manual
    step (add_footnotes.py).

    footnote_scans is the re-run guard: one row per scanned (book, chapter)
    with the sha256 of the source it saw. Same hash → skip on re-scan; a
    changed hash means the stored source was rewritten (e.g. trad→simp
    retrofit) and the chapter is re-scanned. Zero-find chapters keep a scan
    row — that is the whole point of the guard.
    """

    def _candidate_row(self, row):
        return dict(zip(_CANDIDATE_COLS, row))

    def list_footnote_candidates(self, book_id, status=None, chapter=None):
        """Candidate rows for a book, ordered by chapter_number then id."""
        try:
            query = (f"SELECT {', '.join(_CANDIDATE_COLS)} FROM footnote_candidates"
                     " WHERE book_id = ?")
            params = [book_id]
            if status:
                query += " AND status = ?"
                params.append(status)
            if chapter is not None:
                query += " AND chapter_number = ?"
                params.append(chapter)
            query += " ORDER BY chapter_number, id"
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
            return [self._candidate_row(r) for r in rows]
        except Exception as e:
            self.logger.error(f"Error listing footnote candidates: {e}")
            return []

    def get_footnote_candidate(self, cand_id):
        """One candidate row as a dict, or None."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT {', '.join(_CANDIDATE_COLS)} FROM footnote_candidates"
                    " WHERE id = ?", (cand_id,))
                row = cursor.fetchone()
            return self._candidate_row(row) if row else None
        except Exception as e:
            self.logger.error(f"Error getting footnote candidate: {e}")
            return None

    def get_footnote_scans(self, book_id):
        """{chapter_number: scan-row dict} for a book."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT book_id, chapter_number, model, content_hash,"
                    " n_found, scanned_at FROM footnote_scans WHERE book_id = ?",
                    (book_id,))
                rows = cursor.fetchall()
            cols = ("book_id", "chapter_number", "model", "content_hash",
                    "n_found", "scanned_at")
            return {r[1]: dict(zip(cols, r)) for r in rows}
        except Exception as e:
            self.logger.error(f"Error loading footnote scans: {e}")
            return {}

    def record_footnote_scan(self, book_id, chapter_number, chapter_title,
                             model, content_hash, found, scanned_at=None,
                             preserve_reviewed=False):
        """Persist one chapter's scan results in one transaction: replace the
        chapter's candidate rows and upsert its scan row. Zero-find chapters
        still get a scan row (the re-run guard). Each item of ``found`` is a
        dict with term_zh/term_en/body/sentence and an optional status
        (defaults to 'pending' — the import script passes reviewed statuses
        through). Returns True on success.

        ``preserve_reviewed`` keeps rows a human has already accepted or
        rejected, replacing only the pending ones, and drops an incoming
        candidate that duplicates a surviving decision. Without it a re-scan of
        a reviewed chapter silently discards that review — which was harmless
        while only first-time ingest scanned, and is not once a chapter is
        rescanned on every retranslation (scan_mode "translation") or by
        `footnote_scan.py --force`."""
        now = scanned_at or datetime.datetime.now().isoformat(timespec="seconds")
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                kept_keys = set()
                if preserve_reviewed:
                    cursor.execute(
                        "SELECT term_zh, term_en FROM footnote_candidates"
                        " WHERE book_id = ? AND chapter_number = ?"
                        " AND status <> 'pending'", (book_id, chapter_number))
                    for row in cursor.fetchall():
                        zh = (row[0] or "").strip()
                        en = (row[1] or "").strip().lower()
                        kept_keys.add(zh or en)
                    kept_keys.discard("")
                    cursor.execute(
                        "DELETE FROM footnote_candidates WHERE book_id = ?"
                        " AND chapter_number = ? AND status = 'pending'",
                        (book_id, chapter_number))
                else:
                    cursor.execute(
                        "DELETE FROM footnote_candidates WHERE book_id = ?"
                        " AND chapter_number = ?", (book_id, chapter_number))
                for f in found:
                    if kept_keys:
                        key = (f.get("term_zh") or "").strip() or \
                            (f.get("term_en") or "").strip().lower()
                        if key and key in kept_keys:
                            continue     # already decided; do not re-ask
                    status = f.get("status") or "pending"
                    if status not in _VALID_STATUSES:
                        status = "pending"
                    cursor.execute(
                        "INSERT INTO footnote_candidates (book_id, chapter_number,"
                        " chapter_title, term_zh, term_en, body, sentence, model,"
                        " status, created_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (book_id, chapter_number, chapter_title,
                         f.get("term_zh"), f.get("term_en"), f["body"],
                         f.get("sentence"), f.get("model") or model, status,
                         f.get("created_date") or now))
                # Upsert the scan row (delete+insert works on both backends).
                cursor.execute(
                    "DELETE FROM footnote_scans WHERE book_id = ?"
                    " AND chapter_number = ?", (book_id, chapter_number))
                # n_found is what the chapter now HOLDS, which under
                # preserve_reviewed is not the length of the incoming list.
                cursor.execute(
                    "SELECT COUNT(*) FROM footnote_candidates WHERE book_id = ?"
                    " AND chapter_number = ?", (book_id, chapter_number))
                n_found = (cursor.fetchone() or [0])[0] or 0
                cursor.execute(
                    "INSERT INTO footnote_scans (book_id, chapter_number, model,"
                    " content_hash, n_found, scanned_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (book_id, chapter_number, model, content_hash, n_found, now))
            return True
        except Exception as e:
            self.logger.error(f"Error recording footnote scan: {e}\n{traceback.format_exc()}")
            if self.strict_writes:
                raise
            return False

    def update_footnote_candidate(self, cand_id, status=None, term_en=None, body=None):
        """Review edit: any subset of status / term_en / body. Returns True
        when a row was updated."""
        if status is not None and status not in _VALID_STATUSES:
            self.logger.error(f"Invalid footnote candidate status: {status!r}")
            return False
        sets, params = [], []
        for col, val in (("status", status), ("term_en", term_en), ("body", body)):
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if not sets:
            return False
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE footnote_candidates SET {', '.join(sets)} WHERE id = ?",
                    params + [cand_id])
                return cursor.rowcount > 0
        except Exception as e:
            self.logger.error(f"Error updating footnote candidate: {e}")
            if self.strict_writes:
                raise
            return False

    def set_footnote_candidates_status(self, ids, status):
        """Batch status change. Returns rows updated."""
        if status not in _VALID_STATUSES or not ids:
            return 0
        try:
            placeholders = ",".join("?" * len(ids))
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE footnote_candidates SET status = ?"
                    f" WHERE id IN ({placeholders})", [status] + list(ids))
                return cursor.rowcount
        except Exception as e:
            self.logger.error(f"Error batch-updating footnote candidates: {e}")
            if self.strict_writes:
                raise
            return 0

    def delete_footnote_candidates(self, ids):
        """Delete candidate rows by id (dedupe/prune). Returns rows deleted."""
        if not ids:
            return 0
        try:
            placeholders = ",".join("?" * len(ids))
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"DELETE FROM footnote_candidates WHERE id IN ({placeholders})",
                    list(ids))
                return cursor.rowcount
        except Exception as e:
            self.logger.error(f"Error deleting footnote candidates: {e}")
            if self.strict_writes:
                raise
            return 0

    def update_footnote_scan_count(self, book_id, chapter_number):
        """Recompute a scan row's n_found after pruning its candidates."""
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE footnote_scans SET n_found = (SELECT COUNT(*) FROM"
                    " footnote_candidates WHERE book_id = ? AND chapter_number = ?)"
                    " WHERE book_id = ? AND chapter_number = ?",
                    (book_id, chapter_number, book_id, chapter_number))
            return True
        except Exception as e:
            self.logger.error(f"Error updating footnote scan count: {e}")
            return False

    def footnote_candidate_book_counts(self):
        """Per-book candidate counts for the review GUI's book list.

        Returns a list of {book_id, book_title, pending, accepted, rejected,
        total, chapters_scanned, last_scanned_at}, ordered by pending desc
        then book id, including only books that have candidates or scans.
        """
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT b.id, b.title,
                        COALESCE(SUM(CASE WHEN fc.status = 'pending' THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN fc.status = 'accepted' THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN fc.status = 'rejected' THEN 1 ELSE 0 END), 0),
                        COUNT(fc.id)
                    FROM books b
                    JOIN footnote_candidates fc ON fc.book_id = b.id
                    GROUP BY b.id, b.title
                """)
                cand_rows = cursor.fetchall()
                cursor.execute(
                    "SELECT book_id, COUNT(*), MAX(scanned_at)"
                    " FROM footnote_scans GROUP BY book_id")
                scan_rows = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}
            out = []
            for bid, title, pending, accepted, rejected, total in cand_rows:
                scanned, last = scan_rows.get(bid, (0, None))
                out.append({
                    "book_id": bid, "book_title": title,
                    "pending": int(pending), "accepted": int(accepted),
                    "rejected": int(rejected), "total": int(total),
                    "chapters_scanned": scanned, "last_scanned_at": last,
                })
            out.sort(key=lambda r: (-r["pending"], r["book_id"]))
            return out
        except Exception as e:
            self.logger.error(f"Error counting footnote candidates: {e}")
            return []
