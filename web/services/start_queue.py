"""The translation start queue: every start waits here for a free job slot.

"Process Next" / "Start auto-process" / "Process All Books" (Queue page),
"Translate" (Dashboard), the MCP resume tool, start_auto_process.py and the
scrape worker's auto-starts all go through ``submit``. When a slot is free and
nothing is ahead, the request starts at once, exactly as before. Otherwise
it waits in ``translation_requests`` (persisted, so an admin restart loses
nothing) and starts when a job ends.

Order is priority, then age. A *priority* start (the checkbox on the start
forms) jumps every normal one and takes the next free slot; everything else
is first come, first served, scraper auto-starts included. Every source uses
the whole cap. A waiting request whose book is already translating is passed
over, not blocking the requests behind it: one job per book is the
registry's rule, and that book's run ends eventually.

The dispatcher runs:
- inline in ``submit`` (so an idle system starts the request in the same call),
- on a background thread woken by every slot release (JobRegistry listener),
  and every RECHECK_SECONDS as a backstop (a raised cap in Settings, a
  restart with requests left over).

Starting is done by the same functions the endpoints always used (the
"starters", registered by app_factory), so a queued start behaves exactly
like an immediate one. A 409 from a starter means "not now" (the slot went to
a direct ``queue=false`` start, say) and the request keeps its place; any
other refusal drops it, logged to the activity log.
"""
import logging
import threading

from db.translation_requests_repo import PRIORITY_HIGH, PRIORITY_NORMAL

log = logging.getLogger(__name__)

KINDS = ("queue", "text")
SOURCES = ("manual", "scrape")


