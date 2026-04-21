"""
Public endpoint for submitting novel translation recommendations.

Protected by Cloudflare Turnstile and rate limiting.
"""
import os
import time
import threading
import httpx
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/public")

_db = None


def init(db_manager):
    global _db
    _db = db_manager


# ------------------------------------------------------------------
# Rate limiting — stricter than the general public API
# ------------------------------------------------------------------

_RATE_WINDOW = 3600       # 1 hour
_RATE_LIMIT  = 5          # max 5 submissions per hour per IP
_hits: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()


def _rate_check(ip: str):
    now = time.time()
    cutoff = now - _RATE_WINDOW
    with _lock:
        bucket = _hits[ip]
        _hits[ip] = bucket = [t for t in bucket if t > cutoff]
        if len(bucket) >= _RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Too many submissions. Please try again later.")
        bucket.append(now)


# ------------------------------------------------------------------
# Turnstile verification
# ------------------------------------------------------------------

_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def _verify_turnstile(token: str, ip: str) -> bool:
    secret = os.getenv("CF_TURNSTILE_SECRET_KEY", "")
    if not secret:
        # If no secret configured, skip verification (dev mode)
        return True
    async with httpx.AsyncClient() as client:
        resp = await client.post(_TURNSTILE_VERIFY_URL, data={
            "secret": secret,
            "response": token,
            "remoteip": ip,
        })
        result = resp.json()
        return result.get("success", False)


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
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host
    _rate_check(ip)

    # Verify Turnstile
    valid = await _verify_turnstile(req.turnstile_token, ip)
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
async def get_turnstile_site_key():
    """Return the Cloudflare Turnstile site key for the frontend widget."""
    key = os.getenv("CF_TURNSTILE_SITE_KEY", "")
    return {"site_key": key}
