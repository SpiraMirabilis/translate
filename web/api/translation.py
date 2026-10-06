"""
Translation API endpoints + WebSocket.
"""
import asyncio
import threading
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List

from translation_engine import TranslationCancelled
from web.services.job_manager import JobBusyError, JobCapacityError

router = APIRouter()

# Injected by app.py
_make_web_interface = None
_registry = None
_entity_manager = None


def init(make_web_interface, job_registry, entity_manager):
    global _make_web_interface, _registry, _entity_manager
    _make_web_interface = make_web_interface
    _registry = job_registry
    _entity_manager = entity_manager


def _begin_job_or_409(book_id):
    """Claim this book's translation slot, or fail with a reason the UI can show."""
    try:
        return _registry.begin(book_id)
    except JobBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except JobCapacityError as e:
        raise HTTPException(status_code=409, detail=str(e))


def _resolve_job(book_id=None, awaiting=None):
    """Find the job a control request refers to.

    `book_id` names it outright. Older clients (and the UI before it learned
    about multiple jobs) send nothing, so fall back to the only job that is
    actually waiting on this prompt — unambiguous whenever one job is parked,
    which is the only time these endpoints are callable at all.
    """
    if book_id is not None:
        return _registry.get(book_id)

    candidates = _registry.active()
    if awaiting is not None:
        parked = [j for j in candidates if j.status == awaiting]
        if parked:
            candidates = parked
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    raise HTTPException(
        status_code=409,
        detail="Several translations are running — say which book this is for "
               "by passing book_id.",
    )


def _job_state(job):
    """The per-job payload the status/jobs endpoints return."""
    state = {
        "job_id": job.job_id,
        "book_id": job.book_id,
        "chapter_number": job.chapter_number,
        "chapter_title": job.chapter_title,
        "status": job.status,
        "is_running": job.is_running,
        "error": job.error,
        "auto_process": job.auto_process,
        "auto_remaining": job.auto_remaining,
        "run_options": job.run_options,
    }
    if job.status == "awaiting_review" and job.pending_review:
        state["pending_review"] = job.pending_review
    if job.status == "awaiting_json_fix" and job.pending_json_fix:
        state["pending_json_fix"] = job.pending_json_fix
    if job.status == "awaiting_chapter_conflict" and job.pending_chapter_conflict:
        state["pending_chapter_conflict"] = job.pending_chapter_conflict
    return state


# ------------------------------------------------------------------
# WebSocket — single persistent connection for progress/events
# ------------------------------------------------------------------

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Auth check for WebSocket connections
    from web.auth import auth_required, validate_cookie, COOKIE_NAME
    if auth_required():
        cookie = websocket.cookies.get(COOKIE_NAME)
        if not cookie or not validate_cookie(cookie):
            await websocket.close(code=4401, reason="Not authenticated")
            return

    await websocket.accept()
    loop = asyncio.get_event_loop()
    backlog = _registry.hub.add_websocket(websocket, loop)
    try:
        # Catch-up replay: events (completion, errors, review prompts) that
        # fired while no tab was open. Flagged so the client can dedupe.
        for msg in backlog:
            await websocket.send_json({**msg, "replayed": True})
        while True:
            # Keep connection alive; all communication is server→client
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        # Remove exactly this socket — a stale disconnect can't kill a live one.
        _registry.hub.remove_websocket(websocket)


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------

class TranslateRequest(BaseModel):
    text: str
    book_id: Optional[int] = None
    chapter_number: Optional[int] = None
    model: Optional[str] = None
    advice_model: Optional[str] = None
    cleaning_model: Optional[str] = None
    no_review: bool = False
    two_pass: bool = False
    no_clean: bool = False
    no_stream: bool = False
    save_as_draft: bool = False  # save new chapters unpublished (publish manually later)


class ReviewSubmitRequest(BaseModel):
    # Keys match the entity category keys from entity_review_needed message.
    # Each category maps to {untranslated: {translation, deleted?, ...}}
    entities: dict
    # Decisions on the model's proposed note/gender revisions, keyed by entity id
    # (as a string) or untranslated text:
    # {"12": {"note": "edited text", "gender": "female"|null, "rejected": bool}}.
    # A null/absent gender on an entry that proposed one means the gender half was
    # declined; the note half still applies.
    # Omitted entirely by clients that predate the feature, which is read as
    # "don't touch any notes" rather than as blanket approval.
    note_updates: Optional[dict] = None
    # Which job this answers. Optional so older clients still work when only
    # one translation is parked on a prompt.
    book_id: Optional[int] = None


