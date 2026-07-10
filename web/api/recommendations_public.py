"""
Public endpoint for submitting novel translation recommendations.

Protected by Cloudflare Turnstile and rate limiting.
"""
import os
import re
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional

from web.services import public_guard, turnstile, recommendation_emails
from web.services.ip import client_ip

router = APIRouter(prefix="/api/public")

_db = None


def init(db_manager):
    global _db
    _db = db_manager


# Rate limiting — stricter than the general public API: 5 submissions/hour/IP.
_limiter = public_guard.SlidingWindowLimiter(
    3600, 5, "Too many submissions. Please try again later.")


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

class RecommendationRequest(BaseModel):
    novel_title: str = Field(max_length=300)
    author: Optional[str] = Field(default=None, max_length=200)
    source_url: str = Field(max_length=1000)
    source_language: Optional[str] = Field(default="zh", max_length=16)
    description: Optional[str] = Field(default=None, max_length=4000)
    requester_name: str = Field(max_length=100)
    requester_email: str = Field(max_length=254)
    notes: Optional[str] = Field(default=None, max_length=4000)
    turnstile_token: str = Field(max_length=4096)


# Deliberately loose — just enough to reject garbage and header-unsafe
# values; deliverability is the mail system's problem.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@router.post("/recommendations")
async def submit_recommendation(req: RecommendationRequest, request: Request,
                                background_tasks: BackgroundTasks):
    ip = client_ip(request)
    _limiter.check(ip)

    # Verify Turnstile (shared helper: 5s timeout, fails closed on network error)
    valid, _err = await turnstile.verify(req.turnstile_token, ip)
    if not valid:
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed. Please try again.")

    # Basic validation
    if not req.novel_title.strip():
        raise HTTPException(status_code=400, detail="Novel title is required.")
    if not re.match(r"^https?://", req.source_url.strip(), re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Source URL must start with http:// or https://.")
    if not req.requester_name.strip():
        raise HTTPException(status_code=400, detail="Your name is required.")
    if not _EMAIL_RE.match(req.requester_email.strip()):
        raise HTTPException(status_code=400, detail="A valid email is required.")

    # The handler must stay async for turnstile.verify; keep the sync DB write
    # off the event loop.
    import asyncio
    rec_id = await asyncio.to_thread(_db.create_recommendation, {
        "novel_title": req.novel_title.strip(),
        "author": (req.author or "").strip() or None,
        "source_url": req.source_url.strip(),
        "source_language": req.source_language or "zh",
        "description": (req.description or "").strip() or None,
        "requester_name": req.requester_name.strip(),
        "requester_email": req.requester_email.strip(),
        "notes": (req.notes or "").strip() or None,
    })

    # Fire-and-forget confirmation email (honours suppression; never blocks submit).
    background_tasks.add_task(
        recommendation_emails.send_submit_confirmation, _db, rec_id)

    return {"status": "ok", "id": rec_id}


@router.get("/turnstile-site-key")
def get_turnstile_site_key():
    """Return the Cloudflare Turnstile site key for the frontend widget."""
    key = os.getenv("CF_TURNSTILE_SITE_KEY", "")
    return {"site_key": key}
