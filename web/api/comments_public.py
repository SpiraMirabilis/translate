"""
Public endpoints for chapter comments.

Visibility model (shadowban):
  - status='approved': visible to everyone
  - status='deleted':  visible to everyone, body replaced with '[removed]'
                       so thread structure survives moderation
  - status='pending' / 'blocked': visible only to the original commenter
                       (matched via X-Commenter-UUID header). Spammers
                       don't get retry signal; legitimate authors don't
                       see "pending review" pills on their own posts.

Protections on POST:
  - Origin/Referer guard (transparent to browsers, blocks casual scripting)
  - Per-IP and per-UUID sliding-window rate limits (5/min, 30/hour)
  - Cloudflare Turnstile token verification
  - Per-book comments_enabled toggle
  - Real client IP via CF-Connecting-IP (web/services/ip.py)
  - Optional async AI auto-moderation via BackgroundTasks

The `Vary: X-Commenter-UUID` header on the list response is mandatory —
without it, intermediate caches will leak one user's pending list to
another.
"""

import os
import re
import hmac
import hashlib
import time
import threading
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from web.services import automod, turnstile
from web.services.email_tokens import verify_unsubscribe_token
from web.services.ip import client_ip
from web.services.notifications import notify_reply


router = APIRouter(prefix="/api/public/comments")

_db = None
_config = None


def init(db_manager, config=None):
    global _db, _config
    _db = db_manager
    _config = config


# ------------------------------------------------------------------
# Origin / Referer guard (mirrors web/api/public.py)
# ------------------------------------------------------------------

def _origin_check(request: Request):
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    host = request.headers.get("host", "")
    allowed = set()
    if host:
        allowed.add(f"http://{host}")
        allowed.add(f"https://{host}")
    allowed.add("http://localhost:5173")
    allowed.add("http://127.0.0.1:5173")
    allowed.add("http://localhost:8000")
    allowed.add("http://127.0.0.1:8000")
    if origin and any(origin.startswith(a) for a in allowed):
        return
    if referer and any(referer.startswith(a) for a in allowed):
        return
    if not origin and not referer:
        return
    raise HTTPException(status_code=403, detail="Forbidden")


# ------------------------------------------------------------------
# Rate limiting — per-IP and per-UUID, sliding windows
# ------------------------------------------------------------------

_RATE_SHORT_WINDOW = 60
_RATE_SHORT_LIMIT = 5
_RATE_LONG_WINDOW = 3600
_RATE_LONG_LIMIT = 30

_hits_ip_short: dict[str, list[float]] = defaultdict(list)
_hits_ip_long:  dict[str, list[float]] = defaultdict(list)
_hits_uuid_short: dict[str, list[float]] = defaultdict(list)
_hits_uuid_long:  dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()


# ------------------------------------------------------------------
# Captcha-skip cache: once a UUID has solved Turnstile successfully
# we don't re-prompt for an hour. Trusted UUIDs (those with at least
# one approved comment) skip Turnstile entirely.
# ------------------------------------------------------------------

_HUMAN_VERIFY_TTL = 3600  # seconds
_human_verified: dict[str, float] = {}


def _is_human_verified(uuid: Optional[str]) -> bool:
    if not uuid:
        return False
    with _lock:
        exp = _human_verified.get(uuid, 0)
        if exp > time.time():
            return True
        _human_verified.pop(uuid, None)
        return False


def _mark_human_verified(uuid: Optional[str]):
    if not uuid:
        return
    with _lock:
        _human_verified[uuid] = time.time() + _HUMAN_VERIFY_TTL


def _captcha_required_for(uuid: Optional[str]) -> bool:
    """True iff this UUID needs to solve Turnstile to POST."""
    if not uuid:
        return True
    if _db.is_commenter_trusted(uuid):
        return False
    return not _is_human_verified(uuid)


def _viewer_meta(uuid: Optional[str]) -> dict:
    if not uuid:
        return {"is_trusted": False, "captcha_required": True}
    return {
        "is_trusted": bool(_db.is_commenter_trusted(uuid)),
        "captcha_required": _captcha_required_for(uuid),
    }


def _check_rate(key: str, hits: dict, window: int, limit: int):
    now = time.time()
    cutoff = now - window
    bucket = [t for t in hits[key] if t > cutoff]
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Too many comments. Slow down.")
    bucket.append(now)
    hits[key] = bucket


def _rate_check(ip: str, uuid: str):
    with _lock:
        _check_rate(ip,   _hits_ip_short,   _RATE_SHORT_WINDOW, _RATE_SHORT_LIMIT)
        _check_rate(ip,   _hits_ip_long,    _RATE_LONG_WINDOW,  _RATE_LONG_LIMIT)
        _check_rate(uuid, _hits_uuid_short, _RATE_SHORT_WINDOW, _RATE_SHORT_LIMIT)
        _check_rate(uuid, _hits_uuid_long,  _RATE_LONG_WINDOW,  _RATE_LONG_LIMIT)


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_uuid(uuid: str) -> str:
    if not uuid or not UUID_RE.match(uuid):
        raise HTTPException(status_code=400, detail="Invalid commenter UUID")
    return uuid.lower()