class JsonFixRequest(BaseModel):
    # The frontend posts the edited JSON under the key "json" (JsonFixPanel.jsx).
    # The old pydantic-v1 `class Config: fields` alias was silently ignored by
    # pydantic v2, so hand-fixed JSON never reached the engine — this Field
    # alias is the v2-correct (and working) form.
    model_config = ConfigDict(populate_by_name=True)

    action: str  # "retry" | "fix" | "abort"
    fixed_json: Optional[str] = Field(default=None, alias="json")  # Only for "fix" action
    book_id: Optional[int] = None  # which job this answers


class ChapterConflictRequest(BaseModel):
    decision: str  # "proceed" | "cancel" | "merge" | "renumber_existing" | "renumber_new"
    new_chapter_number: Optional[int] = None  # Required for renumber_* decisions
    book_id: Optional[int] = None  # which job this answers


class SkipReviewRequest(BaseModel):
    book_id: Optional[int] = None


class CancelRequest(BaseModel):
    # Omitted = cancel every running job (what the pre-multi-job UI expects).
    book_id: Optional[int] = None


# ------------------------------------------------------------------
# Translation endpoints
# ------------------------------------------------------------------

@router.post("/api/translate")
def start_translation(req: TranslateRequest):
    # Sync handler on purpose: runs in FastAPI's threadpool so the DB lookups
    # here can't stall the event loop (see the async-starvation punchlist item).
    lines = req.text.splitlines()
    if not lines:
        raise HTTPException(status_code=400, detail="No text provided.")

    job = _begin_job_or_409(req.book_id)
    job.clear_cancel()

    # This run gets its own WebInterface, engine and config clone, so the
    # per-request model overrides can't leak into (or be clobbered by) anyone
    # else's run. Nothing to save or restore afterwards.
    web_interface = _make_web_interface(
        job, translation_model=req.model, advice_model=req.advice_model)

    try:
        # Configure the job
        job.pending_text = lines
        job.book_id = req.book_id
        job.chapter_number = req.chapter_number
        job.status = "running"
        job.error = None
        job.last_result = None

        web_interface.cleaning_model = req.cleaning_model or None
        web_interface.no_review = req.no_review
        # Mutually exclusive with no_review: defensive guard for stale clients that
        # may send both flags. UI also enforces this, but trust nothing from the wire.
        web_interface.two_pass = req.two_pass and not req.no_review
        web_interface.no_clean = req.no_clean
        web_interface.stream = not req.no_stream
        web_interface.save_as_draft = req.save_as_draft

        # Resolve book name for the activity log
        book_name = None
        if req.book_id:
            book = _entity_manager.get_book(req.book_id)
            if book:
                book_name = book.get("title")

        job.log_activity(
            type='start',
            message=f'Translation started: {book_name or "No book"} — Chapter {req.chapter_number or "auto"}…',
            book_id=req.book_id, chapter=req.chapter_number, book_name=book_name,
        )
    except BaseException:
        # A failure before the worker thread owns the slot would leave this
        # book permanently un-startable (every later request 409s).
        _registry.end(req.book_id)
        raise

    # Run translation in a background thread so the event loop stays free
    def run():
        try:
            web_interface.run_translation()
        except TranslationCancelled:
            job.status = "idle"
            job.send_message_sync({"type": "translation_cancelled"})
        except Exception as e:
            job.status = "error"
            job.error = str(e)
            job.log_activity(type='error', message=f'Error: {e}')
            job.send_message_sync({"type": "error", "message": str(e)})
        finally:
            _registry.end(req.book_id)
            if job.status not in ("error", "idle", "awaiting_review", "awaiting_json_fix", "awaiting_chapter_conflict"):
                job.status = "complete"
            # Run boundary: summarize this run's module transforms. Scoped to
            # this book so a concurrent job's pending summaries aren't flushed
            # early and attributed to this run's boundary.
            from modules import module_activity
            module_activity.flush(book_id=job.book_id)
            _registry.prune()

    try:
        thread = threading.Thread(
            target=run, daemon=True, name=f"translate-book{req.book_id}")
        thread.start()
    except BaseException:
        _registry.end(req.book_id)
        raise

    return {"status": "started", "job_id": job.job_id, "book_id": job.book_id}


