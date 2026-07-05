"""Background runner for per-book module lifecycle backfills.

Module enable/disable events (and settings rebuilds) can rewrite every chapter
of a book two ways — on a large book that is far too slow to run inside an HTTP
request. This runner executes those backfills on a background thread, one task
per book at a time, and is the single source of truth for the guardrails:
while a book has an active task, module toggles, module-settings changes and
book deletion are refused with :class:`ModuleTaskBusyError`.

The claim/start split exists so a caller can reserve the book's slot *before*
persisting the state change that the backfill will act on (closing the gap
where the DB row changes but the slot turns out to be taken):

    module_task_runner.claim(book_id, label)     # raises ModuleTaskBusyError
    ...persist the change...
    module_task_runner.start(book_id, fn)        # runs fn on a thread
    # or, if nothing needs backfilling / persistence failed:
    module_task_runner.release(book_id)

Threads are non-daemon: a service shutdown waits for a running backfill rather
than killing it mid-rewrite. Task info dicts are JSON-safe (no thread objects)
so API endpoints can return them verbatim.
"""
import datetime
import threading


class ModuleTaskBusyError(RuntimeError):
    """A module backfill is already pending/running for this book."""

    def __init__(self, book_id, task):
        self.book_id = book_id
        self.task = dict(task or {})
        label = self.task.get("label") or "module task"
        super().__init__(
            f"A module task ({label}) is already running for book {book_id}. "
            "Wait for it to finish and try again.")


class ModuleTaskRunner:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = {}   # book_id -> task info dict (state: pending|running)
        self._threads = {}  # book_id -> Thread (running tasks only)
        self._last = {}     # book_id -> last finished task info dict

    # -- slot lifecycle ----------------------------------------------------

    def claim(self, book_id, label):
        """Reserve the book's task slot. Raises ModuleTaskBusyError if taken."""
        with self._lock:
            if book_id in self._active:
                raise ModuleTaskBusyError(book_id, self._active[book_id])
            self._active[book_id] = {
                "book_id": book_id,
                "label": label,
                "state": "pending",
                "started_at": datetime.datetime.now().isoformat(),
            }

    def release(self, book_id):
        """Drop a claim that never started (persistence failed / no-op diff)."""
        with self._lock:
            info = self._active.get(book_id)
            if info is not None and info.get("state") == "pending":
                del self._active[book_id]

    def start(self, book_id, fn, label=None, logger=None):
        """Run ``fn`` on a background thread in the book's claimed slot.

        The slot must have been claimed first. ``label`` (optional) replaces
        the claim-time label now that the caller knows the real diff. Any
        exception from ``fn`` is caught, logged, and recorded on the finished
        task info (the thread never propagates).
        """
        with self._lock:
            info = self._active.get(book_id)
            if info is None or info.get("state") != "pending":
                raise RuntimeError(
                    f"start() without a pending claim for book {book_id}")
            if label:
                info["label"] = label
            info["state"] = "running"
            info["started_at"] = datetime.datetime.now().isoformat()

        def run():
            error = None
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - must never kill the thread
                error = str(e)
                if logger:
                    logger.error(
                        f"Module task for book {book_id} failed: {e}")
            finally:
                with self._lock:
                    done = self._active.pop(book_id, info)
                    self._threads.pop(book_id, None)
                    self._last[book_id] = {
                        **done,
                        "state": "error" if error else "done",
                        "error": error,
                        "finished_at": datetime.datetime.now().isoformat(),
                    }

        t = threading.Thread(
            target=run, name=f"module-task-book-{book_id}", daemon=False)
        with self._lock:
            self._threads[book_id] = t
        t.start()

    # -- inspection ----------------------------------------------------------

    def active(self, book_id):
        """Copy of the pending/running task info for a book, or None."""
        with self._lock:
            info = self._active.get(book_id)
            return dict(info) if info else None

    def status(self, book_id):
        """JSON-safe ``{"running": info|None, "last": info|None}`` for a book."""
        with self._lock:
            info = self._active.get(book_id)
            last = self._last.get(book_id)
            return {
                "running": dict(info) if info else None,
                "last": dict(last) if last else None,
            }

    def join(self, book_id, timeout=None):
        """Wait for the book's running task thread (tests / CLI determinism).

        Returns True when no task is running (or it finished in time).
        """
        with self._lock:
            t = self._threads.get(book_id)
        if t is None:
            return True
        t.join(timeout)
        return not t.is_alive()


# Process-wide singleton (single-user app, like web's job_manager).
module_task_runner = ModuleTaskRunner()
