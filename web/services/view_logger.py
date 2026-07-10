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

ViewLogger also owns view *deduplication*: a repeat (book, chapter, ip) inside
`dedupe_window` is dropped. The reader beacon cannot do this itself — reloads,
back/forward navigation and a second tab all reset its per-session guard. This
makes ViewLogger semantically load-bearing rather than a pure buffer: the
direct `_db.log_reader_view` fallback in web/api/public.py is the undeduped
path, used only when no ViewLogger is wired (tests, CLI).
"""
import threading
import time
from collections import Counter, deque

_MAX_DEDUPE_KEYS = 200_000


class ViewLogger:
    def __init__(self, db_manager, flush_interval: float = 5.0, max_buffer: int = 2000,
                 dedupe_window: float = 1800.0):
        self._db = db_manager
        self._interval = flush_interval
        self._max_buffer = max_buffer
        self._dedupe_window = dedupe_window
        self._views: deque = deque()        # (book_id, chapter_number, ip)
        self._book_bumps: Counter = Counter()  # book_id -> extra view_count bumps
        self._recent: dict = {}             # (book_id, chapter_number, ip) -> monotonic ts
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

    # -- producers (request handlers; O(1), never raise) ------------------

    def log_view(self, book_id: int, chapter_number: int, ip: str) -> None:
        """Record a chapter view (reader_log row + view_count bump).

        A repeat view of the same chapter by the same IP within
        `dedupe_window` seconds is dropped.
        """
        try:
            key = (book_id, chapter_number, ip)
            now = time.monotonic()
            with self._lock:
                last = self._recent.get(key)
                if last is not None and now - last < self._dedupe_window:
                    return
                self._recent[key] = now
                if len(self._recent) > _MAX_DEDUPE_KEYS:
                    self._prune_recent_locked(now)
                self._views.append(key)
                self._book_bumps[book_id] += 1
                overflow = len(self._views) >= self._max_buffer
            if overflow:
                self.flush()
        except Exception:
            pass

    def _prune_recent_locked(self, now: float) -> None:
        """Drop dedupe keys older than the window. Caller holds the lock."""
        cutoff = now - self._dedupe_window
        stale = [k for k, ts in self._recent.items() if ts <= cutoff]
        for k in stale:
            del self._recent[k]

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
            with self._lock:
                self._prune_recent_locked(time.monotonic())

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
