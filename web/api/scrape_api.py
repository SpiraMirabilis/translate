"""
Admin endpoints for automated source scraping.

The work itself happens in scrape_worker.py (t9-scraper.service), a separate
process that owns the scraper lane. These endpoints only read state and write
rows for it — book_scrape config, scrape_tasks — which it picks up within
seconds. Admin process only.
"""
import json
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import scrape_sync

router = APIRouter()

_db = None
_hub = None

HEARTBEAT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "logs", "scrape_worker.json")
# The worker writes a heartbeat every tick (~10 s); older than this = down.
HEARTBEAT_STALE_SECONDS = 120


def init(db_manager, hub=None):
    global _db, _hub
    _db = db_manager
    _hub = hub


def _worker_status():
    try:
        with open(HEARTBEAT) as f:
            hb = json.load(f)
    except (OSError, ValueError):
        return {"running": False, "age_seconds": None}
    age = time.time() - float(hb.get("at") or 0)
    return {"running": age < HEARTBEAT_STALE_SECONDS, "age_seconds": int(age),
            "state": hb.get("state"), "lane_task_id": hb.get("lane_task_id")}


def _book_or_404(book_id):
    book = _db.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@router.get("/api/scrape/overview")
def overview():
    known = scrape_sync.load_supported_sites()
    sites = known["sites"] if known else None
    books = _db.list_book_scrapes()
    for b in books:
        b["site_supported"] = scrape_sync.site_supported(b.get("source_url") or "", sites)
    return {
        "settings": {
            "enabled": bool(scrape_sync.setting("scrape_enabled")),
            "daily_hour": scrape_sync.setting("scrape_daily_hour"),
            "chunk_size": scrape_sync.setting("scrape_chunk_size"),
            "default_gap": scrape_sync.setting("scrape_default_gap"),
            "scraper_url": scrape_sync.setting("scraper_url"),
            "has_token": bool(scrape_sync.scraper_token()),
            "failure_limit": scrape_sync.FAILURE_LIMIT,
            "reserved_slots": scrape_sync.setting("scrape_reserved_slots"),
        },
        "worker": _worker_status(),
        "paused": scrape_sync.load_pause(),
        "supported_sites": known,
        "lane": _db.lane_task(),
        "queued": _db.list_scrape_tasks(statuses=("queued",), order="t.priority, t.id", limit=200),
        "recent": _db.list_scrape_tasks(
            statuses=("done", "failed", "cancelled", "skipped"), limit=40,
            order="t.finished_at DESC, t.id DESC"),
        "books": books,
    }


@router.post("/api/scrape/pause")
def pause():
    """Hold the whole lane, e.g. before restarting the worker or the scraper.
    The worker suspends the job on the scraper (it resumes from its last page
    on Resume), submits nothing new and starts no translations. Persisted, so
    a paused worker comes back up paused."""
    already = scrape_sync.load_pause()
    state = scrape_sync.set_paused(True)
    # The worker does this too on its next tick; doing it here makes the
    # page show "suspending" at once.
    lane = _db.lane_task()
    if lane and lane["status"] in ("running", "blocked"):
        _db.update_scrape_task(lane["id"], status="suspending")
    if not already:
        _db.add_activity_log("scrape", "Scrape: paused"
                             + (f" — suspending {lane.get('book_title') or 'book ' + str(lane['book_id'])}'s job"
                                if lane else ""))
    return {"paused": state, "lane": _db.lane_task()}


@router.post("/api/scrape/resume")
def resume():
    was = scrape_sync.load_pause()
    scrape_sync.set_paused(False)
    if was:
        _db.add_activity_log("scrape", "Scrape: resumed")
    return {"paused": None, "lane": _db.lane_task()}


@router.post("/api/scrape/sites/refresh")
def refresh_sites():
    """Re-read the scraper's /supported_sites now (the worker does it daily).
    A newly listed site onboards its books exactly as the worker's read would."""
    sites = scrape_sync.ScrapeEngine(_db, admin=object()).refresh_sites(force=True)
    known = scrape_sync.load_supported_sites()
    if sites is None or not known:
        raise HTTPException(status_code=502, detail="Could not read the scraper's supported sites.")
    return known


