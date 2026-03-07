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

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    def set_websocket(self, websocket, loop: asyncio.AbstractEventLoop):
        self.websocket = websocket
        self.loop = loop

    def send_message_sync(self, message: dict):
        """Send a JSON message to the frontend from the background translation thread."""
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

    def wait_for_review(self, timeout: int = 600) -> dict:
        """
        Block the translation thread until the user submits entity review.
        Returns the submitted entity data (or empty dict on timeout).
        """
        self.status = "awaiting_review"
        self._review_event.clear()
        self._review_event.wait(timeout=timeout)
        self.status = "running"
        result = self._review_result or {}
        self._review_result = None
        return result

    def submit_review(self, result: dict):
        """Called from the API endpoint when user submits entity review."""
        self._review_result = result
        self._review_event.set()

    def skip_review(self):
        """Skip entity review — accept AI translations as-is."""
        self._review_result = {}
        self._review_event.set()


# Global singleton — single user, so one job at a time
job_manager = JobManager()
