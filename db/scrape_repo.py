import datetime
import json


def now_iso():
    """Naive server-local ISO timestamp, the convention the scrape tables share
    with translation_date / published_at (the admin UI renders it as local)."""
    return datetime.datetime.now().isoformat(timespec="seconds")


# Fields a caller may write on book_scrape. JSON fields are encoded here.
_BOOK_SCRAPE_FIELDS = {
    "mode", "auto_gap", "number_offset", "linked", "cursor_url", "cursor_number",
    "run_options", "last_toc", "toc_count", "toc_latest_title", "pending_count",
    "last_checked_at", "last_sweep_at", "last_new_at", "consecutive_failures",
    "last_error", "hold_reason", "unplaced", "unplaced_pending",
}
_BOOK_SCRAPE_JSON = {"run_options", "last_toc", "unplaced"}
# Big JSON blobs left out of ordinary reads.
_BOOK_SCRAPE_HEAVY = {"last_toc", "unplaced"}

_TASK_FIELDS = {
    "status", "priority", "from_url", "to_url", "max_chapters", "options",
    "scraper_job_id", "attempts", "not_before", "last_error", "vnc_url",
    "progress", "result_summary", "started_at", "finished_at",
}
_TASK_JSON = {"options", "progress", "result_summary"}

# A task in one of these states occupies the scraper lane.
LANE_STATES = ("running", "blocked", "cancelling")
OPEN_STATES = ("queued",) + LANE_STATES


def _lines(content):
    """Stored chapter/queue content -> list of lines (JSON array or plain text)."""
    if isinstance(content, list):
        return content
    if not content:
        return []
    if content.lstrip().startswith("["):
        try:
            val = json.loads(content)
            if isinstance(val, list):
                return [str(x) for x in val]
        except ValueError:
            pass
    return content.splitlines()


def _decode(row, json_fields):
    for f in json_fields:
        if f in row and isinstance(row[f], str) and row[f]:
            try:
                row[f] = json.loads(row[f])
            except ValueError:
                row[f] = None
    return row


