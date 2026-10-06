"""Translation job status, pause (drain) and resume — over the admin API."""

from typing import Annotated, Optional

import anyio
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from ..deps import AdminHTTPError, app
from ..formatting import json_out
from ..guard import book_job, job_snapshot, pause_book
from ._common import report, tool
from .books import book_progress


def register(mcp) -> None:

    @tool(mcp, "t9_translation_status", "Translation status", read_only=True,
          idempotent=False)
    def t9_translation_status(
        book_id: Annotated[Optional[int], Field(description="Also report this book's job and DB progress")] = None,
    ) -> str:
        """Whether translations are running (per book), with each job's chapter,
        auto-process state, remaining chapter budget and run options. With
        book_id, adds whether THAT book is safe for entity-DB writes and its
        translated/highest/queued counts."""
        status = app().admin.status()
        jobs = {bid: job_snapshot(j) for bid, j in (status.get("jobs") or {}).items()}
        out = {"status": status.get("status"), "running": status.get("running"),
               "max_concurrent": status.get("max_concurrent"), "jobs": jobs}
        if book_id is not None:
            job = book_job(status, book_id)
            out["book"] = {"id": book_id, "translating": job is not None,
                           "entity_writes_safe": job is None,
                           **book_progress(app().ensure_db(), book_id)}
        return json_out(out)

    @tool(mcp, "t9_pause_translation", "Pause a book's translation", read_only=False,
          idempotent=True)
    async def t9_pause_translation(
        book_id: int,
        wait: Annotated[bool, Field(description="Wait for the in-flight chapter to finish")] = True,
        timeout_seconds: Annotated[int, Field(ge=10, le=3600)] = 900,
        poll_seconds: Annotated[int, Field(ge=2, le=120)] = 10,
        ctx: Context = None,
    ) -> str:
        """Stop auto-process for one book and wait until its in-flight chapter is
        saved (never cancels a chapter). Returns the run's options and a
        resume_hint to pass to t9_resume_translation. Returns early with
        needs_human=true if the run parks on a GUI decision (entity review, JSON
        fix, chapter conflict). Other books keep translating."""
        admin = app().admin
        loop_token = anyio.lowlevel.current_token()

        def progress(waited, message):
            anyio.from_thread.run(report, ctx, waited, timeout_seconds, message,
                                  token=loop_token)

        result = await anyio.to_thread.run_sync(
            lambda: pause_book(admin, book_id, wait=wait, timeout_seconds=timeout_seconds,
                               poll_seconds=poll_seconds, progress=progress))
        return json_out(result)

    @tool(mcp, "t9_resume_translation", "Resume a book's translation", read_only=False,
          idempotent=False, open_world=True)
    def t9_resume_translation(
        book_id: int,
        max_chapters: Annotated[Optional[int], Field(ge=1, description="Stop after this many chapters; omit for the whole queue")] = None,
        translation_model: Annotated[Optional[str], Field(description="provider:model, e.g. claude:claude-opus-5; omit for the default")] = None,
        advice_model: Optional[str] = None,
        cleaning_model: Optional[str] = None,
        no_review: Annotated[bool, Field(description="Skip the entity-review handshake")] = False,
        two_pass: bool = False,
        no_clean: bool = False,
        no_stream: bool = False,
        save_as_draft: bool = False,
    ) -> str:
        """Start auto-processing this book's queue (POST /api/queue/process-next).
        Pass the resume_hint from t9_pause_translation to restart a paused run
        the way it was. This starts paid model calls."""
        payload = {"book_id": book_id, "auto_process": True, "max_chapters": max_chapters,
                   "translation_model": translation_model, "advice_model": advice_model,
                   "cleaning_model": cleaning_model, "no_review": no_review,
                   "two_pass": two_pass, "no_clean": no_clean, "no_stream": no_stream,
                   "save_as_draft": save_as_draft}
        try:
            resp = app().admin.process_next(payload)
        except AdminHTTPError as exc:
            if exc.status_code == 404:
                raise ToolError(f"Book {book_id}'s queue is empty — nothing to resume.") from exc
            if exc.status_code == 409:
                raise ToolError(f"Cannot start book {book_id}: {exc.detail}") from exc
            raise
        return json_out({"started": True, "book_id": book_id,
                         "options": {k: v for k, v in payload.items() if v not in (None, False)},
                         "response": resp})
