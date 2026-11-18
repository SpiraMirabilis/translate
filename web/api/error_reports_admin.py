"""
Admin endpoints for triaging reader-submitted error reports.

Requires authentication (handled by AuthMiddleware). Registered in the admin
process only — the public process accepts reports but never lists them.
"""
import datetime
import json
import logging
import threading
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/error-reports")

_db = None
_config = None

STATUSES = ("new", "reviewed", "resolved", "dismissed")


def init(db_manager, config=None):
    global _db, _config
    _db = db_manager
    _config = config
    # A 'running' auto-fix at startup is an orphan of the previous process.
    _db.fail_stale_error_report_autofix()


def _decode(report):
    """autofix_result is stored as JSON text; hand the UI an object."""
    if report and isinstance(report.get("autofix_result"), str):
        try:
            report["autofix_result"] = json.loads(report["autofix_result"])
        except ValueError:
            report["autofix_result"] = None
    return report


class ErrorReportUpdate(BaseModel):
    status: Optional[str] = None
    admin_notes: Optional[str] = None


@router.get("")
def list_error_reports(status: Optional[str] = None, book_id: Optional[int] = None):
    reports = [_decode(r) for r in _db.list_error_reports(status=status, book_id=book_id)]
    return {"items": reports, "count": len(reports)}


@router.get("/count")
def count_error_reports(status: Optional[str] = None):
    return {"count": _db.count_error_reports(status=status)}


@router.get("/{report_id}")
def get_error_report(report_id: int):
    report = _db.get_error_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Error report not found")
    return _decode(report)


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


# ------------------------------------------------------------------
# Auto-fix (web/services/report_autofix.py): the model proposes, the
# admin previews and applies. The model call runs in a daemon thread so
# the POST returns at once (the polish precedent — Apache's Timeout 300).
# ------------------------------------------------------------------

@router.post("/{report_id}/autofix")
def start_autofix(report_id: int):
    from web.services import report_autofix

    report = _db.get_error_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Error report not found")
    if report.get("chapter_number") is None:
        raise HTTPException(status_code=400,
                            detail="Book-wide reports can't be auto-fixed — there is no chapter to edit.")
    if not _db.get_chapter(book_id=report["book_id"], chapter_number=report["chapter_number"]):
        raise HTTPException(status_code=404, detail=f"Chapter {report['chapter_number']} not found.")
    spec = report_autofix.model_spec()
    # One run per report: a second click re-attaches instead of paying twice.
    if report.get("autofix_status") == "running":
        return {"status": "running", "model": spec}
    if _config is None:
        raise HTTPException(status_code=503, detail="Auto-fix isn't configured in this process.")
    _db.set_error_report_autofix(report_id, "running")
    threading.Thread(
        target=report_autofix.run_autofix,
        args=(_db, _config, report_id),
        kwargs={"spec": spec},
        name=f"report-autofix-{report_id}",
        daemon=True,
    ).start()
    return {"status": "running", "model": spec}


class AutofixFootnote(BaseModel):
    id: int
    anchor: Optional[str] = None
    body: Optional[str] = None


class AutofixApply(BaseModel):
    edit_ids: Optional[List[int]] = None          # None = every proposed edit
    footnotes: Optional[List[AutofixFootnote]] = None  # None = every proposed footnote
    entity_update_ids: Optional[List[int]] = None  # opt-in; None = none
    notes: Optional[str] = None                    # None = the model's explanation


@router.post("/{report_id}/autofix/apply")
def apply_autofix(report_id: int, req: AutofixApply):
    from web.api import deps
    from web.services import report_autofix

    report = _db.get_error_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Error report not found")
    if report.get("chapter_number") is None:
        raise HTTPException(status_code=400, detail="Book-wide reports have nothing to apply.")
    with deps.chapter_save_lock(report["book_id"], report["chapter_number"]):
        try:
            applied = report_autofix.apply_proposal(
                _db, report,
                edit_ids=req.edit_ids,
                footnotes=[f.model_dump() for f in req.footnotes] if req.footnotes is not None else None,
                entity_update_ids=req.entity_update_ids,
                notes=req.notes,
            )
        except report_autofix.ChapterChanged as e:
            raise HTTPException(status_code=409, detail=str(e))
        except report_autofix.AutofixError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "applied": applied}
