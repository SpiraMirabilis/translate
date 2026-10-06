"""
Public endpoint for reader-submitted translation error reports.

A report is anchored to a book and, optionally, to one chapter — chapter_number
NULL means "book-wide issue". The optional `quote` is the passage the reader
highlighted in the reader; the admin queue turns it into a Chapter Editor deep
link, which is what makes a report actionable without re-reading the chapter.

Protections on POST (the comments set, not the recommendations one):
  - Origin/Referer guard — this form is only ever submitted from a page we serve
  - Per-IP sliding-window rate limits (3/10min, 10/hour)
  - Cloudflare Turnstile token verification
  - Global error_reports_enabled kill switch (settings.json, read live)
  - Private books and unpublished chapters 404 exactly as they do elsewhere on
    the public API, so a report cannot be used to probe for drafts
"""
import asyncio
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from web.services import public_guard, turnstile
from web.services.ip import client_ip

router = APIRouter(prefix="/api/public")

_db = None


def init(db_manager):
    global _db
    _db = db_manager


# Rate limiting — a burst allowance for someone working through a chapter they
# found several problems in, with an hourly ceiling behind it.
_RATE_DETAIL = "Too many reports. Please try again later."
_limiter_short = public_guard.SlidingWindowLimiter(600, 3, _RATE_DETAIL)
_limiter_long = public_guard.SlidingWindowLimiter(3600, 10, _RATE_DETAIL)


# The report_type enum. Mirrored in the frontend modal; anything else is
# rejected rather than coerced, so the admin queue's filters stay meaningful.
REPORT_TYPES = {
    "wrong_term",
    "mistranslation",
    "typo",
    "formatting",
    "missing_text",
    "other",
}

QUOTE_MAX = 500
TEXT_MAX = 2000

# Deliberately loose — just enough to reject garbage and header-unsafe values.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def reports_enabled() -> bool:
    """Global kill switch. Read per request: settings_store reloads settings.json
    on mtime change, so flipping this in the admin Settings UI reaches the public
    process without a restart."""
    import settings_store
    return bool(settings_store.get("error_reports_enabled", True))


class ErrorReportRequest(BaseModel):
    book_id: int
    chapter_number: Optional[int] = None
    report_type: str = Field(max_length=32)
    quote: Optional[str] = Field(default=None, max_length=QUOTE_MAX)
    problem: str = Field(max_length=TEXT_MAX)
    suggested_fix: Optional[str] = Field(default=None, max_length=TEXT_MAX)
    reporter_email: Optional[str] = Field(default=None, max_length=254)
    turnstile_token: str = Field(default="", max_length=4096)


@router.post("/error-reports")
async def submit_error_report(req: ErrorReportRequest, request: Request):
    if not reports_enabled():
        raise HTTPException(status_code=403, detail="Error reporting is currently disabled.")

    ip = client_ip(request) or "unknown"
    _limiter_short.check(ip)
    _limiter_long.check(ip)
    public_guard.origin_check(request)

    # Shape checks before the captcha round-trip
    if req.report_type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail="Unknown report type.")
    problem = (req.problem or "").strip()
    if not problem:
        raise HTTPException(status_code=400, detail="Please describe the problem.")
    email = (req.reporter_email or "").strip()
    if email and not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="That email address doesn't look valid.")

    # Private books: same 404 as a missing book (no existence leak).
    book = _db.get_book(book_id=req.book_id)
    if not book or not book.get("is_public", True):
        raise HTTPException(status_code=404, detail="Book not found.")

    # Drafts and not-yet-due scheduled chapters aren't publicly readable, so
    # they can't be reported on either.
    chapter_number = req.chapter_number
    if chapter_number is not None:
        if chapter_number <= 0:
            chapter_number = None  # treat a sentinel/0 as book-wide
        elif not _db.get_chapter(book_id=req.book_id, chapter_number=chapter_number,
                                 published_only=True):
            raise HTTPException(status_code=404, detail="Chapter not found.")

    valid, err = await turnstile.verify(req.turnstile_token, ip)
    if not valid:
        raise HTTPException(status_code=400,
                            detail=f"CAPTCHA verification failed ({err}).")

    user_agent = request.headers.get("user-agent", "")[:256]

    # The handler must stay async for turnstile.verify; keep the sync DB write
    # off the event loop.
    report_id = await asyncio.to_thread(_db.create_error_report, {
        "book_id": req.book_id,
        "chapter_number": chapter_number,
        "report_type": req.report_type,
        "quote": (req.quote or "").strip()[:QUOTE_MAX] or None,
        "problem": problem,
        "suggested_fix": (req.suggested_fix or "").strip() or None,
        "reporter_email": email or None,
        "ip": ip,
        "user_agent": user_agent,
    })

    return {"status": "ok", "id": report_id}


@router.get("/error-reports/enabled")
def error_reports_enabled():
    """Whether the reader should offer the report entry points at all."""
    return {"enabled": reports_enabled()}
