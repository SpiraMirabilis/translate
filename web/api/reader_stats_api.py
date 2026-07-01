"""
Reader activity stats endpoint (admin, session-auth via AuthMiddleware).

Returns the same aggregated data as the reader_stats.py CLI, shaped for the
web GUI.
"""
import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from reader_stats_core import collect_reader_stats, parse_duration, resolve_ips

router = APIRouter(tags=["reader-stats"])

# Hard cap on IPs accepted per ip-info request, to bound DNS/IPInfo fan-out.
_MAX_IPS_PER_REQUEST = 5000

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
    # resolve=False: pure SQL + aggregation, no per-IP DNS/geo. The page paints
    # immediately and enriches IPs via POST /api/reader-stats/ip-info below.
    # to_thread keeps the blocking SQLite work off the event loop.
    return await asyncio.to_thread(
        collect_reader_stats, _db_manager, duration, group_by=group_by, resolve=False
    )


class IpInfoRequest(BaseModel):
    ips: list[str]


@router.post("/api/reader-stats/ip-info")
async def get_reader_stats_ip_info(req: IpInfoRequest):
    """Resolve a batch of IPs to hostname/geo. Parallelized + cached server-side."""
    ips = req.ips[:_MAX_IPS_PER_REQUEST]
    return await asyncio.to_thread(resolve_ips, ips)
