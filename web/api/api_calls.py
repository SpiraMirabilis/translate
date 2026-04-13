"""API call log endpoints."""
from itertools import groupby
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
async def list_all_api_calls(book_id: Optional[int] = Query(None)):
    rows = _entity_manager.get_all_api_calls(book_id=book_id)
    sessions = []
    seen_sessions = {}
    for row in rows:
        sid = row["session_id"]
        if sid not in seen_sessions:
            seen_sessions[sid] = {
                "session_id": sid,
                "book_id": row["book_id"],
                "book_title": row.get("book_title", ""),
                "chapter_number": row["chapter_number"],
                "model_name": row["model_name"],
                "provider": row["provider"],
                "created_at": row["created_at"],
                "total_chunks": row["total_chunks"],
                "calls": [],
            }
            sessions.append(seen_sessions[sid])
        seen_sessions[sid]["calls"].append(row)
    return {"sessions": sessions}


@router.get("/api/api-calls/{book_id}")
async def list_api_calls(book_id: int, chapter_number: Optional[int] = Query(None)):
    rows = _entity_manager.get_api_calls(book_id, chapter_number=chapter_number)
    # Group by session_id, preserving the DB ordering (newest sessions first)
    sessions = []
    seen_sessions = {}
    for row in rows:
        sid = row["session_id"]
        if sid not in seen_sessions:
            seen_sessions[sid] = {
                "session_id": sid,
                "chapter_number": row["chapter_number"],
                "model_name": row["model_name"],
                "provider": row["provider"],
                "created_at": row["created_at"],
                "total_chunks": row["total_chunks"],
                "calls": [],
            }
            sessions.append(seen_sessions[sid])
        seen_sessions[sid]["calls"].append(row)
    return {"sessions": sessions}


@router.get("/api/api-calls/detail/{call_id}")
async def get_api_call(call_id: int):
    row = _entity_manager.get_api_call(call_id)
    if not row:
        raise HTTPException(status_code=404, detail="API call not found")
    return row


@router.put("/api/api-calls/detail/{call_id}")
async def update_api_call(call_id: int, body: ApiCallUpdate):
    ok = _entity_manager.update_api_call_response(call_id, body.response_text)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update")
    return {"status": "ok"}
