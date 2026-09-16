"""
Footnote-candidate review endpoints (admin GUI).

Candidates are LLM-collected footnote SUGGESTIONS (footnote_scan.py CLI /
footnote_scan module) awaiting human triage. These endpoints only read and
re-label them — nothing here ever places a footnote or touches chapters;
applying approved candidates stays a separate manual step (add_footnotes.py).
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.api.deps import get_book_or_404

router = APIRouter(prefix="/api")

_db = None

VALID_STATUSES = ("pending", "accepted", "rejected")


def init(entity_manager):
    global _db
    _db = entity_manager


class CandidateUpdate(BaseModel):
    status: Optional[str] = None
    term_en: Optional[str] = None
    body: Optional[str] = None


class BatchStatus(BaseModel):
    ids: List[int]
    status: str


@router.get("/footnote-candidates/books")
def candidate_books():
    """Books that have collected candidates, with per-status counts."""
    return {"books": _db.footnote_candidate_book_counts()}


@router.get("/books/{book_id}/footnote-candidates")
def list_candidates(book_id: int, status: Optional[str] = None):
    """All candidate rows for a book (optionally one status), plus the same
    review flags the CLI TUI computes: `dup` (a later repeat of a term first
    collected in an earlier chapter) and `already` (the term is already a real
    footnote anchor in this book), and a scan-coverage summary."""
    get_book_or_404(book_id)
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status filter.")
    from footnote_scan_core import dedupe_first_mention, footnoted_anchors

    # Flags are computed over ALL rows (dup/first-mention structure must not
    # change with the filter), then filtered.
    rows = _db.list_footnote_candidates(book_id)
    _, repeats = dedupe_first_mention(rows)
    dup_ids = {d["id"] for dups in repeats.values() for d in dups}
    already = footnoted_anchors(_db, book_id)
    for r in rows:
        r["dup"] = r["id"] in dup_ids
        r["already"] = (r.get("term_en") or "").strip().lower() in already
    if status:
        rows = [r for r in rows if r["status"] == status]

    scans = _db.get_footnote_scans(book_id)
    return {
        "candidates": rows,
        "scan": {
            "chapters_scanned": len(scans),
            "last_scanned_at": max((s["scanned_at"] for s in scans.values()),
                                   default=None),
        },
    }


@router.put("/footnote-candidates/{cand_id}")
def update_candidate(cand_id: int, payload: CandidateUpdate):
    """Review action: set status and/or inline-edit term_en / body."""
    if payload.status is not None and payload.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status.")
    if payload.body is not None and not payload.body.strip():
        raise HTTPException(status_code=422, detail="Body cannot be empty.")
    existing = _db.get_footnote_candidate(cand_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    if (payload.status, payload.term_en, payload.body) == (None, None, None):
        return {"candidate": existing}
    ok = _db.update_footnote_candidate(
        cand_id, status=payload.status, term_en=payload.term_en,
        body=payload.body)
    if not ok:
        raise HTTPException(status_code=500, detail="Update failed.")
    return {"candidate": _db.get_footnote_candidate(cand_id)}


@router.post("/books/{book_id}/footnote-candidates/batch")
def batch_status(book_id: int, payload: BatchStatus):
    """Bulk approve/reject/unmark a set of this book's candidates."""
    get_book_or_404(book_id)
    if payload.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status.")
    # Scope the ids to this book so a stray id can't relabel another book's rows.
    book_ids = {r["id"] for r in _db.list_footnote_candidates(book_id)}
    ids = [i for i in payload.ids if i in book_ids]
    updated = _db.set_footnote_candidates_status(ids, payload.status)
    return {"updated": updated}
