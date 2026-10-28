import datetime
import json

# Lower runs first. A "priority" start jumps every normal one and takes the
# next free slot; within a level it is first come, first served.
PRIORITY_HIGH = 0
PRIORITY_NORMAL = 10


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


class TranslationRequestsRepo:
    """Translation starts waiting for a free job slot. The dispatcher is
    web/services/start_queue.py; a row lives only while it waits."""

    def add_translation_request(self, kind: str, book_id, options: dict, *,
                                source: str = "manual", priority: int = PRIORITY_NORMAL) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO translation_requests (book_id, kind, source, priority, options, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (book_id, kind, source, int(priority),
                 json.dumps(options or {}, ensure_ascii=False), _now_iso()))
            return cursor.lastrowid

    def update_translation_request(self, request_id: int, **fields):
        vals = {}
        for k, v in fields.items():
            if k not in ("options", "priority", "source", "last_error"):
                continue
            if k == "options" and v is not None and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            vals[k] = v
        if not vals:
            return
        with self._conn() as conn:
            cursor = conn.cursor()
            sets = ", ".join(f"{k} = ?" for k in vals)
            cursor.execute(f"UPDATE translation_requests SET {sets} WHERE id = ?",
                           list(vals.values()) + [request_id])

    def list_translation_requests(self, book_id=None) -> list:
        """Waiting starts in run order (priority, then age), book title joined."""
        sql = ("SELECT r.*, b.title AS book_title FROM translation_requests r "
               "LEFT JOIN books b ON b.id = r.book_id")
        params = []
        if book_id is not None:
            sql += " WHERE r.book_id = ?"
            params.append(book_id)
        sql += " ORDER BY r.priority, r.id"
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            names = [d[0] for d in cursor.description]
            rows = [dict(zip(names, r)) for r in cursor.fetchall()]
        for row in rows:
            try:
                row["options"] = json.loads(row["options"]) if row.get("options") else {}
            except ValueError:
                row["options"] = {}
        return rows

    def get_translation_request(self, request_id: int):
        return next((r for r in self.list_translation_requests() if r["id"] == request_id), None)

    def delete_translation_request(self, request_id: int) -> bool:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM translation_requests WHERE id = ?", (request_id,))
            return cursor.rowcount > 0
