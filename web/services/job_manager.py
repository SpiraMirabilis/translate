"""
Single-user job manager for the web interface.
Bridges the synchronous translation thread with the async FastAPI/WebSocket layer.
"""
import asyncio
import threading
from typing import Optional, Any


class JobManager:
    """
    Manages the single active translation job.

    Translation runs in a background thread (because it makes blocking HTTP calls).
    This class bridges that thread with the async FastAPI event loop via:
    - asyncio.run_coroutine_threadsafe() to send WebSocket messages from the thread
    - threading.Event to pause the thread during entity review
    """

    def __init__(self):
        self.db_manager = None  # Set by app.py after DatabaseManager is created
        self.reset()

    def reset(self):
        self.is_running = False
        self.status = "idle"  # idle | running | awaiting_review | complete | error
        self.error: Optional[str] = None
        self.last_result: Optional[dict] = None

        # Set by the API before starting a job
        self.pending_text: Optional[list] = None
        self.book_id: Optional[int] = None
        self.chapter_number: Optional[int] = None

        # WebSocket connection — set when client connects
        self.websocket = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        # Entity review synchronisation
        self._review_event = threading.Event()
        self._review_result: Optional[dict] = None
        self.pending_review: Optional[dict] = None  # {entities, context} for late-joining clients

        # JSON fix synchronisation (same pattern as entity review)
        self._json_fix_event = threading.Event()
        self._json_fix_result: Optional[dict] = None
        self.pending_json_fix: Optional[dict] = None

        # Auto-process queue state
        self.auto_process = False
        self._stop_auto = threading.Event()
        self._auto_max = None
        self._auto_done = 0

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    def set_websocket(self, websocket, loop: asyncio.AbstractEventLoop):
        self.websocket = websocket
        self.loop = loop

    def send_message_sync(self, message: dict):
        """Send a JSON message to the frontend from the background translation thread."""
        if not self.loop or not self.websocket:
            print(f"[JobManager] No WebSocket connected, dropping message: {message.get('type')}:{message.get('step', '')}")
            return
        if self.loop and self.websocket:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._send(message), self.loop
                )
                future.result(timeout=10)
            except Exception as e:
                print(f"[JobManager] WebSocket send error: {e}")

    async def send_message_async(self, message: dict):
        """Send a JSON message from an async context (e.g. API endpoints)."""
        await self._send(message)

    async def _send(self, message: dict):
        if self.websocket:
            try:
                await self.websocket.send_json(message)
            except Exception as e:
                print(f"[JobManager] WebSocket error: {e}")

    # ------------------------------------------------------------------
    # Progress callback (called from TranslationEngine)
    # ------------------------------------------------------------------

    def on_progress(self, progress: dict):
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
        self._review_event.set()

    def skip_review(self):
        """Skip entity review — accept AI translations as-is."""
        self._review_result = {}
        self.pending_review = None
        self._review_event.set()

    # ------------------------------------------------------------------
    # JSON fix pause/resume
    # ------------------------------------------------------------------

    def wait_for_json_fix(self) -> dict:
        """
        Block the translation thread until the user submits a JSON fix.
        """
        self.status = "awaiting_json_fix"
        self._json_fix_event.clear()
        self._json_fix_event.wait()
        self.status = "running"
        result = self._json_fix_result or {}
        self._json_fix_result = None
        return result

    def submit_json_fix(self, result: dict):
        """Called from the API endpoint when user submits a JSON fix action."""
        self._json_fix_result = result
        self.pending_json_fix = None
        self._json_fix_event.set()

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