@router.post("/api/scraper-notify")
async def scraper_notify(request: Request):
    """The scraper's "a human is needed" callback: a job stuck on a Cloudflare
    challenge it couldn't clear by itself. Outside session auth (the scraper
    has no cookie); instead every POST must carry X-Scraper-Signature, an
    HMAC-SHA256 of the raw body under SCRAPER_NOTIFY_SECRET, with a timestamp
    no older than five minutes. Writes a red activity-log line naming the book,
    with a noVNC link when scraper_vnc_url is set."""
    from scraper_client import verify_signature
    secret = scrape_sync.notify_secret()
    body = await request.body()
    if not secret or not verify_signature(secret, body, request.headers.get("X-Scraper-Signature", "")):
        raise HTTPException(status_code=401, detail="Bad or missing signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Body is not JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    book_id, title, line, url = await run_in_threadpool(scrape_sync.blocked_notice, _db, payload)
    entities = [{"label": "Solve it in noVNC", "href": url}] if url else None
    if _hub is not None:
        await _hub.log_activity_async("scrape_blocked", line, book_id=book_id, book_name=title,
                                      entities=entities)
    else:
        await run_in_threadpool(_db.add_activity_log, "scrape_blocked", line, book_id=book_id,
                                book_name=title, entities=entities)
    return {"status": "ok", "book_id": book_id}


@router.get("/api/scrape/count")
def attention_count():
    # A forgotten pause stops everything, so it counts too.
    return {"count": _db.count_scrape_attention(scrape_sync.FAILURE_LIMIT)
            + (1 if scrape_sync.load_pause() else 0)}


@router.get("/api/books/{book_id}/scrape")
def get_book_scrape(book_id: int):
    book = _book_or_404(book_id)
    state = _db.get_book_scrape(book_id, include_toc=True)
    rows = _db.list_book_scrapes(book_id=book_id)
    heads = rows[0] if rows else {}
    toc = (state.pop("last_toc", None) or {}).get("chapters") or []
    unplaced = state.pop("unplaced", None) or []
    unplaced_pending = [i for i in unplaced if i.get("status") == "pending"]
    unplaced_recent = [{k: v for k, v in i.items() if k != "text"}
                       for i in unplaced if i.get("status") != "pending"][-10:][::-1]
    proposal = scrape_sync.propose_link(
        toc, heads.get("have_max"), state.get("number_offset") or 0,
        our_titles=_db.recent_source_titles(book_id)) if toc else None
    return {
        "book_id": book_id,
        "source_url": book.get("source_url"),
        "site": scrape_sync.site_of(book.get("source_url") or ""),
        "site_supported": scrape_sync.site_supported(
            book.get("source_url") or "", (scrape_sync.load_supported_sites() or {}).get("sites")),
        "state": state,
        "translated_max": heads.get("translated_max"),
        "queued_max": heads.get("queued_max"),
        "have_max": heads.get("have_max"),
        "queued_count": heads.get("queued_count") or 0,
        "toc": [{"index": i, "title": c.get("title"), "url": c.get("url"),
                 "number": scrape_sync.parse_title_number(c.get("title"))}
                for i, c in enumerate(toc)],
        "cursor_index": scrape_sync.cursor_index(state, toc) if toc else None,
        "proposal": proposal,
        # What an auto-start would use with no per-book override (the
        # override itself is state.run_options.translation_model).
        "default_model": scrape_sync.auto_translate_model(),
        "latest_model": _db.latest_translation_model(book_id),
        "default_gap": scrape_sync.setting("scrape_default_gap"),
        "tasks": _db.list_scrape_tasks(book_id=book_id, limit=15),
        "unplaced": unplaced_pending,
        "unplaced_recent": unplaced_recent,
    }


class RunOptions(BaseModel):
    # Blank/None = follow the global scrape_translation_model.
    translation_model: Optional[str] = None
    no_review: bool = True
    save_as_draft: bool = False


class BookScrapeUpdate(BaseModel):
    mode: Optional[str] = None
    auto_gap: Optional[int] = None
    clear_auto_gap: bool = False
    number_offset: Optional[int] = None
    run_options: Optional[RunOptions] = None
    clear_hold: bool = False
    clear_warnings: bool = False


@router.put("/api/books/{book_id}/scrape")
def update_book_scrape(book_id: int, req: BookScrapeUpdate):
    book = _book_or_404(book_id)
    fields = {}
    if req.mode is not None:
        if req.mode not in scrape_sync.MODES:
            raise HTTPException(status_code=400, detail="mode must be off, queue or auto")
        if req.mode != "off" and not (book.get("source_url") or "").strip():
            raise HTTPException(status_code=400, detail="Set the book's source URL first.")
        fields["mode"] = req.mode
        if req.mode != "off":
            fields["consecutive_failures"] = 0
    if req.clear_auto_gap:
        fields["auto_gap"] = None
    elif req.auto_gap is not None:
        if req.auto_gap < 0:
            raise HTTPException(status_code=400, detail="auto_gap must be >= 0")
        fields["auto_gap"] = req.auto_gap
    if req.number_offset is not None:
        fields["number_offset"] = req.number_offset
    if req.run_options is not None:
        opts = req.run_options.model_dump()
        model = (opts.get("translation_model") or "").strip()
        if model and not scrape_sync.valid_model_spec(model):
            raise HTTPException(status_code=400, detail="Model must be provider:model")
        opts["translation_model"] = model or None
        fields["run_options"] = opts
    if req.clear_hold:
        fields["hold_reason"] = None
        fields["last_error"] = None
    if req.clear_warnings:
        fields["warnings"] = None
    if fields:
        _db.update_book_scrape(book_id, **fields)
    return get_book_scrape(book_id)


class CheckRequest(BaseModel):
    catchup: bool = False
    # Run it exactly as the daily check would (scrape new chapters and maybe
    # auto-translate when the book's mode says so), just now.
    like_daily: bool = False


@router.post("/api/books/{book_id}/scrape/check")
def check_now(book_id: int, req: CheckRequest = CheckRequest()):
    book = _book_or_404(book_id)
    if not (book.get("source_url") or "").strip():
        raise HTTPException(status_code=400, detail="This book has no source URL.")
    if _db.has_open_scrape_task(book_id, kinds=("toc",)):
        raise HTTPException(status_code=409, detail="A TOC check for this book is already queued.")
    task_id = scrape_sync.enqueue_check(_db, book_id, catchup=req.catchup,
                                        reason="forced" if req.like_daily else "manual")
    return {"status": "queued", "task_id": task_id}


class LinkRequest(BaseModel):
    index: int
    chapter_number: Optional[int] = None


@router.post("/api/books/{book_id}/scrape/link")
def link(book_id: int, req: LinkRequest):
    _book_or_404(book_id)
    try:
        result = scrape_sync.link_book(_db, book_id, req.index, req.chapter_number)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", **result}


class BackfillRequest(BaseModel):
    from_index: int
    to_index: Optional[int] = None


@router.post("/api/books/{book_id}/scrape/backfill")
def backfill(book_id: int, req: BackfillRequest):
    _book_or_404(book_id)
    state = _db.get_book_scrape(book_id)
    if _db.has_open_scrape_task(book_id, kinds=("chapters",)):
        raise HTTPException(status_code=409, detail="A chapter scrape for this book is already queued or running.")
    # A book with nothing in it yet: link before the first entry, so the
    # backfill moves the cursor and the daily check carries on from its end.
    if not state.get("linked") and not _db.existing_chapter_numbers(book_id):
        scrape_sync.link_book(_db, book_id, -1)
    try:
        task_id = scrape_sync.enqueue_backfill(_db, book_id, req.from_index, req.to_index)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "queued", "task_id": task_id}


