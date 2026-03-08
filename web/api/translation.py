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

    # Run translation in a background thread so the event loop stays free
    def run():
        try:
            _web_interface.run_translation()
        except Exception as e:
            _job_manager.status = "error"
            _job_manager.error = str(e)
            _job_manager.send_message_sync({
                "type": "error",
                "message": str(e),
            })
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
    _job_manager.submit_review(req.entities)
    return {"status": "ok"}


@router.post("/api/translate/skip-review")
async def skip_review():
    if _job_manager.status != "awaiting_review":
        raise HTTPException(status_code=409, detail="Not waiting for entity review.")
    _job_manager.skip_review()
    return {"status": "ok"}


@router.get("/api/translate/status")
async def get_status():
    return {
        "status": _job_manager.status,
        "is_running": _job_manager.is_running,
        "error": _job_manager.error,
    }


@router.post("/api/translate/cancel")
async def cancel_translation():
    """
    Best-effort cancel: unblock the review event so the thread can finish.
    Actual mid-chunk cancellation is not supported (would require provider changes).
    """
    if _job_manager.status == "awaiting_review":
        _job_manager.skip_review()
    _job_manager.is_running = False
    _job_manager.status = "idle"
    return {"status": "cancelled"}