@router.post("/api/translate/submit-review")
async def submit_review(req: ReviewSubmitRequest):
    job = _resolve_job(req.book_id, awaiting="awaiting_review")
    if job is None or job.status != "awaiting_review":
        raise HTTPException(status_code=409, detail="Not waiting for entity review.")

    # Log entity changes before unblocking the translation thread
    accepted, edited, deleted = [], [], []
    for cat, cat_entities in req.entities.items():
        for untranslated, data in cat_entities.items():
            if data.get('deleted'):
                deleted.append(untranslated)
            elif data.get('incorrect_translation'):
                edited.append({'untranslated': untranslated, 'from': data['incorrect_translation'], 'to': data.get('translation', '')})
            else:
                accepted.append({'untranslated': untranslated, 'translation': data.get('translation', '')})

    if accepted:
        await job.log_activity_async(
            type='entities_accepted', message='New entities:',
            entities=[{'name': e['untranslated'], 'label': f"{e['untranslated']} → {e['translation']}"} for e in accepted],
        )
    for e in edited:
        await job.log_activity_async(
            type='entity_edited', message='Entity edited:',
            entities=[{'name': e['untranslated'], 'label': f'{e["untranslated"]} — "{e["from"]}" → "{e["to"]}"'}],
        )
    if deleted:
        await job.log_activity_async(
            type='entity_deleted', message='Entities deleted:',
            entities=[{'name': n, 'label': n} for n in deleted],
        )
    if req.note_updates:
        kept = [d for d in req.note_updates.values()
                if isinstance(d, dict) and not d.get('rejected')]
        rejected = len(req.note_updates) - len(kept)
        # One entry may revise the note, correct the gender, or both, so the
        # counts are per change and not per entry.
        n_notes = sum(1 for d in kept if (d.get('note') or '').strip())
        n_genders = sum(1 for d in kept if (d.get('gender') or '').strip())
        if n_notes:
            await job.log_activity_async(
                type='entity_note_updated',
                message=f'{n_notes} entity note{"" if n_notes == 1 else "s"} revised.',
            )
        if n_genders:
            await job.log_activity_async(
                type='entity_note_updated',
                message=f'{n_genders} entity gender{"" if n_genders == 1 else "s"} corrected.',
            )
        if rejected:
            await job.log_activity_async(
                type='info',
                message=f'{rejected} proposed entity change{"" if rejected == 1 else "s"} rejected.',
            )

    await job.log_activity_async(type='info', message='Review submitted — resuming translation…')

    payload = dict(req.entities)
    if req.note_updates is not None:
        payload["note_updates"] = req.note_updates
    job.submit_review(payload)
    return {"status": "ok"}


@router.post("/api/translate/skip-review")
async def skip_review(req: SkipReviewRequest = SkipReviewRequest()):
    job = _resolve_job(req.book_id, awaiting="awaiting_review")
    if job is None or job.status != "awaiting_review":
        raise HTTPException(status_code=409, detail="Not waiting for entity review.")
    await job.log_activity_async(type='info', message='Entity review skipped — resuming translation…')
    job.skip_review()
    return {"status": "ok"}


@router.post("/api/translate/submit-json-fix")
async def submit_json_fix(req: JsonFixRequest):
    job = _resolve_job(req.book_id, awaiting="awaiting_json_fix")
    if job is None or job.status != "awaiting_json_fix":
        raise HTTPException(status_code=409, detail="Not waiting for JSON fix.")

    action_labels = {"retry": "Retrying chunk…", "fix": "Manual JSON fix submitted — resuming…", "abort": "Translation aborted by user."}
    await job.log_activity_async(
        type='json_fix' if req.action != 'abort' else 'info',
        message=action_labels.get(req.action, f'JSON fix action: {req.action}'),
    )

    job.submit_json_fix({"action": req.action, "json": req.fixed_json})
    return {"status": "ok"}


