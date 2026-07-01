"""Activity log API endpoints."""
from fastapi import APIRouter, Query

router = APIRouter()

_entity_manager = None


def init(entity_manager):
    global _entity_manager
    _entity_manager = entity_manager


@router.get("/api/activity-log")
def get_activity_log(limit: int = Query(200, ge=1, le=1000)):
    entries = _entity_manager.get_activity_log(limit=limit)
    return {"entries": entries}


@router.delete("/api/activity-log")
def clear_activity_log():
    _entity_manager.clear_activity_log()
    return {"status": "ok"}
