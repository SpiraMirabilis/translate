"""
Mail ingest endpoint for the mbox-monitor daemon.

The daemon (deploy/t9_mail_monitor.py) runs as the `editor` user, reads new
messages from /var/mail/editor, and POSTs the parsed fields here. This endpoint
correlates each reply back to a recommendation, dedups by Message-ID, and stores
it in recommendation_replies.

Auth is a dedicated shared secret (MAIL_INGEST_TOKEN) in the X-Ingest-Token
header — NOT the admin session cookie — so the daemon never holds the master
password. The /api/mail/ prefix is listed in web/auth._ALWAYS_PUBLIC_PREFIXES so
the cookie middleware doesn't 401 it; this handler self-guards with the token.
"""
import hmac
import os
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List

from web.services.email_tokens import verify_rec

router = APIRouter(prefix="/api/mail")

_db = None


def init(db_manager):
    global _db
    _db = db_manager


# editor+r<id>-<sig>@domain  (sig is 10 hex chars from email_tokens.sign_rec)
_PLUS_TAG_RE = re.compile(r"\+r(\d+)-([0-9a-fA-F]{6,})@")
# <rec<id>.<uuid>@domain>  (outbound Message-ID)
_MSGID_RE = re.compile(r"rec(\d+)\.[0-9a-fA-F]+@")

_QUOTE_BOUNDARY_RE = re.compile(
    r"^\s*(On .+ wrote:|-{2,} ?Original Message ?-{2,}|_{5,}|From: .+)\s*$"
)


class IngestReply(BaseModel):
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[str] = None
    to: Optional[str] = None
    delivered_to: Optional[List[str]] = None
    date: Optional[str] = None


def _check_token(request: Request):
    expected = os.getenv("MAIL_INGEST_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Mail ingest not configured")
    provided = request.headers.get("X-Ingest-Token", "")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Bad ingest token")


def _trim_quotes(body: str) -> str:
    """Best-effort strip of quoted reply history so the stored body is just the
    new text. Cuts at the first quote boundary / run of leading-'>' lines."""
    if not body:
        return ""
    out = []
    for line in body.splitlines():
        if _QUOTE_BOUNDARY_RE.match(line):
            break
        out.append(line)
    # Drop trailing blank / quoted lines left dangling before the boundary.
    while out and (not out[-1].strip() or out[-1].lstrip().startswith(">")):
        out.pop()
    trimmed = "\n".join(out).strip()
    return trimmed or body.strip()


def _correlate(payload: IngestReply):
    """Return (recommendation_id | None, correlation_kind)."""
    # (a) signed plus-address tag in any Delivered-To / To header.
    candidates = list(payload.delivered_to or [])
    if payload.to:
        candidates.append(payload.to)
    for value in candidates:
        m = _PLUS_TAG_RE.search(value or "")
        if m:
            rec_id, sig = int(m.group(1)), m.group(2)
            if verify_rec(rec_id, sig) and _db.get_recommendation(rec_id):
                return (rec_id, "plus")

    # (b) Message-ID correlation is deliberately NOT accepted without a
    # signature: In-Reply-To / References are attacker-controlled headers
    # once mail reaches the monitored mailbox, and rec{N}.hex@ is forgeable.
    # Only the signed plus-address tag (path a) is trusted for auto-match.
    # Unmatched replies still land in the admin Unmatched tab for manual link.

    # (c) give up — store as unmatched (surfaced in the admin Unmatched tab).
    return (None, "unmatched")


@router.post("/ingest-reply")
def ingest_reply(payload: IngestReply, request: Request):
    _check_token(request)

    rec_id, correlation = _correlate(payload)
    reply_id, inserted = _db.insert_reply({
        "recommendation_id": rec_id,
        "from_email": (payload.from_email or "").strip() or None,
        "from_name": (payload.from_name or "").strip() or None,
        "subject": (payload.subject or "").strip() or None,
        "body": _trim_quotes(payload.body or ""),
        "message_id": (payload.message_id or "").strip() or None,
        "in_reply_to": (payload.in_reply_to or "").strip() or None,
        "correlation": correlation,
        "received_at": (payload.date or "").strip() or None,
    })
    return {
        "status": "ok",
        "matched": rec_id is not None,
        "recommendation_id": rec_id,
        "correlation": correlation,
        "duplicate": not inserted,
        "reply_id": reply_id,
    }
