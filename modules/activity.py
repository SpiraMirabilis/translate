"""Debounced activity-log summaries for module transforms.

When a module's ingest transform actually changes content, the dispatchers in
``modules/__init__.py`` record it here instead of logging immediately — a bulk
ingest (EPUB import, batch upload, queue auto-process) would otherwise spam one
activity-log line per chapter. Records accumulate in memory per
``(book_id, module_id, side)`` and are flushed as a single summary line
naming the chapter range and book ("Chatgroup Transformer transformed
chapters 2-31 of Some Novel (book 7) during source ingest") when:

  * a bulk call site that knows its batch ended calls :meth:`flush`
    (EPUB/batch upload endpoints, the translation worker's ``finally``), or
  * the fallback sweep thread finds an entry idle past ``quiet_seconds``
    (covers one-off saves with no explicit boundary), or
  * the process exits cleanly (``atexit`` — covers CLI runs and service stops).

Deliberately in-memory: these are informational log lines, and every ingest
path runs in this one process. A hard crash loses at most one pending summary;
persisting counters would instead risk stale rows resurrecting ghost messages.

Thread safety: a single lock guards the pending dict; all mutation happens
under it, and entries are *popped* under the lock before their summary is
emitted outside it (so slow DB writes never block recording, and an entry can
never be emitted twice). The sweep thread is started lazily on first record
and exits when the dict empties, so idle processes carry no extra thread.

Event backfills (enable/disable/settings rebuild) don't come through here —
they already have a natural completion point and log one summary directly via
:func:`log_module_activity`.
"""
import atexit
import threading
import time

# Fallback flush: an entry with no new records for this long is emitted by the
# sweep thread. Generous on purpose — explicit flush() covers the bulk paths,
# and a lazy fallback avoids per-chapter lines during slow drips (queue
# auto-processing ingests minutes apart).
QUIET_SECONDS = 300
SWEEP_INTERVAL = 20

# Optional richer sink (set by the web app to job_manager.log_activity so
# summaries also broadcast over WebSocket). Falls back to db.add_activity_log.
_notifier = None


def set_activity_notifier(fn):
    """Install a ``fn(type=, message=, book_id=)`` sink for module activity."""
    global _notifier
    _notifier = fn


def log_module_activity(db, type, message, book_id=None):
    """Write one module activity-log line, via the notifier when installed.

    Best-effort: activity logging must never break a transform or backfill.
    """
    fn = _notifier
    if fn is not None:
        try:
            fn(type=type, message=message, book_id=book_id)
            return
        except Exception:
            pass  # fall through to the plain DB write
    if db is None:
        return
    try:
        db.add_activity_log(type=type, message=message, book_id=book_id)
    except Exception:
        pass


def _as_chapter_int(chapter):
    """Coerce a chapter number to int, or None if it isn't one (so range
    compression stays meaningful). Accepts ints and clean int-valued strings."""
    try:
        return int(chapter)
    except (TypeError, ValueError):
        return None


def _format_ranges(nums):
    """Compress a sorted list of ints into "2-31", "34", "34,36-40"."""
    parts = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


def _book_label(entry, book_id):
    """"Title (book 12)" when the title is known, else "book 12"."""
    title = entry.get("book_title")
    return f"{title} (book {book_id})" if title else f"book {book_id}"


class ModuleActivityAggregator:
    def __init__(self, quiet_seconds=QUIET_SECONDS, sweep_interval=SWEEP_INTERVAL):
        self.quiet_seconds = quiet_seconds
        self.sweep_interval = sweep_interval
        self._lock = threading.Lock()
        self._pending = {}  # (book_id, module_id, side) -> {count, last, db}
        self._thread = None

    def record(self, db, book_id, module_id, side, chapter=None, book_title=None):
        """Count one ingested item that ``module_id`` actually transformed.

        ``side`` is ``"source"`` or ``"translated"``. ``chapter`` (when known)
        is remembered so the summary can name the chapter range instead of a
        bare item count; ``book_title`` labels the book in the summary. ``db``
        is retained so the eventual flush can write the activity log (last
        recorder wins).
        """
        if not book_id:
            return
        with self._lock:
            key = (book_id, module_id, side)
            entry = self._pending.get(key)
            if entry is None:
                entry = self._pending[key] = {"count": 0, "chapters": set()}
            entry["count"] += 1
            num = _as_chapter_int(chapter)
            if num is not None:
                entry["chapters"].add(num)
            entry["last"] = time.monotonic()
            entry["db"] = db
            if book_title:
                entry["book_title"] = book_title
            self._ensure_sweeper()

    def flush(self, book_id=None):
        """Emit pending summaries now (all books, or just one).

        Called by bulk call sites when their batch ends — EPUB import, batch
        upload, the translation worker's ``finally`` — and at process exit.
        """
        self._flush_idle(0, book_id=book_id)

    # -- internals -----------------------------------------------------------

    def _ensure_sweeper(self):
        """Start the fallback sweep thread if it isn't running. Caller holds the lock."""
        if self._thread is None:
            t = threading.Thread(
                target=self._sweep_loop, name="module-activity-sweep", daemon=True)
            self._thread = t
            t.start()

    def _sweep_loop(self):
        while True:
            time.sleep(self.sweep_interval)
            self._flush_idle(self.quiet_seconds)
            with self._lock:
                if not self._pending:
                    # Nothing left to watch — exit; the next record() restarts us.
                    self._thread = None
                    return

    def _flush_idle(self, min_idle, book_id=None):
        now = time.monotonic()
        with self._lock:
            due = [(key, self._pending.pop(key))
                   for key in list(self._pending)
                   if (book_id is None or key[0] == book_id)
                   and now - self._pending[key]["last"] >= min_idle]
        # Emit outside the lock: DB/WS writes must not block record().
        for (bid, module_id, side), entry in due:
            self._emit(bid, module_id, side, entry)

    def _emit(self, book_id, module_id, side, entry):
        from . import REGISTRY  # late import — this module is imported by __init__
        mod = REGISTRY.get(module_id)
        name = mod.name if mod else module_id
        chapters = entry.get("chapters") or set()
        if chapters:
            nums = sorted(chapters)
            noun = "chapter" if len(nums) == 1 else "chapters"
            book_label = _book_label(entry, book_id)
            message = (f"{name} transformed {noun} {_format_ranges(nums)} "
                       f"of {book_label} during {side} ingest")
        else:
            # No per-chapter info reached us (direct call sites) — fall back to
            # the bare item count.
            n = entry["count"]
            message = (f"{name} transformed {n} item{'s' if n != 1 else ''} "
                       f"during {side} ingest")
        log_module_activity(entry.get("db"), "info", message, book_id)


# Process-wide singleton; flush pending summaries on clean interpreter exit
# (covers CLI runs and normal service shutdown).
module_activity = ModuleActivityAggregator()
atexit.register(module_activity.flush)
