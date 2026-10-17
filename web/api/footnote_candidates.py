"""
Footnote-candidate review endpoints (admin GUI).

Candidates are LLM-collected footnote SUGGESTIONS (footnote_scan.py CLI /
footnote_scan module) awaiting human triage. The inline scan places its own
candidates as real footnotes (status "applied", linked by footnote_id), so a
review action here may touch a chapter: rejecting a placed candidate deletes
its footnote, editing one rewrites or re-anchors it, and "place" puts an
unplaced one in. All of that goes through footnote_scan_core's curation
helpers, shared with the MCP tools and the CLI TUI.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.api.deps import get_book_or_404

router = APIRouter(prefix="/api")

_db = None

# Filterable. "applied" is set only by the auto-apply code, never by a client.
VALID_STATUSES = ("pending", "applied", "accepted", "rejected")
REVIEW_STATUSES = ("pending", "accepted", "rejected")


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
    from footnote_scan_core import dedupe_first_mention, footnote_link_status

    # Flags are computed over ALL rows (dup/first-mention structure must not
    # change with the filter), then filtered.
    rows = _db.list_footnote_candidates(book_id)
    _, repeats = dedupe_first_mention(rows)
    dup_ids = {d["id"] for dups in repeats.values() for d in dups}
    footnotes = _db.get_book_footnotes(book_id)
    by_id = {f["id"]: f for f in footnotes}
    # A placed row's own footnote doesn't make it "already footnoted".
    own = {r.get("footnote_id") for r in rows if r.get("footnote_id")}
    already = {(f.get("anchor") or "").strip().lower()
               for f in footnotes if f["id"] not in own} - {""}
    for r in rows:
        r["dup"] = r["id"] in dup_ids
        r["footnote_status"] = footnote_link_status(r, by_id)
        r["already"] = (not r["footnote_status"]
                        and (r.get("term_en") or "").strip().lower() in already)
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
    """Review action: set status and/or inline-edit term_en / body. On a
    placed candidate the footnote follows (see module docstring)."""
    from footnote_scan_core import (CandidateActionError, decide_candidates,
                                    edit_candidate)
    if payload.status is not None and payload.status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status.")
    if payload.body is not None and not payload.body.strip():
        raise HTTPException(status_code=422, detail="Body cannot be empty.")
    existing = _db.get_footnote_candidate(cand_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    if (payload.status, payload.term_en, payload.body) == (None, None, None):
        return {"candidate": existing}
    try:
        if payload.term_en is not None or payload.body is not None:
            edit_candidate(_db, cand_id, term_en=payload.term_en, body=payload.body)
        if payload.status is not None:
            decide_candidates(_db, [cand_id], payload.status)
    except CandidateActionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"candidate": _db.get_footnote_candidate(cand_id)}


@router.post("/footnote-candidates/{cand_id}/place")
def place(cand_id: int):
    """Place an unplaced candidate as a footnote in its chapter (anchored on
    its term_en; first match, skipping a heading line 0). 422 says why when it can't."""
    from footnote_scan_core import CandidateActionError, place_candidate
    if not _db.get_footnote_candidate(cand_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")
    try:
        row = place_candidate(_db, cand_id)
    except CandidateActionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"candidate": row}


@router.post("/books/{book_id}/footnote-candidates/batch")
def batch_status(book_id: int, payload: BatchStatus):
    """Bulk approve/reject/unmark a set of this book's candidates. Rejecting
    a placed candidate removes its footnote from the chapter."""
    from footnote_scan_core import decide_candidates
    get_book_or_404(book_id)
    if payload.status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status.")
    # Scope the ids to this book so a stray id can't relabel another book's rows.
    book_ids = {r["id"] for r in _db.list_footnote_candidates(book_id)}
    ids = [i for i in payload.ids if i in book_ids]
    updated = decide_candidates(_db, ids, payload.status)
    return {"updated": updated}
