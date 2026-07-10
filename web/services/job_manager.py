"""
Single-user job manager for the web interface.
Bridges the synchronous translation thread with the async FastAPI/WebSocket layer.
"""
import asyncio
import threading
from collections import deque
from typing import Optional, Any


class JobManager:
    """
    Manages the single active translation job.

    Translation runs in a background thread (because it makes blocking HTTP calls).
    This class bridges that thread with the async FastAPI event loop via:
    - asyncio.run_coroutine_threadsafe() to broadcast WebSocket messages from the thread
    - threading.Event to pause the thread during entity review
    """

    # Message types NOT kept in the replay buffer: activity_log entries are
    # persisted in the DB and refetched by the frontend on load, and progress
    # ticks are high-frequency transient state that the status endpoint
    # already restores — replaying stale ones would just flicker the UI.
    _NO_REPLAY_TYPES = {"activity_log", "progress"}

    # Terminal job outcomes. Only the newest of each type stays in the replay
    # buffer — an older completion carries no state a reconnecting client can
    # still act on, and the buffer otherwise fills with a process-lifetime
    # backlog of them.
    _COLLAPSE_REPLAY_TYPES = {
        "translation_complete",
        "auto_process_done",
        "translation_cancelled",
        "error",
    }

    def __init__(self):
        self.db_manager = None  # Set by app.py after DatabaseManager is created

        # WebSocket connection state — lives for the process, not per job,
        # so it is deliberately NOT part of reset(): a future reset() caller
        # must not orphan connected clients.
        self.websockets: set = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_lock = threading.Lock()
        # Serializes check-then-set of is_running so two concurrent
        # /api/translate or /api/queue/process-next calls cannot both start.
        self._job_start_lock = threading.Lock()
        # Recent low-frequency events (completion, errors, review prompts),
        # replayed to reconnecting clients so e.g. a translation_complete
        # fired with no tab open isn't lost.
        self._replay: deque = deque(maxlen=100)
        self._seq = 0

        self.reset()

    def try_begin_job(self) -> bool:
        """Atomically claim the single job slot. Returns False if already running.

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

    def _buffer(self, message: dict):
        """Retain low-frequency events for replay to (re)connecting clients."""
        mtype = message.get("type")
        if mtype in self._NO_REPLAY_TYPES:
            return
        with self._ws_lock:
            self._seq += 1
            if mtype in self._COLLAPSE_REPLAY_TYPES:
                # A reconnecting client needs the latest outcome, not every one
                # since process start. Superseding keeps the buffer from filling
                # with stale completions that each new tab must replay.
                keep = [m for m in self._replay if m.get("type") != mtype]
                self._replay.clear()
                self._replay.extend(keep)
            self._replay.append({**message, "seq": self._seq})

    def _drop_replay(self, *types: str):
        """Remove buffered messages of the given types.

        Called when an interactive prompt (entity review, JSON fix, chapter
        conflict) is resolved, so a reconnecting client isn't shown a stale
        modal for a question that has already been answered.
        """
        with self._ws_lock:
            keep = [m for m in self._replay if m.get("type") not in types]
            self._replay.clear()
            self._replay.extend(keep)

    def send_message_sync(self, message: dict):
        """Broadcast a JSON message to all clients from the background translation thread."""
        self._buffer(message)
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
            print(f"[JobManager] WebSocket send error: {e}")

    async def send_message_async(self, message: dict):
        """Broadcast a JSON message from an async context (e.g. API endpoints)."""
        self._buffer(message)
        await self._broadcast(message)

    async def _broadcast(self, message: dict):
        with self._ws_lock:
            sockets = list(self.websockets)
        dead = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception as e:
                print(f"[JobManager] WebSocket send failed, dropping socket: {e}")
                dead.append(ws)
        for ws in dead:
            self.remove_websocket(ws)

    # ------------------------------------------------------------------
    # Progress callback (called from TranslationEngine)
    # ------------------------------------------------------------------

    def on_progress(self, progress: dict):
        phase = progress.get("phase")
        if phase in ("session_limit", "overloaded"):
            # The translation thread is parked — either until the Claude Code
            # session usage resets, or for the configured 529-overload retry
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
                    msg = "Claude Code session limit reached — queue paused"
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
                resume_msg = "Session limit reset — queue resumed"
            self._waiting_reason = None
            self.log_activity(
                type="info", message=resume_msg,
                book_id=self.book_id, chapter=self.chapter_number,
            )
        self.send_message_sync({"type": "progress", **progress})

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

    # ------------------------------------------------------------------
    # Activity log — persist + broadcast
    # ------------------------------------------------------------------

    def log_activity(self, type, message, book_id=None, chapter=None, book_name=None, entities=None):
        """Write an activity log entry to the DB and send it via WS (from background threads)."""
        entry = self._write_activity(type, message, book_id, chapter, book_name, entities)
        if entry:
            self.send_message_sync({"type": "activity_log", "entry": entry})

    async def log_activity_async(self, type, message, book_id=None, chapter=None, book_name=None, entities=None):
        """Write an activity log entry to the DB and send it via WS (from async endpoints)."""
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


# Global singleton — single user, so one job at a time
job_manager = JobManager()