def _viewer_uuid(header_value: Optional[str]) -> Optional[str]:
    """Permissive: malformed header → None (read-only context)."""
    if not header_value:
        return None
    s = header_value.strip()
    if UUID_RE.match(s):
        return s.lower()
    return None


# ------------------------------------------------------------------
# Public response shaping
# ------------------------------------------------------------------

def _fingerprint(display_name: Optional[str], email: Optional[str]) -> str:
    """
    4-hex-char public tag derived from (display_name, email).

    Lets readers disambiguate impersonation: an impostor reusing
    someone else's display name will not know their email, so the
    tag will differ. Email is treated as a shared secret since it
    is never exposed in public responses. Tying the tag to the
    display name (instead of UUID) means it follows the user across
    devices, where the UUID would otherwise change.
    """
    name = (display_name or "").strip().casefold()
    mail = (email or "").strip().lower()
    if not name and not mail:
        return ""
    payload = f"{name}\x1f{mail}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:4]


def _shape_public(row: dict, viewer_uuid: Optional[str]) -> dict:
    """Strip admin-only fields; add is_own + fingerprint derived flags."""
    uuid = row.get("commenter_uuid")
    is_own = bool(viewer_uuid and uuid == viewer_uuid)
    return {
        "id": row["id"],
        "parent_id": row.get("parent_id"),
        "depth": row.get("depth", 0),
        "root_id": row.get("root_id"),
        "display_name": row.get("display_name"),
        "fingerprint": _fingerprint(row.get("display_name"), row.get("email")),
        "body": row.get("body"),
        "status": row.get("status"),
        "edited_at": row.get("edited_at"),
        "deleted_at": row.get("deleted_at"),
        "created_at": row.get("created_at"),
        "is_own": is_own,
    }


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

class CreateCommentReq(BaseModel):
    book_id: int
    chapter_number: int
    parent_id: Optional[int] = None
    commenter_uuid: str
    display_name: str = Field(min_length=1, max_length=40)
    email: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=1, max_length=4000)
    turnstile_token: str
    notify_replies: bool = False


