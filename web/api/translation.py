"""
Translation API endpoints + WebSocket.
"""
import asyncio
import threading
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List

from translation_engine import TranslationCancelled

router = APIRouter()

# Injected by app.py
_web_interface = None
_job_manager = None


def init(web_interface, job_manager):
    global _web_interface, _job_manager
    _web_interface = web_interface
    _job_manager = job_manager


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
    backlog = _job_manager.add_websocket(websocket, loop)
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
        _job_manager.remove_websocket(websocket)


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


class JsonFixRequest(BaseModel):
    # The frontend posts the edited JSON under the key "json" (JsonFixPanel.jsx).
    # The old pydantic-v1 `class Config: fields` alias was silently ignored by
    # pydantic v2, so hand-fixed JSON never reached the engine — this Field
    # alias is the v2-correct (and working) form.
    model_config = ConfigDict(populate_by_name=True)

    action: str  # "retry" | "fix" | "abort"
    fixed_json: Optional[str] = Field(default=None, alias="json")  # Only for "fix" action


class ChapterConflictRequest(BaseModel):
    decision: str  # "proceed" | "cancel" | "merge" | "renumber_existing" | "renumber_new"
    new_chapter_number: Optional[int] = None  # Required for renumber_* decisions


# ------------------------------------------------------------------
# Translation endpoints
# ------------------------------------------------------------------

@router.post("/api/translate")
async def start_translation(req: TranslateRequest):
    if _job_manager.is_running:
        raise HTTPException(status_code=409, detail="A translation is already running.")

    lines = req.text.splitlines()
    if not lines:
        raise HTTPException(status_code=400, detail="No text provided.")

    # Configure the job
    _job_manager.clear_cancel()
    _job_manager.pending_text = lines
    _job_manager.book_id = req.book_id
    _job_manager.chapter_number = req.chapter_number
    _job_manager.is_running = True
    _job_manager.status = "running"
    _job_manager.error = None
    _job_manager.last_result = None

    # Override models if specified
    if req.model:
        _web_interface.translator.config.translation_model = req.model
    if req.advice_model:
        _web_interface.translator.config.advice_model = req.advice_model
    _web_interface.cleaning_model = req.cleaning_model or None

    _web_interface.no_review = req.no_review
    # Mutually exclusive with no_review: defensive guard for stale clients that
    # may send both flags. UI also enforces this, but trust nothing from the wire.
    _web_interface.two_pass = req.two_pass and not req.no_review
    _web_interface.no_clean = req.no_clean
    _web_interface.stream = not req.no_stream
    _web_interface.save_as_draft = req.save_as_draft

    # Resolve book name for the activity log
    book_name = None
    if req.book_id:
        book = _web_interface.entity_manager.get_book(req.book_id)
        if book:
            book_name = book.get("title")

    await _job_manager.log_activity_async(
        type='start',
        message=f'Translation started: {book_name or "No book"} — Chapter {req.chapter_number or "auto"}…',
        book_id=req.book_id, chapter=req.chapter_number, book_name=book_name,
    )

    # Run translation in a background thread so the event loop stays free
    def run():
        try:
            _web_interface.run_translation()
        except TranslationCancelled:
            _job_manager.status = "idle"
            _job_manager.send_message_sync({"type": "translation_cancelled"})
        except Exception as e:
            _job_manager.status = "error"
            _job_manager.error = str(e)
            _job_manager.log_activity(type='error', message=f'Error: {e}')
            _job_manager.send_message_sync({"type": "error", "message": str(e)})
        finally:
            _job_manager.is_running = False
            if _job_manager.status not in ("error", "idle", "awaiting_review", "awaiting_json_fix", "awaiting_chapter_conflict"):
                _job_manager.status = "complete"

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return {"status": "started"}


@router.post("/api/translate/submit-review")
async def submit_review(req: ReviewSubmitRequest):
    if _job_manager.status != "awaiting_review":
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
        await _job_manager.log_activity_async(
            type='entities_accepted', message='New entities:',
            entities=[{'name': e['untranslated'], 'label': f"{e['untranslated']} → {e['translation']}"} for e in accepted],
        )
    for e in edited:
        await _job_manager.log_activity_async(
            type='entity_edited', message='Entity edited:',
            entities=[{'name': e['untranslated'], 'label': f'{e["untranslated"]} — "{e["from"]}" → "{e["to"]}"'}],
        )
    if deleted:
        await _job_manager.log_activity_async(
            type='entity_deleted', message='Entities deleted:',
            entities=[{'name': n, 'label': n} for n in deleted],
        )
    await _job_manager.log_activity_async(type='info', message='Review submitted — resuming translation…')

    _job_manager.submit_review(req.entities)
    return {"status": "ok"}


