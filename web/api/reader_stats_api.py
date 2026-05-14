"""
Reader activity stats endpoint (admin, session-auth via AuthMiddleware).

Returns the same aggregated data as the reader_stats.py CLI, shaped for the
web GUI.
"""
from fastapi import APIRouter, HTTPException, Query

from reader_stats_core import collect_reader_stats, parse_duration

router = APIRouter(tags=["reader-stats"])

_db_manager = None


def init(db_manager):
    global _db_manager
    _db_manager = db_manager


@router.get("/api/reader-stats")
async def get_reader_stats(
    duration: str = Query("24h"),
    group_by: str = Query("ip", regex="^(ip|book)$"),
):
    try:
        parse_duration(duration)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return collect_reader_stats(_db_manager, duration, group_by=group_by)
