import datetime
import json

from db.scrape_repo import _decode, now_iso

# Fields a caller may write on book_nu. JSON fields are encoded here.
_BOOK_NU_FIELDS = {
    "mode", "state", "nu_slug", "nu_url", "nu_series_id", "start_chapter",
    "candidates", "search_names", "series_draft", "draft_dry_run_at", "draft_dry_run",
    "first_release_label", "first_release_at", "active_since", "last_release_label",
    "last_release_at", "checked_at", "hold_reason", "hold_detail", "alerted_at",
    "consecutive_failures", "last_error",
}
_BOOK_NU_JSON = {"candidates", "search_names", "series_draft", "draft_dry_run"}

_RELEASE_FIELDS = {"label", "link", "status", "nu_task_id", "submitted_at", "message", "error"}
# A chapter in one of these is done: posted by us, already on NU, or posted by hand.
RELEASE_DONE = ("sent", "exists", "manual")

_TASK_FIELDS = {
    "status", "priority", "options", "scraper_job_id", "attempts", "not_before",
    "last_error", "vnc_url", "progress", "result", "started_at", "finished_at",
}
_TASK_JSON = {"options", "progress", "result"}

# Same lane vocabulary as scrape_tasks.
NU_LANE_STATES = ("running", "blocked", "cancelling")
NU_OPEN_STATES = ("queued",) + NU_LANE_STATES

_DEFAULTS = {
    "mode": "off", "state": "untracked", "nu_slug": None, "nu_url": None,
    "nu_series_id": None, "start_chapter": None, "candidates": None, "search_names": None,
    "series_draft": None, "draft_dry_run_at": None, "draft_dry_run": None,
    "first_release_label": None, "first_release_at": None, "active_since": None,
    "last_release_label": None, "last_release_at": None, "checked_at": None,
    "hold_reason": None, "hold_detail": None, "alerted_at": None,
    "consecutive_failures": 0, "last_error": None, "updated_at": None,
}


def _published_now():
    return datetime.datetime.now().isoformat()


