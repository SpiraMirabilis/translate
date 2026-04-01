"""
Health check endpoint for T9 service monitoring.

Returns status of backend (FastAPI + DB) and frontend (static files).
Designed for use by the t9-watchdog systemd service.
"""
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


@router.get("/api/health")
async def health():
    checks = {}
    healthy = True

    # Backend: can we query the database?
    try:
        conn = _db_manager.get_connection()
        conn.execute("SELECT 1")
        conn.close()
        checks["database"] = "ok"
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
