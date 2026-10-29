"""
Admin endpoints for NovelUpdates posting.

The work happens in the NU lane (nu_sync.NuEngine), ticked by scrape_worker.py
(t9-scraper.service). These endpoints read its state and record human
decisions — mode and baseline, a picked series, an approved new-series draft,
a resolved "check NU" — for it to act on within seconds. Admin process only.
"""
import re
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import nu_sync
from db.scrape_repo import now_iso
from web.api import scrape_api

router = APIRouter()

_db = None


def init(db_manager):
    global _db
    _db = db_manager


def _act(fn, *args, **kwargs):
    try:
        return fn(_db, *args, **kwargs)
    except nu_sync.NuActionError as e:
        raise HTTPException(status_code=409, detail=str(e))


def _book_or_404(book_id):
    book = _db.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


def _settings():
    return {k: nu_sync.setting(k) for k in nu_sync.DEFAULTS}


def _attention(rows):
    out = []
    for r in rows:
        if r.get("hold_reason"):
            out.append({"book_id": r["book_id"], "title": r["title"], "kind": r["hold_reason"],
                        "detail": r.get("hold_detail")})
        elif r["state"] in ("needs_review", "new_series", "series_submitted"):
            detail = {"needs_review": r.get("last_error") or "pick the series or declare it new",
                      "new_series": "review and submit the new-series draft",
                      "series_submitted": "post the first release by hand, then record it"}[r["state"]]
            out.append({"book_id": r["book_id"], "title": r["title"], "kind": r["state"], "detail": detail})
    return out


@router.get("/api/nu/overview")
def overview():
    rows = _db.list_book_nu()
    for r in rows:
        r["pending"] = (_db.count_unsent_published(r["book_id"], int(r["start_chapter"]))
                        if r["state"] in ("linked", "active") and r.get("start_chapter") else 0)
        r.pop("series_draft", None)
        r.pop("draft_dry_run", None)
    vocab = nu_sync.load_vocab()
    return {
        "settings": _settings(),
        "site_base": nu_sync.site_base(),
        "worker": scrape_api._worker_status(),
        "hold": nu_sync.load_hold(),
        "lane": _db.nu_lane_task(),
        "queued": _db.list_nu_tasks(statuses=("queued",), order="t.priority, t.id", limit=200),
        "recent": _db.list_nu_tasks(statuses=("done", "failed", "cancelled", "skipped"),
                                    order="t.finished_at DESC", limit=40),
        "books": rows,
        "attention": _attention(rows),
        "vocab_cached_at": (vocab or {}).get("cachedAt"),
        "last_sync": _db.last_finished_nu_task("sync"),
    }


@router.get("/api/nu/count")
def count():
    return {"count": _db.count_nu_attention() + (1 if nu_sync.load_hold() else 0)}


@router.post("/api/nu/find-all")
def find_all():
    return _act(nu_sync.find_all)


@router.post("/api/nu/sync")
def sync():
    if not _db.open_nu_task(None, "sync"):
        _db.create_nu_task(None, "sync", priority=nu_sync.PRIORITY_SYNC)
    return {"status": "queued"}


@router.post("/api/nu/vocabulary/refresh")
def refresh_vocab():
    if not _db.open_nu_task(None, "vocab"):
        _db.create_nu_task(None, "vocab", priority=nu_sync.PRIORITY_VOCAB)
    return {"status": "queued"}


@router.post("/api/nu/hold/clear")
def clear_hold():
    nu_sync.clear_hold()
    return {"hold": None}


_SHOT_RE = re.compile(r"^[A-Za-z0-9._-]+\.png$")


@router.get("/api/nu/screenshots/{name}")
def screenshot(name: str):
    if not _SHOT_RE.match(name):
        raise HTTPException(status_code=400, detail="bad screenshot name")
    try:
        data = nu_sync.make_client()._get_bytes(f"/nu/screenshots/{name}")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"screenshot unavailable: {e}")
    return Response(content=data, media_type="image/png")


