"""
Translation job state for the web interface.

Split into two objects so several books can translate at once:

- :class:`JobHub` — process-wide. Owns the WebSocket registry, the replay
  buffer and the activity log. There is exactly one, shared by every job.
- :class:`Job` — one translation run, at most one per book. Owns the status,
  the interactive-prompt handshakes, the cancel flag and the auto-process
  counters, i.e. everything that used to be a field on the old singleton.

A `Job` delegates every hub method (`send_message_sync`, `log_activity`,
`add_websocket`, …), so it is a drop-in for the old `JobManager` and the API
routers keep working unchanged. It also stamps `book_id`/`job_id` onto every
message it broadcasts, which is what lets the frontend route events to the
right book once more than one job can be live.

Translation runs in a background thread (because it makes blocking HTTP calls).
The hub bridges that thread with the async FastAPI event loop via
asyncio.run_coroutine_threadsafe(); a Job parks its thread on threading.Events
while it waits for a human to answer a prompt.
"""
import asyncio
import itertools
import threading
from collections import deque
from typing import Optional


class JobHub:
    """Process-wide WebSocket fan-out, replay buffer and activity log.

    Deliberately holds no per-run state: connections outlive any single job,
    so a job ending must never orphan a connected client.
    """

    # Message types NOT kept in the replay buffer: activity_log entries are
    # persisted in the DB and refetched by the frontend on load, and progress
    # ticks are high-frequency transient state that the status endpoint
    # already restores — replaying stale ones would just flicker the UI.
    # (Progress matters more now: N concurrent jobs emit N streams of it.)
    _NO_REPLAY_TYPES = {"activity_log", "progress"}

    # The events that end a run, as the frontend reads them. Named separately
    # because a job parking on a prompt retracts them (see Job.await_prompt):
    # a run blocked on a human has provably not ended.
    _TERMINAL_TYPES = {
        "translation_complete",
        "translation_cancelled",
        "error",
    }

    # Terminal job outcomes. Only the newest of each type *per book* stays in
    # the replay buffer — an older completion carries no state a reconnecting
    # client can still act on, and the buffer otherwise fills with a
    # process-lifetime backlog of them. Collapsing per (type, book_id) rather
    # than per type is what keeps book A's completion from evicting book B's
    # before a reconnecting tab has seen it.
    _COLLAPSE_REPLAY_TYPES = _TERMINAL_TYPES | {"auto_process_done"}

    def __init__(self):
        self.db_manager = None  # Set by app_factory after DatabaseManager is created

        self.websockets: set = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_lock = threading.Lock()
        # Recent low-frequency events (completion, errors, review prompts),
        # replayed to reconnecting clients so e.g. a translation_complete
        # fired with no tab open isn't lost.
        self._replay: deque = deque(maxlen=100)
        self._seq = 0

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    def add_websocket(self, websocket, loop: asyncio.AbstractEventLoop) -> list:
        """Register a client socket. Returns buffered messages for catch-up replay."""
        with self._ws_lock:
            self.websockets.add(websocket)
            self.loop = loop
            return list(self._replay)

    def remove_websocket(self, websocket):
        """Deregister exactly this socket (no-op if already gone).

        A stale disconnect can only remove itself — it can never silence a
        newer live connection, and a second tab never overwrites the first.
        """
        with self._ws_lock:
            self.websockets.discard(websocket)

    def set_websocket(self, websocket, loop: asyncio.AbstractEventLoop):
        """Legacy alias for add_websocket (kept for compatibility)."""
        self.add_websocket(websocket, loop)

    def _buffer(self, message: dict) -> dict:
        """Retain low-frequency events for replay to (re)connecting clients.

        Returns the message to actually broadcast: a buffered event is stamped
        with a monotonic ``seq`` and the *same stamped copy* goes out live, so
        a client can tell a replayed event it already saw from one it missed
        while its socket was down. Un-buffered types are returned unchanged
        (they are never replayed, so they need no identity).
        """
        mtype = message.get("type")
        if mtype in self._NO_REPLAY_TYPES:
            return message
        with self._ws_lock:
            self._seq += 1
            if mtype in self._COLLAPSE_REPLAY_TYPES:
                # A reconnecting client needs the latest outcome per book, not
                # every one since process start.
                book_id = message.get("book_id")
                keep = [
                    m for m in self._replay
                    if not (m.get("type") == mtype and m.get("book_id") == book_id)
                ]
                self._replay.clear()
                self._replay.extend(keep)
            stamped = {**message, "seq": self._seq}
            self._replay.append(stamped)
        return stamped

    def _drop_replay(self, *types: str, book_id=None):
        """Remove buffered messages of the given types.

        Called when an interactive prompt (entity review, JSON fix, chapter
        conflict) is resolved, so a reconnecting client isn't shown a stale
        modal for a question that has already been answered. Scoped to one book
        when `book_id` is given, so resolving book A's prompt doesn't dismiss
        book B's still-open one.
        """
        with self._ws_lock:
            keep = [
                m for m in self._replay
                if m.get("type") not in types
                or (book_id is not None and m.get("book_id") != book_id)
            ]
            self._replay.clear()
            self._replay.extend(keep)

    def send_message_sync(self, message: dict):
        """Broadcast a JSON message to all clients from a background thread."""
        message = self._buffer(message)
        loop = self.loop
        with self._ws_lock:
            has_sockets = bool(self.websockets)
        if not loop or not has_sockets:
            # Buffered above; a reconnecting client will receive it as replay.
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._broadcast(message), loop
            )
            future.result(timeout=10)
        except Exception as e:
            print(f"[JobHub] WebSocket send error: {e}")

    async def send_message_async(self, message: dict):
        """Broadcast a JSON message from an async context (e.g. API endpoints)."""
        await self._broadcast(self._buffer(message))

    async def _broadcast(self, message: dict):
        with self._ws_lock:
            sockets = list(self.websockets)
        dead = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception as e:
                print(f"[JobHub] WebSocket send failed, dropping socket: {e}")
                dead.append(ws)
        for ws in dead:
            self.remove_websocket(ws)

    # ------------------------------------------------------------------
    # Activity log — persist + broadcast
    # ------------------------------------------------------------------

    def log_activity(self, type, message, book_id=None, chapter=None, book_name=None, entities=None):
        """Write an activity log entry to the DB and send it via WS (background threads)."""
        entry = self._write_activity(type, message, book_id, chapter, book_name, entities)
        if entry:
            self.send_message_sync({"type": "activity_log", "entry": entry})

    async def log_activity_async(self, type, message, book_id=None, chapter=None, book_name=None, entities=None):
        """Write an activity log entry to the DB and send it via WS (async endpoints)."""
        entry = self._write_activity(type, message, book_id, chapter, book_name, entities)
        if entry:
            await self.send_message_async({"type": "activity_log", "entry": entry})

    def _write_activity(self, type, message, book_id, chapter, book_name, entities):
        if self.db_manager:
            return self.db_manager.add_activity_log(
                type=type, message=message,
                book_id=book_id, chapter=chapter,
                book_name=book_name, entities=entities,
            )
        return None


