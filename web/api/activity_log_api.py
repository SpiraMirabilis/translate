"""Activity log API endpoints."""
from fastapi import APIRouter

router = APIRouter()

_entity_manager = None


def init(entity_manager):
    global _entity_manager
    _entity_manager = entity_manager


@router.get("/api/activity-log")
async def get_activity_log():
    entries = _entity_manager.get_activity_log()
    return {"entries": entries}


@router.delete("/api/activity-log")
async def clear_activity_log():
    _entity_manager.clear_activity_log()
    return {"status": "ok"}
