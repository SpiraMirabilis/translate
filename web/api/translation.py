"""
Translation API endpoints + WebSocket.
"""
import asyncio
import threading
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional, List

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
    await websocket.accept()
    loop = asyncio.get_event_loop()
    _job_manager.set_websocket(websocket, loop)
    try:
        while True:
            # Keep connection alive; all communication is server→client
            await websocket.receive_text()
    except WebSocketDisconnect:
        _job_manager.websocket = None


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
    no_clean: bool = False
    no_repair: bool = False


class ReviewSubmitRequest(BaseModel):
    # Keys match the entity category keys from entity_review_needed message.
    # Each category maps to {untranslated: {translation, deleted?, ...}}
    entities: dict


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
    _web_interface.no_clean = req.no_clean
    _web_interface.no_repair = req.no_repair

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
        except Exception as e:
            _job_manager.status = "error"
            _job_manager.error = str(e)
            _job_manager.log_activity(type='error', message=f'Error: {e}')
            _job_manager.send_message_sync({"type": "error", "message": str(e)})
        finally:
            _job_manager.is_running = False
            if _job_manager.status not in ("error", "awaiting_review"):
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
    return result


@router.post("/api/translate/cancel")
async def cancel_translation():
    """
    Best-effort cancel: unblock the review event so the thread can finish.
    Actual mid-chunk cancellation is not supported (would require provider changes).
    """
    if _job_manager.auto_process:
        _job_manager.stop_auto_process()
    if _job_manager.status == "awaiting_review":
        _job_manager.skip_review()
    _job_manager.is_running = False
    _job_manager.status = "idle"
    await _job_manager.log_activity_async(type='info', message='Translation cancelled.')
    return {"status": "cancelled"}
