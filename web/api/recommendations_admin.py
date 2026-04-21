"""
Admin endpoints for managing novel translation recommendations.

Requires authentication (handled by AuthMiddleware).
"""
import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/recommendations")

_db = None


def init(db_manager):
    global _db
    _db = db_manager


class RecommendationUpdate(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None


@router.get("")
async def list_recommendations(status: Optional[str] = None):
    recs = _db.list_recommendations(status=status)
    return {"items": recs, "count": len(recs)}


@router.get("/count")
async def count_recommendations(status: Optional[str] = None):
    count = _db.count_recommendations(status=status)
    return {"count": count}


@router.get("/{rec_id}")
async def get_recommendation(rec_id: int):
    rec = _db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return rec


@router.put("/{rec_id}")
async def update_recommendation(rec_id: int, req: RecommendationUpdate):
    rec = _db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    updates = {}
    if req.status is not None:
        if req.status not in ("new", "reviewed", "accepted", "dismissed"):
            raise HTTPException(status_code=400, detail="Invalid status")
        updates["status"] = req.status
        if req.status != "new":
            updates["reviewed_at"] = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    if req.admin_notes is not None:
        updates["admin_notes"] = req.admin_notes

    if updates:
        _db.update_recommendation(rec_id, updates)

    return {"status": "ok"}


@router.delete("/{rec_id}")
async def delete_recommendation(rec_id: int):
    rec = _db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    _db.delete_recommendation(rec_id)
    return {"status": "ok"}