@router.post("/api/translate/skip-review")
async def skip_review():
    if _job_manager.status != "awaiting_review":
        raise HTTPException(status_code=409, detail="Not waiting for entity review.")
    await _job_manager.log_activity_async(type='info', message='Entity review skipped — resuming translation…')
    _job_manager.skip_review()
    return {"status": "ok"}


@router.post("/api/translate/submit-json-fix")
async def submit_json_fix(req: JsonFixRequest):
    if _job_manager.status != "awaiting_json_fix":
        raise HTTPException(status_code=409, detail="Not waiting for JSON fix.")

    action_labels = {"retry": "Retrying chunk…", "fix": "Manual JSON fix submitted — resuming…", "abort": "Translation aborted by user."}
    await _job_manager.log_activity_async(
        type='json_fix' if req.action != 'abort' else 'info',
        message=action_labels.get(req.action, f'JSON fix action: {req.action}'),
    )

    _job_manager.submit_json_fix({"action": req.action, "json": req.fixed_json})
    return {"status": "ok"}


@router.post("/api/translate/resolve-chapter-conflict")
async def resolve_chapter_conflict(req: ChapterConflictRequest):
    if _job_manager.status != "awaiting_chapter_conflict":
        raise HTTPException(status_code=409, detail="Not waiting for chapter conflict resolution.")
    valid_decisions = ("proceed", "cancel", "merge", "renumber_existing", "renumber_new", "insert_shift")
    if req.decision not in valid_decisions:
        raise HTTPException(status_code=400, detail=f"decision must be one of {valid_decisions}.")
    if req.decision in ("renumber_existing", "renumber_new"):
        if req.new_chapter_number is None or req.new_chapter_number < 1:
            raise HTTPException(status_code=400, detail="new_chapter_number must be a positive integer for renumber decisions.")

    pending = _job_manager.pending_chapter_conflict or {}
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
    await _job_manager.log_activity_async(
        type='info', message=f'Chapter {ch}: {label_map[req.decision]}',
        book_id=pending.get("book_id"), chapter=ch, book_name=book_name,
    )

    _job_manager.submit_chapter_conflict(req.decision, req.new_chapter_number)
    return {"status": "ok"}


@router.get("/api/translate/status")
async def get_status():
    result = {
        "status": _job_manager.status,
        "is_running": _job_manager.is_running,
        "error": _job_manager.error,
        "auto_process": _job_manager.auto_process,
    }
    if _job_manager.status == "awaiting_review" and _job_manager.pending_review:
        result["pending_review"] = _job_manager.pending_review
    if _job_manager.status == "awaiting_json_fix" and _job_manager.pending_json_fix:
        result["pending_json_fix"] = _job_manager.pending_json_fix
    if _job_manager.status == "awaiting_chapter_conflict" and _job_manager.pending_chapter_conflict:
        result["pending_chapter_conflict"] = _job_manager.pending_chapter_conflict
    return result


@router.post("/api/translate/cancel")
async def cancel_translation():
    """
    Cancel the running translation. Sets the cooperative-cancel flag (the engine
    polls it between and mid-chunk and raises TranslationCancelled), stops the
    auto-process loop, and unblocks any pause the thread is parked on (entity
    review / JSON fix / chapter conflict) so it can reach the next cancel check.
    """
    # Signal the engine to stop at its next cancellation checkpoint. This is the
    # key fix: previously the thread kept streaming and the backend treated the
    # interruption as a transient failure and silently retried.
    _job_manager.request_cancel()

    if _job_manager.auto_process:
        _job_manager.stop_auto_process()
    if _job_manager.status == "awaiting_review":
        _job_manager.skip_review()
    if _job_manager.status == "awaiting_json_fix":
        _job_manager.submit_json_fix({"action": "abort"})
    if _job_manager.status == "awaiting_chapter_conflict":
        _job_manager.submit_chapter_conflict("cancel")
    _job_manager.status = "idle"
    await _job_manager.log_activity_async(type='info', message='Translation cancelled.')
    return {"status": "cancelled"}