class UnplacedDecision(BaseModel):
    action: str                         # discard | append | queue
    chapter_number: Optional[int] = None


@router.post("/api/books/{book_id}/scrape/unplaced/{item_id}")
def resolve_unplaced(book_id: int, item_id: str, req: UnplacedDecision):
    """Decide a parked unnumbered entry: discard it, append it to the end of a
    chapter (default: the one it followed), or queue it as its own chapter."""
    _book_or_404(book_id)
    try:
        done = scrape_sync.resolve_unplaced(_db, book_id, item_id, req.action, req.chapter_number)
    except scrape_sync.UnplacedError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"status": "ok", "done": done}


@router.post("/api/scrape/tasks/{task_id}/cancel")
def cancel_task(task_id: int):
    task = _db.get_scrape_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] == "queued":
        _db.update_scrape_task(task_id, status="cancelled", finished_at=scrape_sync.now_iso())
    elif task["status"] in ("running", "blocked", "suspending"):
        # The worker cancels the scraper job on its next poll.
        _db.update_scrape_task(task_id, status="cancelling")
    else:
        raise HTTPException(status_code=409, detail=f"Task is already {task['status']}.")
    return _db.get_scrape_task(task_id)


@router.post("/api/scrape/tasks/{task_id}/retry")
def retry_task(task_id: int):
    """Run a failed/cancelled task again, or a queued one now (skips its backoff)."""
    task = _db.get_scrape_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] in ("running", "blocked", "cancelling", "suspending"):
        raise HTTPException(status_code=409, detail="Task is on the scraper right now.")
    if task["status"] == "done":
        raise HTTPException(status_code=409, detail="Task already finished.")
    _db.update_scrape_task(task_id, status="queued", not_before=None, attempts=0,
                           finished_at=None, last_error=None)
    return _db.get_scrape_task(task_id)
