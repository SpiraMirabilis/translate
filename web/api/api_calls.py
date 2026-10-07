"""API call log endpoints."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

_entity_manager = None


def init(entity_manager):
    global _entity_manager
    _entity_manager = entity_manager


class ApiCallUpdate(BaseModel):
    response_text: str


@router.get("/api/api-calls")
def list_api_call_sessions(book_id: Optional[int] = Query(None),
                           chapter_number: Optional[int] = Query(None),
                           before: Optional[int] = Query(None),
                           limit: int = Query(50, ge=1, le=200)):
    """A page of sessions, metadata only. Texts come from the session endpoint."""
    sessions, next_before = _entity_manager.list_api_call_sessions(
        book_id=book_id, chapter_number=chapter_number, before=before, limit=limit)
    return {"sessions": sessions, "next_before": next_before}


@router.get("/api/api-calls/session/{session_id}")
def get_api_call_session(session_id: str):
    calls = _entity_manager.get_api_call_session(session_id)
    if not calls:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "calls": calls}


@router.get("/api/api-calls/detail/{call_id}")
def get_api_call(call_id: int):
    row = _entity_manager.get_api_call(call_id)
    if not row:
        raise HTTPException(status_code=404, detail="API call not found")
    return row


@router.put("/api/api-calls/detail/{call_id}")
def update_api_call(call_id: int, body: ApiCallUpdate):
    ok = _entity_manager.update_api_call_response(call_id, body.response_text)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update")
    return {"status": "ok"}