class StartQueue:
    RECHECK_SECONDS = 30

    def __init__(self, registry, hub):
        self.registry = registry
        self.hub = hub
        self.db = None
        self._starters = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._dirty = False
        self._thread = None
        registry.add_release_listener(self.notify)

    def configure(self, db, starters):
        """Point at the DB and the per-kind start functions: kind ->
        callable(options) -> response dict, raising HTTPException to refuse."""
        self.db = db
        self._starters = dict(starters)

    # -- background dispatcher ---------------------------------------------------

    def notify(self):
        """Something changed (a slot freed): wake the dispatcher. Never blocks."""
        self._wake.set()

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="start-queue")
        self._thread.start()
        self.notify()   # requests left over from before a restart

    def _loop(self):
        while True:
            self._wake.wait(self.RECHECK_SECONDS)
            self._wake.clear()
            try:
                with self._lock:
                    outcomes = self._dispatch_locked()
                if outcomes:
                    self._dirty = True
            except Exception as e:
                log.exception("start queue dispatch failed: %s", e)
            if self._dirty:
                self._dirty = False
                self._broadcast()

    def _changed(self):
        # Broadcast from the dispatcher thread, never the caller's: an async
        # endpoint calling send_message_sync would block its own event loop.
        self._dirty = True
        self._wake.set()

    def _broadcast(self):
        try:
            self.hub.send_message_sync({"type": "translation_queue_changed"})
        except Exception:
            pass

    # -- public API ---------------------------------------------------------------

    def submit(self, kind, book_id, options, *, source="manual", priority=False):
        """Start now if a slot is free and nothing is ahead; otherwise wait.

        Returns the starter's response when it started, or
        ``{"status": "queued", "request_id", "position", "book_id"}``.
        Re-raises the starter's HTTPException if this request was refused for
        a reason other than "busy" (e.g. 404, nothing queued for the book).
        """
        if kind not in KINDS:
            raise ValueError(f"unknown start kind {kind!r}")
        source = source if source in SOURCES else "manual"
        with self._lock:
            request_id, fresh = self._enqueue(kind, book_id, options, source, priority)
            outcomes = self._dispatch_locked(just_submitted=request_id)
            position = self._position(request_id)
        self._changed()

        outcome = outcomes.get(request_id)
        if outcome is not None:
            state, value = outcome
            if state == "started":
                return value
            raise value
        if fresh:
            self._log_queued(kind, book_id, position, priority)
        return {"status": "queued", "request_id": request_id, "position": position,
                "book_id": book_id, "priority": bool(priority)}

    def list(self):
        """Waiting requests for the UI, with positions. Never the pasted text."""
        rows = self.db.list_translation_requests() if self.db else []
        out = []
        for i, row in enumerate(rows, 1):
            opts = row.get("options") or {}
            out.append({
                "id": row["id"],
                "position": i,
                "kind": row["kind"],
                "book_id": row["book_id"],
                "book_title": row.get("book_title"),
                "source": row["source"],
                "priority": row["priority"] <= PRIORITY_HIGH,
                "created_at": row["created_at"],
                "last_error": row.get("last_error"),
                "chapter_number": opts.get("chapter_number"),
                "auto_process": bool(opts.get("auto_process")),
                "max_chapters": opts.get("max_chapters"),
                "translation_model": opts.get("translation_model") or opts.get("model"),
            })
        return out

    def remove(self, request_id) -> bool:
        removed = self.db.delete_translation_request(request_id)
        if removed:
            self._changed()
        return removed

    def set_priority(self, request_id, priority: bool) -> bool:
        if not self.db.get_translation_request(request_id):
            return False
        self.db.update_translation_request(
            request_id, priority=PRIORITY_HIGH if priority else PRIORITY_NORMAL)
        self._changed()
        return True

    def drop_for_book(self, book_id=None, reason="stopped"):
        """Forget waiting requests for one book, or every one with book_id=None.
        Called by cancel / stop-auto: "stop" must not be followed by the same
        book starting again off a request that was waiting behind it."""
        if self.db is None:
            return []
        rows = self.db.list_translation_requests(book_id=book_id)
        dropped = [r["id"] for r in rows if self.db.delete_translation_request(r["id"])]
        if dropped:
            self._changed()
            try:
                self.hub.log_activity(
                    type="info",
                    message=f"Removed {len(dropped)} waiting translation start(s) — {reason}.",
                    book_id=book_id)
            except Exception:
                pass
        return dropped

    # -- internals -----------------------------------------------------------------

    def _enqueue(self, kind, book_id, options, source, priority):
        """(request id, is_new). One waiting queue run per book: a second
        request for a book that is already waiting updates it in place (newest
        options win; priority and a manual source are kept once given)."""
        level = PRIORITY_HIGH if priority else PRIORITY_NORMAL
        if kind == "queue" and book_id is not None:
            for row in self.db.list_translation_requests(book_id=book_id):
                if row["kind"] != "queue":
                    continue
                if source == "scrape" and row["source"] == "manual":
                    # A manual request already covers it; keep its options.
                    return row["id"], False
                self.db.update_translation_request(
                    row["id"], options=options, source=source,
                    priority=min(level, row["priority"]), last_error=None)
                return row["id"], False
        return self.db.add_translation_request(
            kind, book_id, options, source=source, priority=level), True

    def _position(self, request_id):
        for i, row in enumerate(self.db.list_translation_requests(), 1):
            if row["id"] == request_id:
                return i
        return None

    def _busy(self, row, running):
        # A "whatever is next" queue run (no book) picks an idle book itself;
        # the None key in `running` is the bookless Dashboard job, not it.
        if row["kind"] == "queue" and row["book_id"] is None:
            return False
        return row["book_id"] in running

    def _dispatch_locked(self, just_submitted=None):
        """Start waiting requests, in order, while slots are free.
        Returns {request_id: ("started", response) | ("dropped", exception)}."""
        from fastapi import HTTPException

        if self.db is None:
            return {}
        outcomes = {}
        for row in self.db.list_translation_requests():
            if len(self.registry.active()) >= self.registry.max_concurrent():
                break
            if self._busy(row, self.registry.running_book_ids()):
                continue
            rid, book_id = row["id"], row["book_id"]
            if row["source"] == "scrape" and book_id is not None:
                import scrape_sync
                ok, why = scrape_sync.autostart_eligibility(self.db, book_id)
                if not ok:
                    self.db.delete_translation_request(rid)
                    outcomes[rid] = ("dropped", HTTPException(status_code=409, detail=why))
                    self._log_dropped(row, f"auto-translate no longer eligible: {why}")
                    continue
            starter = self._starters.get(row["kind"])
            if starter is None:
                continue
            try:
                response = starter(dict(row.get("options") or {}))
            except HTTPException as e:
                if e.status_code == 409:
                    self.db.update_translation_request(rid, last_error=str(e.detail))
                    continue
                self.db.delete_translation_request(rid)
                outcomes[rid] = ("dropped", e)
                if rid != just_submitted:
                    self._log_dropped(row, e.detail)
                continue
            except Exception as e:
                log.exception("queued translation start %s failed", rid)
                self.db.delete_translation_request(rid)
                outcomes[rid] = ("dropped", HTTPException(status_code=500, detail=str(e)))
                if rid != just_submitted:
                    self._log_dropped(row, str(e))
                continue
            self.db.delete_translation_request(rid)
            outcomes[rid] = ("started", response)
        return outcomes

    def _book_name(self, book_id):
        if book_id is None:
            return "No book"
        try:
            book = self.db.get_book(book_id)
            return (book or {}).get("title") or f"Book {book_id}"
        except Exception:
            return f"Book {book_id}"

    def _log_queued(self, kind, book_id, position, priority):
        what = "Translation" if kind == "text" else "Queue run"
        try:
            self.hub.log_activity(
                type="info",
                message=(f"{what} for {self._book_name(book_id)} waiting for a free slot"
                         f"{' (priority)' if priority else ''} — position {position}."),
                book_id=book_id)
        except Exception:
            pass

    def _log_dropped(self, row, why):
        try:
            self.hub.log_activity(
                type="info",
                message=f"Waiting translation for {self._book_name(row['book_id'])} not started: {why}",
                book_id=row["book_id"])
        except Exception:
            pass