class EditCommentReq(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    turnstile_token: Optional[str] = None


@router.get("/chapter/{book_id}/{chapter_number}")
async def list_chapter_comments(
    book_id: int,
    chapter_number: int,
    request: Request,
    response: Response,
    x_commenter_uuid: Optional[str] = Header(default=None, alias="X-Commenter-UUID"),
):
    _origin_check(request)
    viewer = _viewer_uuid(x_commenter_uuid)
    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["Vary"] = "X-Commenter-UUID"

    if not _db.get_book_comments_enabled(book_id):
        # Comments disabled for this book — return empty rather than 403 so
        # the badge silently shows 0 and the icon can be hidden by the UI.
        return {"comments": [], "count": 0, "enabled": False, "viewer": _viewer_meta(viewer)}

    rows = _db.list_comments_for_chapter(book_id, chapter_number, viewer)
    items = [_shape_public(r, viewer) for r in rows]
    return {
        "comments": items,
        "count": len([i for i in items if i["status"] != "deleted"]),
        "enabled": True,
        "viewer": _viewer_meta(viewer),
    }


@router.get("/chapter/{book_id}/{chapter_number}/count")
async def count_chapter_comments(
    book_id: int,
    chapter_number: int,
    request: Request,
    response: Response,
    x_commenter_uuid: Optional[str] = Header(default=None, alias="X-Commenter-UUID"),
):
    _origin_check(request)
    viewer = _viewer_uuid(x_commenter_uuid)
    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["Vary"] = "X-Commenter-UUID"
    if not _db.get_book_comments_enabled(book_id):
        return {"count": 0, "enabled": False, "viewer": _viewer_meta(viewer)}
    n = _db.count_comments_visible(book_id, chapter_number, viewer)
    return {"count": n, "enabled": True, "viewer": _viewer_meta(viewer)}


@router.post("")
async def create_comment(
    req: CreateCommentReq,
    request: Request,
    background: BackgroundTasks,
):
    _origin_check(request)

    # Validate inputs
    uuid = _validate_uuid(req.commenter_uuid)
    if not EMAIL_RE.match(req.email.strip()):
        raise HTTPException(status_code=400, detail="A valid email is required.")
    display_name = req.display_name.strip()
    body = req.body.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="Display name is required.")
    if not body:
        raise HTTPException(status_code=400, detail="Comment body is required.")

    # Per-book toggle
    if not _db.get_book_comments_enabled(req.book_id):
        raise HTTPException(status_code=403, detail="Comments are disabled for this book.")

    # Real client IP + rate limits
    ip = client_ip(request)
    if not ip:
        ip = "unknown"
    _rate_check(ip, uuid)

    # Turnstile — skipped for trusted UUIDs and for UUIDs that solved a
    # challenge in the last hour. Fail-closed for everyone else.
    if _captcha_required_for(uuid):
        ok, err = await turnstile.verify(req.turnstile_token, ip)
        if not ok:
            raise HTTPException(status_code=400, detail=f"CAPTCHA verification failed ({err}).")
        _mark_human_verified(uuid)

    user_agent = request.headers.get("user-agent", "")[:256]

    try:
        new_id = _db.create_comment({
            "book_id": req.book_id,
            "chapter_number": req.chapter_number,
            "parent_id": req.parent_id,
            "commenter_uuid": uuid,
            "display_name": display_name,
            "email": req.email.strip(),
            "body": body,
            "ip": ip,
            "user_agent": user_agent,
            "notify_replies": req.notify_replies,
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    inserted = _db.get_comment(new_id)

    if inserted and inserted["status"] == "pending" and automod.is_enabled():
        # Automod will handle notification dispatch internally if it approves.
        background.add_task(automod.run_automod_for_comment, _db, new_id)
    elif inserted and inserted["status"] == "approved" and inserted.get("parent_id"):
        # Trusted UUID: skipped automod, went straight to approved. Notify
        # the parent commenter (if they opted in).
        background.add_task(notify_reply, _db, new_id)

    return {"comment": _shape_public(inserted, uuid), "status": "ok"}


@router.put("/{comment_id}")
async def edit_comment(
    comment_id: int,
    req: EditCommentReq,
    request: Request,
    background: BackgroundTasks,
    x_commenter_uuid: Optional[str] = Header(default=None, alias="X-Commenter-UUID"),
):
    _origin_check(request)
    viewer = _viewer_uuid(x_commenter_uuid)
    if not viewer:
        raise HTTPException(status_code=400, detail="Missing or invalid X-Commenter-UUID header.")

    existing = _db.get_comment(comment_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Comment not found.")
    if existing["status"] == "deleted":
        raise HTTPException(status_code=410, detail="Comment has been removed.")
    # Constant-time UUID compare
    if not hmac.compare_digest(viewer, str(existing["commenter_uuid"]).lower()):
        raise HTTPException(status_code=403, detail="Not authorized to edit this comment.")

    body = req.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment body is required.")

    _db.update_comment(comment_id, body=body)

    # Re-queue automod after edit (body changed → status demoted to pending)
    refreshed = _db.get_comment(comment_id)
    if automod.is_enabled() and refreshed and refreshed["status"] == "pending":
        background.add_task(automod.run_automod_for_comment, _db, comment_id)

    return {"comment": _shape_public(refreshed, viewer), "status": "ok"}


@router.delete("/{comment_id}")
async def delete_comment(
    comment_id: int,
    request: Request,
    x_commenter_uuid: Optional[str] = Header(default=None, alias="X-Commenter-UUID"),
):
    _origin_check(request)
    viewer = _viewer_uuid(x_commenter_uuid)
    if not viewer:
        raise HTTPException(status_code=400, detail="Missing or invalid X-Commenter-UUID header.")

    existing = _db.get_comment(comment_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Comment not found.")
    if existing["status"] == "deleted":
        return {"status": "ok"}  # idempotent
    if not hmac.compare_digest(viewer, str(existing["commenter_uuid"]).lower()):
        raise HTTPException(status_code=403, detail="Not authorized to delete this comment.")

    _db.update_comment(comment_id, soft_delete=True)
    return {"status": "ok"}


@router.get("/turnstile-site-key")
async def get_turnstile_site_key():
    """Sibling endpoint to /api/public/turnstile-site-key for symmetry."""
    return {"site_key": os.getenv("CF_TURNSTILE_SITE_KEY", "")}


@router.get("/unsubscribe", include_in_schema=False)
async def unsubscribe(token: str = ""):
    """One-click unsubscribe from reply notifications.

    Idempotent: clicking the link multiple times has no extra effect.
    Anyone holding the signed token (intended recipient) can unsubscribe;
    no auth needed because the token IS the auth.
    """
    email = verify_unsubscribe_token(token)
    if not email:
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8>"
            "<title>Invalid link</title>"
            "<div style='font-family:system-ui,sans-serif;max-width:420px;margin:80px auto;text-align:center'>"
            "<h1 style='color:#d4183d'>Invalid or expired link</h1>"
            "<p>This unsubscribe link could not be verified. Please use the link from a recent notification email.</p>"
            "</div>",
            status_code=400,
        )
    _db.add_email_suppression(email, reason="unsubscribe")
    site_name = os.getenv("PUBLIC_SITE_NAME") or os.getenv("SITE_NAME") or "Reader"
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        f"<title>Unsubscribed — {site_name}</title>"
        "<div style='font-family:system-ui,sans-serif;max-width:420px;margin:80px auto;text-align:center'>"
        "<h1 style='color:#16a34a'>You're unsubscribed</h1>"
        "<p>You won't receive any more reply-notification emails from this site.</p>"
        "<p style='color:#666;font-size:14px'>You can still post comments — just no email notifications.</p>"
        "</div>"
    )
