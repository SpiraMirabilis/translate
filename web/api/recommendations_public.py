"""
Public endpoint for submitting novel translation recommendations.

Protected by Cloudflare Turnstile and rate limiting.
"""
import os
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from web.services import public_guard, turnstile
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
    novel_title: str
    author: Optional[str] = None
    source_url: str
    source_language: Optional[str] = "zh"
    description: Optional[str] = None
    requester_name: str
    requester_email: str
    notes: Optional[str] = None
    turnstile_token: str


@router.post("/recommendations")
async def submit_recommendation(req: RecommendationRequest, request: Request):
    ip = client_ip(request)
    _limiter.check(ip)

    # Verify Turnstile (shared helper: 5s timeout, fails closed on network error)
    valid, _err = await turnstile.verify(req.turnstile_token, ip)
    if not valid:
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed. Please try again.")

    # Basic validation
    if not req.novel_title.strip():
        raise HTTPException(status_code=400, detail="Novel title is required.")
    if not req.source_url.strip():
        raise HTTPException(status_code=400, detail="Source URL is required.")
    if not req.requester_name.strip():
        raise HTTPException(status_code=400, detail="Your name is required.")
    if not req.requester_email.strip() or "@" not in req.requester_email:
        raise HTTPException(status_code=400, detail="A valid email is required.")

    rec_id = _db.create_recommendation({
        "novel_title": req.novel_title.strip(),
        "author": (req.author or "").strip() or None,
        "source_url": req.source_url.strip(),
        "source_language": req.source_language or "zh",
        "description": (req.description or "").strip() or None,
        "requester_name": req.requester_name.strip(),
        "requester_email": req.requester_email.strip(),
        "notes": (req.notes or "").strip() or None,
    })

    return {"status": "ok", "id": rec_id}


@router.get("/turnstile-site-key")
def get_turnstile_site_key():
    """Return the Cloudflare Turnstile site key for the frontend widget."""
    key = os.getenv("CF_TURNSTILE_SITE_KEY", "")
    return {"site_key": key}