class ScrapeRepo:
    """Automated source scraping: per-book config/state (book_scrape) and the
    single scraper lane (scrape_tasks). The worker is scrape_worker.py; the
    admin API (web/api/scrape_api.py) only writes rows for it to pick up."""

    # -- book_scrape ---------------------------------------------------------

    def get_book_scrape(self, book_id: int, include_toc: bool = False) -> dict:
        """The book's scrape row, or defaults (exists=False) if it has none."""
        cols = "*" if include_toc else ", ".join(
            ["book_id"] + sorted(_BOOK_SCRAPE_FIELDS - _BOOK_SCRAPE_HEAVY) + ["updated_at"])
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {cols} FROM book_scrape WHERE book_id = ?", (book_id,))
            row = cursor.fetchone()
            names = [d[0] for d in cursor.description]
        if not row:
            return {"book_id": book_id, "exists": False, "mode": "off", "auto_gap": None,
                    "number_offset": 0, "linked": 0, "cursor_url": None,
                    "cursor_number": None, "run_options": None, "last_toc": None,
                    "consecutive_failures": 0, "hold_reason": None,
                    "unplaced": None, "unplaced_pending": 0}
        out = _decode(dict(zip(names, row)), _BOOK_SCRAPE_JSON)
        out["exists"] = True
        return out

    def update_book_scrape(self, book_id: int, **fields):
        """Upsert the given book_scrape fields (unknown keys are ignored)."""
        vals = {}
        for k, v in fields.items():
            if k not in _BOOK_SCRAPE_FIELDS:
                continue
            if k in _BOOK_SCRAPE_JSON and v is not None and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            vals[k] = v
        vals["updated_at"] = now_iso()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM book_scrape WHERE book_id = ?", (book_id,))
            if cursor.fetchone():
                sets = ", ".join(f"{k} = ?" for k in vals)
                cursor.execute(f"UPDATE book_scrape SET {sets} WHERE book_id = ?",
                               list(vals.values()) + [book_id])
            else:
                cols = ["book_id"] + list(vals)
                cursor.execute(
                    f"INSERT INTO book_scrape ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [book_id] + list(vals.values()))

    def list_book_scrapes(self, book_id: int = None) -> list:
        """Every book that has a source URL or a scrape row, with its scrape
        state (minus the stored TOC) and both chapter heads: the highest
        translated chapter and the highest chapter we hold at all (saved or
        queued) — the gap rule needs the first, the overview shows both."""
        cols = ", ".join(f"s.{c}" for c in sorted(_BOOK_SCRAPE_FIELDS - _BOOK_SCRAPE_HEAVY - {"run_options"}))
        sql = (
            f"SELECT b.id AS book_id, b.title, b.source_url, b.status AS book_status, "
            f"s.book_id AS has_row, s.run_options, {cols}, "
            "(SELECT MAX(c.chapter_number) FROM chapters c WHERE c.book_id = b.id) AS translated_max, "
            "(SELECT MAX(q.chapter_number) FROM queue q WHERE q.book_id = b.id) AS queued_max, "
            "(SELECT COUNT(*) FROM queue q WHERE q.book_id = b.id) AS queued_count "
            "FROM books b LEFT JOIN book_scrape s ON s.book_id = b.id "
            "WHERE ((b.source_url IS NOT NULL AND b.source_url <> '') OR s.book_id IS NOT NULL)"
            + (" AND b.id = ?" if book_id is not None else "") + " ORDER BY b.id")
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (book_id,) if book_id is not None else ())
            names = [d[0] for d in cursor.description]
            rows = [dict(zip(names, r)) for r in cursor.fetchall()]
        for r in rows:
            r["exists"] = r.pop("has_row") is not None
            r["mode"] = r.get("mode") or "off"
            r["linked"] = r.get("linked") or 0
            r["number_offset"] = r.get("number_offset") or 0
            r["consecutive_failures"] = r.get("consecutive_failures") or 0
            r["unplaced_pending"] = r.get("unplaced_pending") or 0
            _decode(r, {"run_options"})
            heads = [h for h in (r["translated_max"], r["queued_max"]) if h is not None]
            r["have_max"] = max(heads) if heads else None
        return rows

    def max_translated_chapter(self, book_id: int):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(chapter_number) FROM chapters WHERE book_id = ?", (book_id,))
            row = cursor.fetchone()
        return row[0] if row else None

    def existing_chapter_numbers(self, book_id: int) -> set:
        """Chapter numbers already present for a book — saved chapters OR
        queue rows (queued or claimed)."""
        nums = set()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT chapter_number FROM chapters WHERE book_id = ?", (book_id,))
            nums.update(r[0] for r in cursor.fetchall() if isinstance(r[0], int))
            cursor.execute(
                "SELECT chapter_number FROM queue WHERE book_id = ? AND chapter_number IS NOT NULL",
                (book_id,))
            nums.update(r[0] for r in cursor.fetchall() if isinstance(r[0], int))
        return nums

    def recent_source_titles(self, book_id: int, limit: int = 8) -> dict:
        """{chapter_number: source title} for the book's highest chapters —
        what auto-linking matches against the TOC. Queue rows carry the source
        title; a saved chapter only has it as the first line of its source,
        when that line is short enough to be a title rather than prose."""
        out = {}
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT chapter_number, title FROM queue WHERE book_id = ? "
                "AND chapter_number IS NOT NULL ORDER BY chapter_number DESC LIMIT ?",
                (book_id, limit))
            for n, title in cursor.fetchall():
                if title and title != "Untitled":
                    out.setdefault(n, title)
            cursor.execute(
                "SELECT chapter_number, untranslated_content FROM chapters WHERE book_id = ? "
                "ORDER BY chapter_number DESC LIMIT ?", (book_id, limit))
            for n, content in cursor.fetchall():
                if n in out or not content:
                    continue
                try:
                    lines = json.loads(content) if content.lstrip().startswith("[") else content.splitlines()
                except ValueError:
                    lines = content.splitlines()
                first = next((ln.strip() for ln in lines if isinstance(ln, str) and ln.strip()), "")
                if 0 < len(first) <= 40:
                    out[n] = first
        return out

    # -- unplaced entries + the two places "append" can land -------------------

    def get_unplaced(self, book_id: int) -> list:
        state = self.get_book_scrape(book_id, include_toc=True)
        return list(state.get("unplaced") or [])

    def set_unplaced(self, book_id: int, items: list):
        self.update_book_scrape(book_id, unplaced=items,
                                unplaced_pending=sum(1 for i in items if i.get("status") == "pending"))

    def queue_row_for_chapter(self, book_id: int, chapter_number: int):
        """(queue id, status, content lines) of the queue row holding this chapter, or None."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, status, content FROM queue WHERE book_id = ? AND chapter_number = ? "
                           "ORDER BY id LIMIT 1", (book_id, chapter_number))
            row = cursor.fetchone()
        if not row:
            return None
        return row[0], row[1] or "queued", _lines(row[2])

    def set_queue_content(self, queue_id: int, lines: list):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE queue SET content = ? WHERE id = ?",
                           (json.dumps(lines, ensure_ascii=False), queue_id))

    def chapter_source(self, book_id: int, chapter_number: int):
        """(title, source lines) of a saved chapter, or None."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT title, untranslated_content FROM chapters "
                           "WHERE book_id = ? AND chapter_number = ?", (book_id, chapter_number))
            row = cursor.fetchone()
        return (row[0], _lines(row[1])) if row else None

    def set_chapter_source(self, book_id: int, chapter_number: int, lines: list):
        """Rewrite a saved chapter's source text only (the translation is left as is)."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE chapters SET untranslated_content = ? WHERE book_id = ? AND chapter_number = ?",
                           (json.dumps(lines, ensure_ascii=False), book_id, chapter_number))

    def latest_translation_model(self, book_id: int):
        """The model the book's most recent chapter was translated with."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT translation_model FROM chapters WHERE book_id = ? "
                "AND translation_model IS NOT NULL AND translation_model <> '' "
                "ORDER BY translation_date DESC LIMIT 1", (book_id,))
            row = cursor.fetchone()
        return row[0] if row else None

    # -- scrape_tasks --------------------------------------------------------

    def create_scrape_task(self, book_id: int, kind: str, *, reason: str = None,
                           priority: int = 50, from_url: str = None, to_url: str = None,
                           max_chapters: int = None, options: dict = None,
                           not_before: str = None) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO scrape_tasks (book_id, kind, reason, priority, status, from_url, "
                "to_url, max_chapters, options, not_before, created_at) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
                (book_id, kind, reason, priority, from_url, to_url, max_chapters,
                 json.dumps(options, ensure_ascii=False) if options else None,
                 not_before, now_iso()))
            return cursor.lastrowid

    def update_scrape_task(self, task_id: int, **fields):
        vals = {}
        for k, v in fields.items():
            if k not in _TASK_FIELDS:
                continue
            if k in _TASK_JSON and v is not None and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            vals[k] = v
        if not vals:
            return
        with self._conn() as conn:
            cursor = conn.cursor()
            sets = ", ".join(f"{k} = ?" for k in vals)
            cursor.execute(f"UPDATE scrape_tasks SET {sets} WHERE id = ?",
                           list(vals.values()) + [task_id])

    def _select_tasks(self, where: str, params, order: str = "t.id DESC", limit: int = None):
        sql = (f"SELECT t.*, b.title AS book_title FROM scrape_tasks t "
               f"LEFT JOIN books b ON b.id = t.book_id WHERE {where} ORDER BY {order}")
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            names = [d[0] for d in cursor.description]
            return [_decode(dict(zip(names, r)), _TASK_JSON) for r in cursor.fetchall()]

    def get_scrape_task(self, task_id: int):
        rows = self._select_tasks("t.id = ?", (task_id,))
        return rows[0] if rows else None

    def find_scrape_task_by_job(self, scraper_job_id: str):
        """The task a scraper job belongs to (newest first: a retried job keeps
        its id, a resubmitted one gets a new one)."""
        rows = self._select_tasks("t.scraper_job_id = ?", (scraper_job_id,), limit=1)
        return rows[0] if rows else None

    def list_scrape_tasks(self, statuses=None, book_id: int = None, limit: int = 50,
                          order: str = "t.id DESC") -> list:
        where, params = ["1=1"], []
        if statuses:
            where.append(f"t.status IN ({', '.join('?' * len(statuses))})")
            params += list(statuses)
        if book_id is not None:
            where.append("t.book_id = ?")
            params.append(book_id)
        return self._select_tasks(" AND ".join(where), params, order=order, limit=limit)

    def lane_task(self):
        """The task currently on the scraper (at most one, by construction)."""
        rows = self._select_tasks(
            f"t.kind IN ('toc', 'chapters') AND t.status IN ({', '.join('?' * len(LANE_STATES))})",
            LANE_STATES, order="t.id", limit=1)
        return rows[0] if rows else None

    def next_scrape_task(self, now: str):
        """Next queued scraper task that is due: lowest priority, then oldest."""
        rows = self._select_tasks(
            "t.kind IN ('toc', 'chapters') AND t.status = 'queued' "
            "AND (t.not_before IS NULL OR t.not_before <= ?)",
            (now,), order="t.priority, t.id", limit=1)
        return rows[0] if rows else None

    def due_autostart_tasks(self, now: str) -> list:
        return self._select_tasks(
            "t.kind = 'autostart' AND t.status = 'queued' "
            "AND (t.not_before IS NULL OR t.not_before <= ?)",
            (now,), order="t.id")

    def has_open_scrape_task(self, book_id: int, kinds=("toc", "chapters")) -> bool:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) FROM scrape_tasks WHERE book_id = ? "
                f"AND kind IN ({', '.join('?' * len(kinds))}) "
                f"AND status IN ({', '.join('?' * len(OPEN_STATES))})",
                [book_id] + list(kinds) + list(OPEN_STATES))
            return cursor.fetchone()[0] > 0

    def count_scrape_attention(self, failure_limit: int) -> int:
        """Nav badge: a job blocked on a challenge, plus switched-on books that
        are failing or held for review."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM scrape_tasks WHERE status = 'blocked'")
            blocked = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM book_scrape WHERE mode <> 'off' "
                "AND (consecutive_failures >= ? OR hold_reason IS NOT NULL OR unplaced_pending > 0)",
                (failure_limit,))
            books = cursor.fetchone()[0]
        return blocked + books