_job_ids = itertools.count(1)


class Job:
    """One translation run. At most one per book.

    Exposes the same member names the old singleton did, so
    ``web/services/web_interface.py`` and the API routers need no changes:
    per-run state lives here, and hub methods are delegated.
    """

    def __init__(self, hub: JobHub, book_id=None):
        self.hub = hub
        self.job_id = f"job-{next(_job_ids)}"
        # Serializes check-then-set of is_running so two concurrent
        # /api/translate or /api/queue/process-next calls cannot both start.
        self._job_start_lock = threading.Lock()
        self.reset()
        self.book_id = book_id

    def __repr__(self):
        return f"<Job {self.job_id} book={self.book_id} status={self.status}>"

    # ------------------------------------------------------------------
    # Job slot
    # ------------------------------------------------------------------

    def try_begin_job(self) -> bool:
        """Atomically claim this job slot. Returns False if already running.

        Callers that get True own is_running until they set it False (or call
        end_job). Pair with clear_cancel() before launching the worker thread.
        """
        with self._job_start_lock:
            if self.is_running:
                return False
            self.is_running = True
            return True

    def end_job(self):
        """Release the job slot (idempotent)."""
        with self._job_start_lock:
            self.is_running = False

    def reset(self):
        self.is_running = False
        self.status = "idle"  # idle | running | waiting | awaiting_review | complete | error
        self._waiting_reason: Optional[str] = None  # 'session_limit' | 'overloaded' while status == waiting
        self._waiting_limit: Optional[str] = None  # 'session' | 'weekly' for a session_limit wait
        self.error: Optional[str] = None
        self.last_result: Optional[dict] = None

        # Set by the API before starting a job
        self.pending_text: Optional[list] = None
        self.book_id: Optional[int] = None
        self.chapter_number: Optional[int] = None
        self.chapter_title: Optional[str] = None

        # Entity review synchronisation
        self._review_event = threading.Event()
        self._review_result: Optional[dict] = None
        self.pending_review: Optional[dict] = None  # {entities, context} for late-joining clients

        # JSON fix synchronisation (same pattern as entity review)
        self._json_fix_event = threading.Event()
        self._json_fix_result: Optional[dict] = None
        self.pending_json_fix: Optional[dict] = None

        # Chapter-conflict synchronisation — fires when an incoming chapter
        # has the same chapter_number as an existing one but different source.
        self._chapter_conflict_event = threading.Event()
        self._chapter_conflict_result: Optional[dict] = None
        self.pending_chapter_conflict: Optional[dict] = None

        # Auto-process queue state
        self.auto_process = False
        self._stop_auto = threading.Event()
        self._auto_max = None
        self._auto_done = 0

        # Cooperative cancellation — set by the cancel endpoint, polled by the
        # translation engine between (and mid-) chunks via is_cancelled().
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Hub delegation
    # ------------------------------------------------------------------

    @property
    def db_manager(self):
        return self.hub.db_manager

    @db_manager.setter
    def db_manager(self, value):
        self.hub.db_manager = value

    @property
    def websockets(self):
        return self.hub.websockets

    @property
    def loop(self):
        return self.hub.loop

    def add_websocket(self, websocket, loop):
        return self.hub.add_websocket(websocket, loop)

    def remove_websocket(self, websocket):
        self.hub.remove_websocket(websocket)

    def set_websocket(self, websocket, loop):
        self.hub.set_websocket(websocket, loop)

    def _stamp(self, message: dict) -> dict:
        """Tag an outgoing message with the job that produced it.

        Every broadcast goes through here, so the ~38 call sites in
        web_interface.py get correct routing without being touched. An explicit
        book_id already in the message wins (chapter-conflict payloads carry
        the incoming chapter's book).
        """
        stamped = {"book_id": self.book_id, **message}
        stamped["job_id"] = self.job_id
        return stamped

    def send_message_sync(self, message: dict):
        self.hub.send_message_sync(self._stamp(message))

    async def send_message_async(self, message: dict):
        await self.hub.send_message_async(self._stamp(message))

    async def _broadcast(self, message: dict):
        await self.hub._broadcast(message)

    def _buffer(self, message: dict):
        self.hub._buffer(self._stamp(message))

    def _drop_replay(self, *types: str):
        self.hub._drop_replay(*types, book_id=self.book_id)

    def log_activity(self, type, message, book_id=None, chapter=None, book_name=None, entities=None):
        self.hub.log_activity(
            type, message,
            book_id=self.book_id if book_id is None else book_id,
            chapter=chapter, book_name=book_name, entities=entities,
        )

    async def log_activity_async(self, type, message, book_id=None, chapter=None, book_name=None, entities=None):
        await self.hub.log_activity_async(
            type, message,
            book_id=self.book_id if book_id is None else book_id,
            chapter=chapter, book_name=book_name, entities=entities,
        )

    def _write_activity(self, type, message, book_id, chapter, book_name, entities):
        return self.hub._write_activity(type, message, book_id, chapter, book_name, entities)

    # ------------------------------------------------------------------
    # Cooperative cancellation
    # ------------------------------------------------------------------

    def request_cancel(self):
        """Signal the running translation thread to stop at the next chunk
        boundary (or mid-stream). The engine polls is_cancelled()."""
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def clear_cancel(self):
        """Reset the cancel flag — called when a fresh job starts so a stale
        cancel from a previous run doesn't immediately kill the new one."""
        self._cancel_event.clear()

    # ------------------------------------------------------------------
    # Progress callback (called from TranslationEngine)
    # ------------------------------------------------------------------

    def on_progress(self, progress: dict):
        phase = progress.get("phase")
        if phase in ("session_limit", "overloaded"):
            # The translation thread is parked — either until the Claude Code
            # session/weekly usage resets, or for the configured 529-overload retry
            # interval (both the Anthropic API and Claude Code providers raise
            # OverloadedError into the same engine retry loop). Surface that as
            # a distinct "waiting" status (so the UI doesn't look like it's
            # stuck mid-chunk) and drop one activity log line per pause. The
            # engine re-emits this phase each retry loop, so guard on the
            # status transition to avoid log spam.
            if self.status != "waiting":
                self.status = "waiting"
                self._waiting_reason = phase
                wait = progress.get("wait_seconds")
                mins = max(1, round(wait / 60)) if wait else None
                if phase == "session_limit":
                    # "session" or "weekly" — a weekly reset can be days out.
                    self._waiting_limit = progress.get("limit") or "session"
                    msg = f"Claude Code {self._waiting_limit} limit reached — queue paused"
                    if mins:
                        msg += f"; resuming in ~{mins} min"
                else:
                    msg = "API overloaded (529) — translation paused"
                    if mins:
                        msg += f"; retrying in ~{mins} min"
                self.log_activity(
                    type="warning", message=msg,
                    book_id=self.book_id, chapter=self.chapter_number,
                )
        elif self.status == "waiting":
            # Any other progress phase means the pause ended and work has
            # resumed; flip back to running and note it on the activity log.
            self.status = "running"
            if self._waiting_reason == "overloaded":
                resume_msg = "API recovered from overload — translation resumed"
            else:
                kind = (self._waiting_limit or "session").capitalize()
                resume_msg = f"{kind} limit reset — queue resumed"
            self._waiting_reason = None
            self._waiting_limit = None
            self.log_activity(
                type="info", message=resume_msg,
                book_id=self.book_id, chapter=self.chapter_number,
            )
        self.send_message_sync({"type": "progress", **progress})

    # ------------------------------------------------------------------
    # Human-decision prompts
    # ------------------------------------------------------------------

    # kind -> (payload attribute, the status that means "parked on this")
    _PROMPT_KINDS = {
        "review": ("pending_review", "awaiting_review"),
        "json_fix": ("pending_json_fix", "awaiting_json_fix"),
        "chapter_conflict": ("pending_chapter_conflict", "awaiting_chapter_conflict"),
    }

    def await_prompt(self, kind: str, payload: dict):
        """Publish a decision payload and mark the job parked on it, together.

        The status used to be set later, by the matching ``wait_for_*`` — after
        the payload had already been broadcast and the activity log written. In
        that window ``_job_state`` (which gates the payload on the status) told
        anyone hydrating "running, nothing pending", so a client that had just
        opened the modal off the WebSocket message erased it again and the run
        was stuck with no way to answer. Payload and status move as one here so
        no snapshot can describe half of it.
        """
        attr, status = self._PROMPT_KINDS[kind]
        setattr(self, attr, payload)
        self.status = status
        # A run parked on a prompt has demonstrably not ended, so any terminal
        # event still buffered for this book is stale — most often an earlier
        # chapter of this very auto-process run. Replaying one to a
        # reconnecting tab wiped the open modal, and with it the only way to
        # answer; on a flaky link the socket reconnects every few seconds, so
        # the prompt was on screen for a moment at a time.
        self._drop_replay(*self.hub._TERMINAL_TYPES)

    # ------------------------------------------------------------------
    # Entity review pause/resume
    # ------------------------------------------------------------------

    def wait_for_review(self) -> dict:
        """
        Block the translation thread until the user submits entity review.
        Waits indefinitely — use cancel to unblock if needed.
        """
        self.status = "awaiting_review"
        self._review_event.clear()
        self._review_event.wait()
        self.status = "running"
        result = self._review_result or {}
        self._review_result = None
        return result

    def submit_review(self, result: dict):
        """Called from the API endpoint when user submits entity review."""
        self._review_result = result
        self.pending_review = None
        self._drop_replay("entity_review_needed")
        self._review_event.set()

    def skip_review(self):
        """Skip entity review — accept AI translations as-is."""
        self._review_result = {}
        self.pending_review = None
        self._drop_replay("entity_review_needed")
        self._review_event.set()

    # ------------------------------------------------------------------
    # JSON fix pause/resume
    # ------------------------------------------------------------------

    def wait_for_json_fix(self, timeout: Optional[float] = None) -> dict:
        """
        Block the translation thread until the user submits a JSON fix, or until
        `timeout` seconds elapse. On timeout (no human response), default to
        retrying the chunk so the job never hangs indefinitely. A non-positive or
        None timeout waits forever (legacy behaviour).
        """
        self.status = "awaiting_json_fix"
        self._json_fix_event.clear()
        wait_for = timeout if (timeout and timeout > 0) else None
        signalled = self._json_fix_event.wait(wait_for)
        self.status = "running"
        if not signalled:
            # No human responded in time — fall back to retrying the chunk.
            self.pending_json_fix = None
            self._json_fix_result = None
            self._drop_replay("json_fix_needed")
            return {"action": "retry", "timed_out": True}
        result = self._json_fix_result or {}
        self._json_fix_result = None
        return result

    def submit_json_fix(self, result: dict):
        """Called from the API endpoint when user submits a JSON fix action."""
        self._json_fix_result = result
        self.pending_json_fix = None
        self._drop_replay("json_fix_needed")
        self._json_fix_event.set()

    # ------------------------------------------------------------------
    # Chapter-conflict pause/resume
    # ------------------------------------------------------------------

    def wait_for_chapter_conflict(self) -> dict:
        """
        Block the translation thread until the user decides whether to
        proceed (overwrite the existing chapter) or cancel (skip this item).
        Returns {"decision": "proceed" | "cancel"}.
        """
        self.status = "awaiting_chapter_conflict"
        self._chapter_conflict_event.clear()
        self._chapter_conflict_event.wait()
        self.status = "running"
        result = self._chapter_conflict_result or {}
        self._chapter_conflict_result = None
        return result

    def submit_chapter_conflict(self, decision: str, new_chapter_number: Optional[int] = None):
        """Called from the API endpoint when the user resolves the conflict."""
        self._chapter_conflict_result = {
            "decision": decision,
            "new_chapter_number": new_chapter_number,
        }
        self.pending_chapter_conflict = None
        self._drop_replay("chapter_conflict_needed")
        self._chapter_conflict_event.set()

    # ------------------------------------------------------------------
    # Auto-process queue
    # ------------------------------------------------------------------

    def start_auto_process(self, max_chapters=None):
        self.auto_process = True
        self._stop_auto.clear()
        self._auto_max = max_chapters  # None = unlimited
        self._auto_done = 1  # first chapter counts

    def stop_auto_process(self):
        """Signal the loop to stop after the current translation finishes."""
        self.auto_process = False
        self._stop_auto.set()

    def should_continue_auto(self):
        """Check whether the auto-process loop should continue."""
        if not self.auto_process or self._stop_auto.is_set():
            return False
        self._auto_done += 1
        if self._auto_max and self._auto_done > self._auto_max:
            return False
        return True


