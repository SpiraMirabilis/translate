"""The safety contract: entity-DB writes need the book's translation idle.

The translator builds its glossary from the entity table chapter by chapter,
so rewriting a record under a running job either gets clobbered by the
in-flight chapter's save or poisons the next chapter's prompt. Prose and
footnote writes are never blocked — the translator only appends chapters.

Checked per book: a job on book A never blocks a fix on book B.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from mcp.server.fastmcp.exceptions import ToolError

from .deps import AdminAuthError, AdminHTTPError, AdminUnreachable, AppContext

# Mirrors translation_status.IDLE_STATUSES (not imported: that module pops
# DEBUG from the environment at import time).
IDLE_STATUSES = {"idle", "complete", "error"}


class BookBusyError(ToolError):
    """The book has a live translation job; the entity write was refused."""


def book_job(status: dict, book_id: int) -> Optional[dict]:
    """This book's live job from a /api/translate/status payload, or None."""
    job = (status.get("jobs") or {}).get(str(book_id))
    if not job:
        return None
    if not job.get("is_running") and job.get("status") in IDLE_STATUSES:
        return None
    return job


def _processing_rows(db, book_id: int) -> int:
    """Queue rows claimed for this book — catches CLI `translator.py --resume`
    runs, which the admin server's job registry never sees."""
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM queue WHERE book_id = ? AND status = 'processing'",
                    (book_id,))
        row = cur.fetchone()
    return int(row[0] if row else 0)


def ensure_book_idle(app: AppContext, book_id: int, force: bool = False) -> None:
    """Raise BookBusyError unless `book_id` has no live translation."""
    if force:
        return
    try:
        status = app.admin.status()
    except (AdminUnreachable, AdminAuthError, AdminHTTPError) as exc:
        raise BookBusyError(
            f"Cannot verify that book {book_id} is idle ({exc}). Pass force=true only "
            "if you know nothing is translating this book.") from exc

    job = book_job(status, book_id)
    if job:
        auto = "on" if job.get("auto_process") else "off"
        raise BookBusyError(
            f"Book {book_id} is translating (status {job.get('status')}, "
            f"chapter {job.get('chapter_number')}, auto-process {auto}). Entity-DB writes "
            "would race the in-flight chapter. Call t9_pause_translation first, apply the "
            "entity changes, then t9_resume_translation. Prose and footnote edits are not "
            "blocked.")

    n = _processing_rows(app.ensure_db(), book_id)
    if n:
        raise BookBusyError(
            f"Book {book_id} has {n} queue row(s) claimed as 'processing' but no admin-server "
            "job — likely a CLI `translator.py --resume` run (or a stale claim from a crash, "
            "released automatically after 6h). Stop that run first, or pass force=true if "
            "you are sure the claim is stale.")


def job_snapshot(job: dict) -> dict:
    keys = ("status", "chapter_number", "chapter_title", "auto_process",
            "auto_remaining", "run_options")
    return {k: job.get(k) for k in keys}


RESUME_KEYS = ("translation_model", "advice_model", "cleaning_model", "no_review",
               "two_pass", "no_clean", "no_stream", "save_as_draft", "max_chapters")


def pause_book(admin, book_id: int, *, wait: bool = True, timeout_seconds: float = 900,
               poll_seconds: float = 10, sleep: Callable[[float], None] = time.sleep,
               progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Stop auto-process for one book and (optionally) wait for it to drain.

    stop-auto lets the in-flight chapter finish — it never cancels one, so no
    half-translated chapter is thrown away.
    """
    status = admin.status()
    job = book_job(status, book_id)
    if job is None:
        return {"book_id": book_id, "already_idle": True, "stopped": True}

    before = job_snapshot(job)
    result = {"book_id": book_id, "already_idle": False, "before": before,
              "run_options": before.get("run_options")}

    if job.get("auto_process"):
        admin.stop_auto(book_id)
        result["stop_auto_sent"] = True
    else:
        result["stop_auto_sent"] = False
        result["note"] = ("Single-chapter run (not auto-processing): nothing to stop, "
                          "waiting for the chapter to finish.")

    if not wait:
        result["stopped"] = False
        result["message"] = "Stop requested; the in-flight chapter is still running."
        return _with_resume_hint(result)

    waited = 0.0
    last = job
    while True:
        if waited >= timeout_seconds:
            result.update(stopped=False, timed_out=True, waited_seconds=round(waited),
                          last_status=last.get("status"),
                          last_chapter=last.get("chapter_number"),
                          message=f"Still running after {round(waited)}s; call again to keep waiting.")
            return _with_resume_hint(result)
        sleep(poll_seconds)
        waited += poll_seconds
        current = book_job(admin.status(), book_id)
        if current is None:
            break
        last = current
        st = current.get("status") or ""
        if progress:
            progress(waited, f"book {book_id}: {st}, chapter {current.get('chapter_number')}")
        if st.startswith("awaiting_"):
            result.update(stopped=False, needs_human=True, waited_seconds=round(waited),
                          last_status=st, last_chapter=current.get("chapter_number"),
                          message=(f"The run is parked on '{st}' — a decision is needed in "
                                   "the admin GUI before the chapter can finish. Auto-process "
                                   "is already stopped, so it will not start another chapter."))
            return _with_resume_hint(result)

    result.update(stopped=True, waited_seconds=round(waited),
                  last_chapter=last.get("chapter_number"))
    return _with_resume_hint(result)


def _with_resume_hint(result: dict) -> dict:
    opts = result.get("run_options")
    if opts:
        args = {k: opts.get(k) for k in RESUME_KEYS if opts.get(k) not in (None, False)}
        result["resume_hint"] = {"book_id": result["book_id"], **args}
    elif not result.get("already_idle"):
        result["resume_hint"] = None
        result["resume_note"] = ("The admin server did not report run_options (restart "
                                 "t9.service to pick up the newer status payload); pass "
                                 "models and flags to t9_resume_translation explicitly.")
    return result