class NuRepo:
    """NovelUpdates posting: per-book state (book_nu), what we posted
    (nu_releases) and the NU lane (nu_tasks). The worker is nu_sync.NuEngine,
    ticked by scrape_worker.py; the admin API (web/api/nu_api.py) writes rows
    for it to pick up."""

    # -- book_nu -------------------------------------------------------------

    def get_book_nu(self, book_id: int) -> dict:
        """The book's NU row, or defaults (exists=False) if it has none."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM book_nu WHERE book_id = ?", (book_id,))
            row = cursor.fetchone()
            names = [d[0] for d in cursor.description]
        if not row:
            return {"book_id": book_id, "exists": False, **_DEFAULTS}
        out = _decode(dict(zip(names, row)), _BOOK_NU_JSON)
        out["consecutive_failures"] = out.get("consecutive_failures") or 0
        out["exists"] = True
        return out

    def update_book_nu(self, book_id: int, **fields):
        """Upsert the given book_nu fields (unknown keys are ignored)."""
        vals = {}
        for k, v in fields.items():
            if k not in _BOOK_NU_FIELDS:
                continue
            if k in _BOOK_NU_JSON and v is not None and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            vals[k] = v
        vals["updated_at"] = now_iso()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM book_nu WHERE book_id = ?", (book_id,))
            if cursor.fetchone():
                sets = ", ".join(f"{k} = ?" for k in vals)
                cursor.execute(f"UPDATE book_nu SET {sets} WHERE book_id = ?",
                               list(vals.values()) + [book_id])
            else:
                cols = ["book_id"] + list(vals)
                cursor.execute(
                    f"INSERT INTO book_nu ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [book_id] + list(vals.values()))

    def list_book_nu(self, book_id: int = None, states=None) -> list:
        """Every translated book with its NU state (defaults when it has no row),
        plus what the planner and the overview need: public flag, titles, the
        newest published chapter and the last chapter we got onto NU."""
        now = _published_now()
        where = ["b.is_original = 0"]
        params = [now]
        if book_id is not None:
            where.append("b.id = ?")
            params.append(book_id)
        if states:
            where.append(f"COALESCE(n.state, 'untracked') IN ({', '.join('?' * len(states))})")
            params += list(states)
        sql = (
            "SELECT b.id AS book_id, b.title, b.source_title, b.nu_title, b.is_public, "
            "b.status AS book_status, n.*, "
            "(SELECT MAX(c.chapter_number) FROM chapters c WHERE c.book_id = b.id "
            " AND c.published_at IS NOT NULL AND c.published_at <= ?) AS published_max "
            "FROM books b LEFT JOIN book_nu n ON n.book_id = b.id "
            f"WHERE {' AND '.join(where)} ORDER BY b.id")
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            names = [d[0] for d in cursor.description]
            rows = []
            for r in cursor.fetchall():
                # n.book_id shadows b.id (NULL with no row) — keep b.id.
                d = {}
                for name, val in zip(names, r):
                    if name == "book_id" and "book_id" in d:
                        d["has_row"] = val is not None
                        continue
                    d[name] = val
                rows.append(d)
        for r in rows:
            r["exists"] = r.pop("has_row", False)
            for k, v in _DEFAULTS.items():
                if r.get(k) is None:
                    r[k] = v
            _decode(r, _BOOK_NU_JSON)
            r["is_public"] = bool(r.get("is_public")) if r.get("is_public") is not None else True
        return rows

    # -- chapters & releases -------------------------------------------------

    def published_chapters_from(self, book_id: int, start: int, limit: int = 1) -> list:
        """Published chapters numbered >= start that have no finished release
        row, lowest first: [{chapter, published_at}]. Gaps in numbering are
        simply absent — nothing is invented."""
        done = ", ".join("?" * len(RELEASE_DONE))
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT c.chapter_number, c.published_at FROM chapters c "
                "WHERE c.book_id = ? AND c.chapter_number >= ? "
                "AND c.published_at IS NOT NULL AND c.published_at <= ? "
                "AND NOT EXISTS (SELECT 1 FROM nu_releases r WHERE r.book_id = c.book_id "
                f"  AND r.chapter_number = c.chapter_number AND r.status IN ({done})) "
                f"ORDER BY c.chapter_number LIMIT {int(limit)}",
                [book_id, start, _published_now()] + list(RELEASE_DONE))
            return [{"chapter": n, "published_at": p} for n, p in cursor.fetchall()]

    def count_unsent_published(self, book_id: int, start: int) -> int:
        done = ", ".join("?" * len(RELEASE_DONE))
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM chapters c WHERE c.book_id = ? AND c.chapter_number >= ? "
                "AND c.published_at IS NOT NULL AND c.published_at <= ? "
                "AND NOT EXISTS (SELECT 1 FROM nu_releases r WHERE r.book_id = c.book_id "
                f"  AND r.chapter_number = c.chapter_number AND r.status IN ({done}))",
                [book_id, start, _published_now()] + list(RELEASE_DONE))
            return cursor.fetchone()[0]

    def chapter_published_at(self, book_id: int, chapter_number: int):
        """The chapter's published_at if it is live now, else None (missing,
        draft, or scheduled for later)."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT published_at FROM chapters WHERE book_id = ? AND chapter_number = ? "
                "AND published_at IS NOT NULL AND published_at <= ?",
                (book_id, chapter_number, _published_now()))
            row = cursor.fetchone()
        return row[0] if row else None

    def first_unpublished_chapter(self, book_id: int) -> int:
        """The baseline the UI prefills: one past the newest published chapter
        (so only chapters published from now on are posted). 1 for an empty book."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT MAX(chapter_number) FROM chapters WHERE book_id = ? "
                "AND published_at IS NOT NULL AND published_at <= ?",
                (book_id, _published_now()))
            row = cursor.fetchone()
        return (row[0] or 0) + 1

    def get_nu_release(self, book_id: int, chapter_number: int):
        rows = self.list_nu_releases(book_id, chapter_number=chapter_number)
        return rows[0] if rows else None

    def upsert_nu_release(self, book_id: int, chapter_number: int, **fields):
        vals = {k: v for k, v in fields.items() if k in _RELEASE_FIELDS}
        vals["updated_at"] = now_iso()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM nu_releases WHERE book_id = ? AND chapter_number = ?",
                           (book_id, chapter_number))
            row = cursor.fetchone()
            if row:
                sets = ", ".join(f"{k} = ?" for k in vals)
                cursor.execute(f"UPDATE nu_releases SET {sets} WHERE id = ?",
                               list(vals.values()) + [row[0]])
            else:
                vals.setdefault("label", f"c{chapter_number}")
                vals.setdefault("status", "queued")
                vals["created_at"] = vals["updated_at"]
                cols = ["book_id", "chapter_number"] + list(vals)
                cursor.execute(
                    f"INSERT INTO nu_releases ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [book_id, chapter_number] + list(vals.values()))

    def list_nu_releases(self, book_id: int, chapter_number: int = None, statuses=None,
                         limit: int = 50) -> list:
        where, params = ["book_id = ?"], [book_id]
        if chapter_number is not None:
            where.append("chapter_number = ?")
            params.append(chapter_number)
        if statuses:
            where.append(f"status IN ({', '.join('?' * len(statuses))})")
            params += list(statuses)
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM nu_releases WHERE {' AND '.join(where)} "
                           f"ORDER BY chapter_number DESC LIMIT {int(limit)}", params)
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, r)) for r in cursor.fetchall()]

    # -- nu_tasks ------------------------------------------------------------

    def create_nu_task(self, book_id, kind: str, *, priority: int = 50, options: dict = None,
                       not_before: str = None) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO nu_tasks (book_id, kind, priority, status, options, not_before, created_at) "
                "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                (book_id, kind, priority,
                 json.dumps(options, ensure_ascii=False) if options else None,
                 not_before, now_iso()))
            return cursor.lastrowid

    def update_nu_task(self, task_id: int, **fields):
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
            cursor.execute(f"UPDATE nu_tasks SET {sets} WHERE id = ?", list(vals.values()) + [task_id])

    def _select_nu_tasks(self, where: str, params, order: str = "t.id DESC", limit: int = None):
        sql = (f"SELECT t.*, b.title AS book_title FROM nu_tasks t "
               f"LEFT JOIN books b ON b.id = t.book_id WHERE {where} ORDER BY {order}")
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            names = [d[0] for d in cursor.description]
            return [_decode(dict(zip(names, r)), _TASK_JSON) for r in cursor.fetchall()]

    def get_nu_task(self, task_id: int):
        rows = self._select_nu_tasks("t.id = ?", (task_id,))
        return rows[0] if rows else None

    def list_nu_tasks(self, statuses=None, book_id: int = None, kinds=None, limit: int = 50,
                      order: str = "t.id DESC") -> list:
        where, params = ["1=1"], []
        if statuses:
            where.append(f"t.status IN ({', '.join('?' * len(statuses))})")
            params += list(statuses)
        if book_id is not None:
            where.append("t.book_id = ?")
            params.append(book_id)
        if kinds:
            where.append(f"t.kind IN ({', '.join('?' * len(kinds))})")
            params += list(kinds)
        return self._select_nu_tasks(" AND ".join(where), params, order=order, limit=limit)

    def nu_lane_task(self):
        """The NU task currently on the scraper (at most one, by construction)."""
        rows = self._select_nu_tasks(
            f"t.status IN ({', '.join('?' * len(NU_LANE_STATES))})",
            NU_LANE_STATES, order="t.id", limit=1)
        return rows[0] if rows else None

    def next_nu_task(self, now: str):
        rows = self._select_nu_tasks(
            "t.status = 'queued' AND (t.not_before IS NULL OR t.not_before <= ?)",
            (now,), order="t.priority, t.id", limit=1)
        return rows[0] if rows else None

    def open_nu_task(self, book_id, kind: str):
        """An open (queued or in-flight) task of this kind for the book; book_id
        None matches the global kinds (sync, vocab)."""
        where = "t.kind = ? AND " + ("t.book_id IS NULL" if book_id is None else "t.book_id = ?")
        params = [kind] + ([] if book_id is None else [book_id])
        rows = self._select_nu_tasks(
            f"{where} AND t.status IN ({', '.join('?' * len(NU_OPEN_STATES))})",
            params + list(NU_OPEN_STATES), limit=1)
        return rows[0] if rows else None

    def last_finished_nu_task(self, kind: str, book_id=None):
        where = "t.kind = ? AND t.status = 'done'" + (" AND t.book_id = ?" if book_id is not None else "")
        rows = self._select_nu_tasks(where, [kind] + ([book_id] if book_id is not None else []),
                                     order="t.finished_at DESC", limit=1)
        return rows[0] if rows else None

    def count_nu_attention(self) -> int:
        """Nav badge: books held or waiting on a human, plus a blocked NU job."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM nu_tasks WHERE status = 'blocked'")
            blocked = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM book_nu WHERE hold_reason IS NOT NULL "
                "OR state IN ('needs_review', 'new_series', 'series_submitted')")
            books = cursor.fetchone()[0]
        return blocked + books