@router.post("/api/nu/tasks/{task_id}/cancel")
def cancel_task(task_id: int):
    task = _db.get_nu_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] == "queued":
        _db.update_nu_task(task_id, status="cancelled", finished_at=now_iso())
    elif task["status"] in ("running", "blocked"):
        _db.update_nu_task(task_id, status="cancelling")
    else:
        raise HTTPException(status_code=409, detail=f"task is {task['status']}")
    return _db.get_nu_task(task_id)


@router.post("/api/nu/tasks/{task_id}/retry")
def retry_task(task_id: int):
    task = _db.get_nu_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] not in ("failed", "cancelled", "skipped"):
        raise HTTPException(status_code=409, detail=f"task is {task['status']}")
    _db.update_nu_task(task_id, status="queued", attempts=0, not_before=None, scraper_job_id=None,
                       last_error=None, finished_at=None)
    return _db.get_nu_task(task_id)


# -- per book ---------------------------------------------------------------

def _book_view(book_id):
    book = _book_or_404(book_id)
    state = _db.get_book_nu(book_id)
    start = state.get("start_chapter")
    vocab = nu_sync.load_vocab() if state["state"] == "new_series" else None
    draft = state.get("series_draft")
    dry = state.get("draft_dry_run") or {}
    return {
        "book": {k: book.get(k) for k in ("id", "title", "nu_title", "source_title", "source_language",
                                          "is_public", "is_original", "tags", "status")},
        "state": state,
        "suggested_start": nu_sync.suggested_start(_db, book_id, state["state"]),
        "pending": _db.count_unsent_published(book_id, int(start)) if start else 0,
        "next": _db.published_chapters_from(book_id, int(start), limit=5) if start else [],
        "releases": _db.list_nu_releases(book_id, limit=30),
        "tasks": _db.list_nu_tasks(book_id=book_id, limit=15),
        "draft_dry_run_current": bool(draft) and dry.get("fingerprint") == nu_sync.draft_fingerprint(draft),
        # A submitted series awaiting approval: the title the add-release form
        # knows it by, and the chapter "Post now" would send.
        "pending_title": nu_sync.pending_title(_db, book_id, state) if state["state"] == "series_submitted" else None,
        "first_chapter": int(start or 1),
        "vocab": {k: [x["name"] for x in (vocab or {}).get(k) or []]
                  for k in ("genres", "tags", "types", "languages")} if vocab else None,
        "hold": nu_sync.load_hold(),
        "enabled": bool(nu_sync.setting("nu_enabled")),
    }


@router.get("/api/books/{book_id}/nu")
def get_book_nu(book_id: int):
    return _book_view(book_id)


class BookNuUpdate(BaseModel):
    mode: Optional[str] = None
    start_chapter: Optional[int] = None
    clear_hold: Optional[bool] = None


@router.put("/api/books/{book_id}/nu")
def update_book_nu(book_id: int, req: BookNuUpdate):
    _book_or_404(book_id)
    if req.mode is not None:
        _act(nu_sync.set_mode, book_id, req.mode, req.start_chapter)
    elif req.start_chapter is not None:
        if req.start_chapter < 1:
            raise HTTPException(status_code=400, detail="start chapter must be 1 or more")
        _db.update_book_nu(book_id, start_chapter=req.start_chapter)
    if req.clear_hold:
        nu_sync.clear_book_hold(_db, book_id)
    return _book_view(book_id)


@router.post("/api/books/{book_id}/nu/search")
def search(book_id: int):
    _book_or_404(book_id)
    _act(nu_sync.request_search, book_id)
    return _book_view(book_id)