@router.post("/api/translate/resolve-chapter-conflict")
async def resolve_chapter_conflict(req: ChapterConflictRequest):
    job = _resolve_job(req.book_id, awaiting="awaiting_chapter_conflict")
    if job is None or job.status != "awaiting_chapter_conflict":
        raise HTTPException(status_code=409, detail="Not waiting for chapter conflict resolution.")
    valid_decisions = ("proceed", "cancel", "merge", "renumber_existing", "renumber_new", "insert_shift")
    if req.decision not in valid_decisions:
        raise HTTPException(status_code=400, detail=f"decision must be one of {valid_decisions}.")
    if req.decision in ("renumber_existing", "renumber_new"):
        if req.new_chapter_number is None or req.new_chapter_number < 1:
            raise HTTPException(status_code=400, detail="new_chapter_number must be a positive integer for renumber decisions.")

    pending = job.pending_chapter_conflict or {}
    ch = pending.get("chapter_number")
    book_name = pending.get("book_title")
    label_map = {
        "proceed":            "Overwriting existing chapter…",
        "merge":              "Appending new source to existing chapter and retranslating…",
        "cancel":             "Skipping chapter — queue item dropped.",
        "renumber_existing":  f"Renumbering existing chapter to {req.new_chapter_number}…",
        "renumber_new":       f"Renumbering incoming chapter to {req.new_chapter_number}…",
        "insert_shift":       f"Inserting at chapter {(ch or 0) + 1} and shifting later queue items up by 1…",
    }
    await job.log_activity_async(
        type='info', message=f'Chapter {ch}: {label_map[req.decision]}',
        book_id=pending.get("book_id"), chapter=ch, book_name=book_name,
    )

    job.submit_chapter_conflict(req.decision, req.new_chapter_number)
    return {"status": "ok"}


# Aggregate status precedence: a job needing a human outranks one merely
# running, so a single-valued summary surfaces the thing that needs attention.
# Every value here is outside translation_status.py's IDLE_STATUSES, so the CLI
# guard still reads "something is happening" correctly.
_STATUS_PRECEDENCE = (
    "awaiting_chapter_conflict",
    "awaiting_json_fix",
    "awaiting_review",
    "waiting",
    "running",
)


@router.get("/api/translate/jobs")
async def list_jobs():
    """Every live translation, keyed by book — what the multi-job UI hydrates from."""
    active = _registry.active()
    return {
        "jobs": [_job_state(j) for j in active],
        "running": len(active),
        "max_concurrent": _registry.max_concurrent(),
    }


@router.get("/api/translate/status")
async def get_status():
    """Aggregate status, plus the per-book breakdown.

    The top-level status/is_running/auto_process fields are kept as aggregates
    because translation_status.py (the CLI guard run before entity-repair
    sweeps) reads them, and a bare `jobs` list would silently report "idle" to
    every existing caller.
    """
    active = _registry.active()
    statuses = {j.status for j in active}
    status = next((s for s in _STATUS_PRECEDENCE if s in statuses), "idle")

    result = {
        "status": status,
        "is_running": bool(active),
        "error": next((j.error for j in active if j.error), None),
        "auto_process": any(j.auto_process for j in active),
        "jobs": {str(j.book_id): _job_state(j) for j in active},
        "running": len(active),
        "max_concurrent": _registry.max_concurrent(),
    }

    # Back-compat: single-job clients read these at the top level. Only
    # meaningful when exactly one job is parked, which is when they ask.
    parked = [j for j in active if j.status.startswith("awaiting_")]
    if len(parked) == 1:
        job = parked[0]
        for key in ("pending_review", "pending_json_fix", "pending_chapter_conflict"):
            value = _job_state(job).get(key)
            if value:
                result[key] = value
    return result


def _cancel_job(job):
    """Stop one job: flag the engine, then unblock whatever it is parked on."""
    # Signal the engine to stop at its next cancellation checkpoint. Without
    # this the thread keeps streaming and the backend treats the interruption
    # as a transient failure and silently retries.
    job.request_cancel()

    if job.auto_process:
        job.stop_auto_process()
    if job.status == "awaiting_review":
        job.skip_review()
    if job.status == "awaiting_json_fix":
        job.submit_json_fix({"action": "abort"})
    if job.status == "awaiting_chapter_conflict":
        job.submit_chapter_conflict("cancel")
    job.status = "idle"


@router.post("/api/translate/cancel")
async def cancel_translation(req: CancelRequest = CancelRequest()):
    """
    Cancel a running translation. Sets the cooperative-cancel flag (the engine
    polls it between and mid-chunk and raises TranslationCancelled), stops the
    auto-process loop, and unblocks any pause the thread is parked on (entity
    review / JSON fix / chapter conflict) so it can reach the next cancel check.

    With no book_id, cancels every running job — what a client that predates
    per-book jobs means by "cancel".
    """
    if req.book_id is not None:
        job = _registry.get(req.book_id)
        targets = [job] if job is not None and job.is_running else []
    else:
        targets = _registry.active()

    if not targets:
        return {"status": "not_running", "cancelled": []}

    for job in targets:
        _cancel_job(job)
        await job.log_activity_async(type='info', message='Translation cancelled.')

    return {"status": "cancelled", "cancelled": [j.book_id for j in targets]}
