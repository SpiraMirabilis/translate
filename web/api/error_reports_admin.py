"""
Admin endpoints for triaging reader-submitted error reports.

Requires authentication (handled by AuthMiddleware). Registered in the admin
process only — the public process accepts reports but never lists them.
"""
import datetime
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/error-reports")

_db = None

STATUSES = ("new", "reviewed", "resolved", "dismissed")


def init(db_manager):
    global _db
    _db = db_manager


class ErrorReportUpdate(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None


@router.get("")
def list_error_reports(status: Optional[str] = None, book_id: Optional[int] = None):
    reports = _db.list_error_reports(status=status, book_id=book_id)
    return {"items": reports, "count": len(reports)}


@router.get("/count")
def count_error_reports(status: Optional[str] = None):
    return {"count": _db.count_error_reports(status=status)}


@router.get("/{report_id}")
def get_error_report(report_id: int):
    report = _db.get_error_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Error report not found")
    return report


@router.put("/{report_id}")
def update_error_report(report_id: int, req: ErrorReportUpdate):
    report = _db.get_error_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Error report not found")

    updates = {}
    if req.status is not None:
        if req.status not in STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        updates["status"] = req.status
        # Naive server-local, deliberately — matches translation_date and the
        # rest of the admin timestamps.
        updates["reviewed_at"] = (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if req.status != "new" else None
        )
    if req.admin_notes is not None:
        updates["admin_notes"] = req.admin_notes

    if updates:
        _db.update_error_report(report_id, updates)
    return {"status": "ok"}


@router.delete("/{report_id}")
def delete_error_report(report_id: int):
    report = _db.get_error_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Error report not found")
    _db.delete_error_report(report_id)
    return {"status": "ok"}
