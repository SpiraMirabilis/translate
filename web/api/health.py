"""
Health check endpoint for T9 service monitoring.

Returns status of backend (FastAPI + DB) and frontend (static files).
Designed for use by the t9-watchdog systemd service.
"""
import asyncio
import os
import time

from fastapi import APIRouter

router = APIRouter(tags=["health"])

_db_manager = None
_static_dir: str | None = None


def init(db_manager):
    global _db_manager, _static_dir
    _db_manager = db_manager
    _static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")


def _probe_db():
    """Synchronous DB liveness probe. Run off the event loop so a SQLite
    write lock held by an in-progress translation can't stall the whole
    server (and trip the watchdog) while it waits its turn."""
    conn = _db_manager.get_connection()
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()


@router.get("/api/health")
async def health():
    checks = {}
    healthy = True

    # Backend: can we query the database? Offload to a worker thread with a
    # timeout — a heavy translation can hold the SQLite lock for several
    # seconds, and a synchronous query here would block the event loop (and
    # every other request, including the watchdog's poll) until it frees.
    try:
        await asyncio.wait_for(asyncio.to_thread(_probe_db), timeout=5.0)
        checks["database"] = "ok"
    except asyncio.TimeoutError:
        # DB is busy/locked, not down. Report degraded but don't hang the
        # event loop — liveness (the server answered) is what matters here.
        checks["database"] = "busy (lock contention)"
    except Exception as e:
        checks["database"] = f"error: {e}"
        healthy = False

    # Frontend: are the built static files present?
    index_path = os.path.join(_static_dir, "index.html") if _static_dir else None
    if index_path and os.path.isfile(index_path):
        checks["frontend"] = "ok"
    else:
        checks["frontend"] = "missing"
        healthy = False

    status = "healthy" if healthy else "unhealthy"
    return {"status": status, "checks": checks, "timestamp": time.time()}
