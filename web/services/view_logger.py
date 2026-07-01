"""
Buffered reader-view logging.

The public reader endpoints used to fire one or two synchronous DB writes on
EVERY chapter/book GET (reader_log INSERT + books.view_count UPDATE), a write
storm under real traffic and pure latency on the hot path. ViewLogger buffers
views in memory and a daemon thread flushes them in bulk every few seconds via
DatabaseManager.flush_reader_views().

Accepted trade-off: up to `flush_interval` seconds of view data is lost on a
hard kill. Views are non-critical analytics; stop() flushes on clean shutdown
(wired to FastAPI lifespan/atexit in web/app.py).
"""
import threading
from collections import Counter, deque


class ViewLogger:
    def __init__(self, db_manager, flush_interval: float = 5.0, max_buffer: int = 2000):
        self._db = db_manager
        self._interval = flush_interval
        self._max_buffer = max_buffer
        self._views: deque = deque()        # (book_id, chapter_number, ip)
        self._book_bumps: Counter = Counter()  # book_id -> extra view_count bumps
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

    # -- producers (request handlers; O(1), never raise) ------------------

    def log_view(self, book_id: int, chapter_number: int, ip: str) -> None:
        """Record a chapter view (reader_log row + view_count bump)."""
        try:
            with self._lock:
                self._views.append((book_id, chapter_number, ip))
                self._book_bumps[book_id] += 1
                overflow = len(self._views) >= self._max_buffer
            if overflow:
                self.flush()
        except Exception:
            pass

    def bump_book(self, book_id: int) -> None:
        """Bump a book's view_count without a reader_log row (book-detail views)."""
        try:
            with self._lock:
                self._book_bumps[book_id] += 1
        except Exception:
            pass

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="view-logger-flush", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the flush thread and flush whatever is buffered."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 5)
        self.flush()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            self.flush()

    # -- flush ---------------------------------------------------------------

    def flush(self) -> None:
        with self._lock:
            if not self._views and not self._book_bumps:
                return
            views = list(self._views)
            bumps = dict(self._book_bumps)
            self._views.clear()
            self._book_bumps.clear()
        try:
            self._db.flush_reader_views(views, bumps)
        except Exception as e:
            # Views are best-effort analytics: log and drop rather than grow
            # the buffer without bound against a broken DB.
            try:
                self._db.logger.error(f"ViewLogger flush failed ({len(views)} views dropped): {e}")
            except Exception:
                pass
