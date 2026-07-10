"""
Admin endpoints for managing novel translation recommendations.

Requires authentication (handled by AuthMiddleware).
"""
import datetime
import logging
import threading
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from web.services import recommendation_emails

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recommendations")

_db = None


def init(db_manager):
    global _db
    _db = db_manager


class RecommendationUpdate(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None


# Serializes the read-check-update in update_recommendation: two concurrent
# status PUTs could both observe the pre-transition status and double-send
# the acceptance email.
_status_lock = threading.Lock()


class RecommendationEmail(BaseModel):
    message: str


@router.get("")
def list_recommendations(status: Optional[str] = None):
    recs = _db.list_recommendations(status=status)
    return {"items": recs, "count": len(recs)}


@router.get("/count")
def count_recommendations(status: Optional[str] = None):
    count = _db.count_recommendations(status=status)
    return {"count": count}


@router.get("/{rec_id}")
def get_recommendation(rec_id: int):
    rec = _db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return rec


@router.put("/{rec_id}")
def update_recommendation(rec_id: int, req: RecommendationUpdate, background_tasks: BackgroundTasks):
    with _status_lock:
        rec = _db.get_recommendation(rec_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Recommendation not found")

        updates = {}
        notify_status = None
        if req.status is not None:
            if req.status not in ("new", "reviewed", "accepted", "dismissed"):
                raise HTTPException(status_code=400, detail="Invalid status")
            updates["status"] = req.status
            if req.status != "new":
                # Naive server-local time — the app-wide convention (translation_date,
                # published_at); utcnow() was offset from every other timestamp.
                updates["reviewed_at"] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # Auto-email the requester only when the status actually transitions
            # into accepted/dismissed (skip no-op re-saves of the same status).
            if req.status in ("accepted", "dismissed") and req.status != rec.get("status"):
                notify_status = req.status
        if req.admin_notes is not None:
            updates["admin_notes"] = req.admin_notes

        if updates:
            _db.update_recommendation(rec_id, updates)

    if notify_status:
        background_tasks.add_task(
            recommendation_emails.send_status_change, _db, rec_id, notify_status)

    return {"status": "ok"}


@router.post("/{rec_id}/email")
def email_requester(rec_id: int, req: RecommendationEmail):
    """Send an admin-composed message to the requester (e.g. "we need more info").

    Sent synchronously so the admin sees whether it went out, was blocked by an
    unsubscribe, or failed.
    """
    rec = _db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if not (req.message or "").strip():
        raise HTTPException(status_code=400, detail="Message is empty.")

    ok, detail = recommendation_emails.send_admin_message(_db, rec_id, req.message)
    return {"status": "ok", "sent": ok, "detail": detail}


@router.delete("/{rec_id}")
def delete_recommendation(rec_id: int):
    rec = _db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    _db.delete_recommendation(rec_id)
    return {"status": "ok"}


# ------------------------------------------------------------------
# Email replies (ingested from the editor mbox by the mail-monitor daemon).
# Static /replies/* routes are declared before /{rec_id}/replies.
# ------------------------------------------------------------------

@router.get("/replies/unread_count")
def unread_replies_count():
    return {"count": _db.count_unread_replies()}


@router.get("/replies/unmatched")
def unmatched_replies():
    items = _db.list_unmatched_replies()
    return {"items": items, "count": len(items)}


@router.get("/{rec_id}/replies")
def list_replies(rec_id: int):
    rec = _db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    items = _db.list_replies(rec_id)
    return {"items": items, "count": len(items)}


@router.post("/{rec_id}/replies/read")
def mark_replies_read(rec_id: int):
    _db.mark_replies_read(rec_id)
    return {"status": "ok"}