class PickSeries(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    id: Optional[str] = None
    new: Optional[bool] = None


@router.post("/api/books/{book_id}/nu/pick")
def pick(book_id: int, req: PickSeries):
    _book_or_404(book_id)
    if req.new:
        _act(nu_sync.declare_new_series, book_id)
    elif req.id and not req.url:
        # a pending (submitted, not yet approved) series: its id from the lookup's hits
        _act(nu_sync.set_pending_series_id, book_id, req.id)
    elif req.url:
        _act(nu_sync.link_series, book_id, req.url, req.title, req.id)
    else:
        raise HTTPException(status_code=400, detail="give a series URL/slug, or new: true")
    return _book_view(book_id)


class Draft(BaseModel):
    title: str = ""
    associatedNames: List[str] = []
    description: Optional[str] = None
    type: Optional[str] = None
    genres: List[str] = []
    tags: List[str] = []
    language: Optional[str] = None
    authors: List[str] = []
    artists: List[str] = []
    year: Optional[str] = None
    statusInCoo: Optional[List[str]] = None
    licensed: Optional[bool] = None
    completelyTranslated: Optional[bool] = None
    originalPublishers: List[str] = []
    englishPublishers: List[str] = []
    coverUrl: Optional[str] = None
    unmatchedTags: List[str] = []


@router.put("/api/books/{book_id}/nu/draft")
def save_draft(book_id: int, req: Draft):
    _book_or_404(book_id)
    _act(nu_sync.save_draft, book_id, req.model_dump())
    return _book_view(book_id)


@router.post("/api/books/{book_id}/nu/draft/reset")
def reset_draft(book_id: int):
    """Rebuild the draft from the book (after editing its tags, say)."""
    book = _book_or_404(book_id)
    if _db.get_book_nu(book_id)["state"] != "new_series":
        raise HTTPException(status_code=409, detail="only a new series has a draft")
    draft = nu_sync.build_series_draft(book, nu_sync.load_vocab(), nu_sync.cover_url_for(_db, book))
    _db.update_book_nu(book_id, series_draft=draft)
    return _book_view(book_id)


@router.post("/api/books/{book_id}/nu/draft/dry-run")
def draft_dry_run(book_id: int):
    _book_or_404(book_id)
    task_id = _act(nu_sync.queue_series, book_id, True)
    return {"task_id": task_id, **_book_view(book_id)}


@router.post("/api/books/{book_id}/nu/draft/submit")
def draft_submit(book_id: int):
    _book_or_404(book_id)
    task_id = _act(nu_sync.queue_series, book_id, False)
    return {"task_id": task_id, **_book_view(book_id)}


@router.post("/api/books/{book_id}/nu/names")
def add_names(book_id: int):
    """Add our English title and both scripts of the raw title to the
    series' Associated Names on NU (merged; others' names are kept)."""
    _book_or_404(book_id)
    task_id = _act(nu_sync.queue_names, book_id)
    return {"task_id": task_id, **_book_view(book_id)}


@router.post("/api/books/{book_id}/nu/release-search")
def release_search(book_id: int):
    """Look a submitted series up on NU's add-release form for its id."""
    _book_or_404(book_id)
    _act(nu_sync.request_release_search, book_id)
    return _book_view(book_id)


class PostFirst(BaseModel):
    chapter: Optional[int] = None


@router.post("/api/books/{book_id}/nu/first-release/post")
def post_first(book_id: int, req: PostFirst):
    """Post a submitted series' first chapter by its pending id, now."""
    _book_or_404(book_id)
    task_id = _act(nu_sync.post_first_release, book_id, req.chapter)
    return {"task_id": task_id, **_book_view(book_id)}


class FirstRelease(BaseModel):
    chapter: int
    label: Optional[str] = None


@router.post("/api/books/{book_id}/nu/first-release")
def first_release(book_id: int, req: FirstRelease):
    _book_or_404(book_id)
    _act(nu_sync.record_first_release, book_id, req.chapter, req.label)
    return _book_view(book_id)


class Resolve(BaseModel):
    action: str


@router.post("/api/books/{book_id}/nu/releases/{chapter}/resolve")
def resolve(book_id: int, chapter: int, req: Resolve):
    _book_or_404(book_id)
    _act(nu_sync.resolve_release, book_id, chapter, req.action)
    return _book_view(book_id)


@router.post("/api/books/{book_id}/nu/releases/{chapter}/dry-run")
def release_dry_run(book_id: int, chapter: int):
    _book_or_404(book_id)
    task_id = _act(nu_sync.queue_release_dry_run, book_id, chapter)
    return {"task_id": task_id, **_book_view(book_id)}