class JobBusyError(RuntimeError):
    """This book already has a live translation job."""

    def __init__(self, book_id, job):
        self.book_id = book_id
        self.job = job
        where = f"Book {book_id}" if book_id is not None else "This text"
        super().__init__(f"{where} is already translating.")


class JobCapacityError(RuntimeError):
    """Every translation slot is in use."""

    def __init__(self, limit):
        self.limit = limit
        super().__init__(
            f"All {limit} translation slots are in use. Wait for one to finish, "
            f"or raise 'Max concurrent translations' in Settings.")


class JobRegistry:
    """The live translation jobs, keyed by book.

    Two rules, both enforced here so no caller has to remember them:

    - **One job per book.** Chapters within a book must stay sequential — the
      entity glossary is built incrementally, so two chapters of the same book
      in flight would corrupt it.
    - **A ceiling on total jobs.** Each worker is another MySQL pool consumer
      and another concurrent provider stream.

    Modelled on ``modules/task_runner.ModuleTaskRunner``, which already does
    claim/start/release per book for module backfills.
    """

    DEFAULT_MAX_CONCURRENT = 3

    def __init__(self, hub: JobHub, max_concurrent=None):
        self.hub = hub
        self._lock = threading.Lock()
        self._jobs = {}  # book_id -> Job
        self._max_concurrent = max_concurrent  # None = read from settings

    def max_concurrent(self) -> int:
        """The current cap, read live so Settings changes apply without a restart."""
        if self._max_concurrent is not None:
            return int(self._max_concurrent)
        try:
            import settings_store
            value = settings_store.get(
                "max_concurrent_translations", self.DEFAULT_MAX_CONCURRENT)
            return max(1, int(value))
        except Exception:
            return self.DEFAULT_MAX_CONCURRENT

    def begin(self, book_id) -> Job:
        """Claim the slot for `book_id` and return its fresh Job.

        Raises JobBusyError if that book is already running, or
        JobCapacityError if every slot is taken. The caller owns the job until
        it calls end(book_id).
        """
        with self._lock:
            existing = self._jobs.get(book_id)
            if existing is not None and existing.is_running:
                raise JobBusyError(book_id, existing)

            limit = self.max_concurrent()
            running = sum(1 for j in self._jobs.values() if j.is_running)
            if existing is None and running >= limit:
                raise JobCapacityError(limit)

            job = Job(self.hub, book_id=book_id)
            job.is_running = True
            self._jobs[book_id] = job
            return job

    def end(self, book_id):
        """Release a book's slot (idempotent).

        The Job stays in the map so its terminal status is still readable by
        the status endpoint; only `is_running` frees the slot.
        """
        with self._lock:
            job = self._jobs.get(book_id)
        if job is not None:
            job.end_job()

    def rekey(self, old_book_id, new_book_id):
        """Move a job to the book it turned out to be for.

        The queue start path may claim a row before it knows the book (the
        request asked for "whatever is next"), so the job is registered under a
        placeholder and re-filed once the item is in hand.
        """
        with self._lock:
            job = self._jobs.pop(old_book_id, None)
            if job is None:
                return None
            clash = self._jobs.get(new_book_id)
            if clash is not None and clash.is_running:
                self._jobs[old_book_id] = job
                raise JobBusyError(new_book_id, clash)
            job.book_id = new_book_id
            self._jobs[new_book_id] = job
            return job

    def get(self, book_id):
        with self._lock:
            return self._jobs.get(book_id)

    def active(self):
        """Every job holding a slot right now."""
        with self._lock:
            return [j for j in self._jobs.values() if j.is_running]

    def all_jobs(self):
        """Every job we still remember, running or finished."""
        with self._lock:
            return list(self._jobs.values())

    def running_book_ids(self):
        with self._lock:
            return {b for b, j in self._jobs.items() if j.is_running}

    def has_capacity(self) -> bool:
        return len(self.active()) < self.max_concurrent()

    def prune(self, keep=32):
        """Forget the oldest finished jobs so the map can't grow forever."""
        with self._lock:
            finished = [b for b, j in self._jobs.items() if not j.is_running]
            for book_id in finished[:max(0, len(finished) - keep)]:
                del self._jobs[book_id]


# Process-wide hub: WebSocket connections and the activity log outlive any job.
job_hub = JobHub()

# The live jobs, one per book.
job_registry = JobRegistry(job_hub)
