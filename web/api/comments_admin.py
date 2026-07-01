"""
Admin endpoints for moderating chapter comments and managing bans.

Requires authentication (handled globally by AuthMiddleware).

Bans:
  - kind='uuid'  → block specific localStorage identity
  - kind='email' → block any commenter using that email
  - kind='ip'    → block IP; ALSO pushes to Cloudflare edge via cf_bans
                   (best-effort; in-DB ban is canonical)

CF push runs in BackgroundTasks so the admin click returns immediately.
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from web.services import automod, cf_bans
from web.services.notifications import notify_reply


router = APIRouter(prefix="/api/comments")

_db = None


def init(db_manager):
    global _db
    _db = db_manager


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------

class CommentUpdate(BaseModel):
    status: Optional[str] = None
    body: Optional[str] = None


class CreateBanReq(BaseModel):
    kind: str = Field(pattern=r"^(uuid|email|ip)$")
    value: str = Field(min_length=1, max_length=255)
    reason: Optional[str] = None


class BookCommentsToggle(BaseModel):
    enabled: bool


# ------------------------------------------------------------------
# Background helpers
# ------------------------------------------------------------------

async def _bg_push_cf(ip: str, ban_id: int, note: str):
    ok, msg = await cf_bans.push_ban(ip, note)
    if ok:
        _db.mark_cf_pushed(ban_id)


async def _bg_remove_cf(ip: str):
    await cf_bans.remove_ban(ip)


# ------------------------------------------------------------------
# Comments
# ------------------------------------------------------------------

@router.get("")
def list_comments(
    status: Optional[str] = Query(default=None),
    book_id: Optional[int] = Query(default=None),
    chapter_number: Optional[int] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    rows = _db.list_comments_admin(
        status=status, book_id=book_id, chapter_number=chapter_number,
        limit=limit, offset=offset,
    )
    return {"items": rows, "count": len(rows)}


@router.get("/count")
def count_comments(
    status: Optional[str] = Query(default="pending"),
    book_id: Optional[int] = Query(default=None),
):
    if status == "pending":
        count = _db.count_pending_comments(book_id=book_id)
    else:
        rows = _db.list_comments_admin(status=status, book_id=book_id, limit=1, offset=0)
        # Cheap path; not used for huge totals
        count = len(rows)
    return {"count": count}


@router.get("/{comment_id}")
def get_comment(comment_id: int):
    row = _db.get_comment(comment_id)
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")
    return row


@router.put("/{comment_id}")
def update_comment(comment_id: int, req: CommentUpdate, background: BackgroundTasks):
    existing = _db.get_comment(comment_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Comment not found")
    if req.status is not None and req.status not in ("pending", "approved", "blocked", "deleted"):
        raise HTTPException(status_code=400, detail="Invalid status")
    _db.update_comment(comment_id, body=req.body, status=req.status)
    refreshed = _db.get_comment(comment_id)

    # If the admin just approved a reply, fire the reply-notification.
    # Idempotent so re-approval after re-pending is safe.
    if refreshed and refreshed.get("status") == "approved" and refreshed.get("parent_id"):
        background.add_task(notify_reply, _db, comment_id)

    return {"status": "ok", "comment": refreshed}


@router.delete("/{comment_id}")
def delete_comment(comment_id: int, soft: bool = Query(default=True)):
    existing = _db.get_comment(comment_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Comment not found")
    if soft:
        _db.update_comment(comment_id, soft_delete=True)
        return {"status": "ok", "mode": "soft"}
    if not _db.hard_delete_comment(comment_id):
        raise HTTPException(
            status_code=409,
            detail="Cannot hard-delete: comment has children. Use soft delete to preserve thread.",
        )
    return {"status": "ok", "mode": "hard"}


@router.post("/{comment_id}/automod-rerun")
def rerun_automod(comment_id: int):
    """Synchronously re-run automod against an existing comment (testing/debug)."""
    existing = _db.get_comment(comment_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Comment not found")
    book = None
    try:
        book = _db.get_book(book_id=existing["book_id"])
    except Exception:
        pass
    context = {
        "chapter_number": existing["chapter_number"],
        "book_title": (book or {}).get("title"),
        "source_language": (book or {}).get("source_language"),
    }
    result = automod.classify(existing["body"], existing["display_name"], context)
    return {"verdict": result["verdict"], "reason": result["reason"]}


# ------------------------------------------------------------------
# Bans
# ------------------------------------------------------------------

@router.get("/bans/list")
def list_bans():
    rows = _db.list_bans()
    return {"items": rows, "count": len(rows)}


@router.post("/bans")
def create_ban(req: CreateBanReq, background: BackgroundTasks):
    try:
        ban_id = _db.add_ban(req.kind, req.value, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if req.kind == "ip":
        note = f"comment-ban:{req.reason or 't9'}"
        background.add_task(_bg_push_cf, req.value, ban_id, note)
    return {"status": "ok", "id": ban_id, "kind": req.kind, "value": req.value}


@router.delete("/bans/{ban_id}")
def delete_ban(ban_id: int, background: BackgroundTasks):
    ban = _db.remove_ban_by_id(ban_id)
    if ban is None:
        raise HTTPException(status_code=404, detail="Ban not found")
    if ban["kind"] == "ip" and ban.get("cf_pushed"):
        background.add_task(_bg_remove_cf, ban["value"])
    return {"status": "ok"}


# ------------------------------------------------------------------
# Per-book toggle
# ------------------------------------------------------------------

@router.put("/book/{book_id}/comments_enabled")
def set_book_comments_enabled(book_id: int, req: BookCommentsToggle):
    book = _db.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    _db.set_book_comments_enabled(book_id, req.enabled)
    return {"status": "ok", "book_id": book_id, "comments_enabled": req.enabled}
