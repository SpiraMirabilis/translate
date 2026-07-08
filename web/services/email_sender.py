"""
Email sender with pluggable backend.

Two delivery backends, selected at send time by the EMAIL_BACKEND env var
(mirrored from settings.json; default "ses"):

- "ses"     — Amazon SES via the boto3 API (send_raw_email). Credentials and
              region live in .env (SES_ACCESS_KEY_ID / SES_SECRET_ACCESS_KEY /
              SES_REGION), following the DigitalOcean Spaces convention. This is
              the default; falls back to Postfix automatically when SES is not
              configured.
- "postfix" — Local Postfix MTA on localhost:25, no auth. Kept as an instant,
              code-free rollback path.

Callers only depend on the (ok: bool, message: str) return contract, which is
identical across backends.
"""

import logging
import os
import smtplib
import threading
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)

SMTP_HOST = "localhost"
SMTP_PORT = 25
SMTP_TIMEOUT = 10

# Cached boto3 SES client (mirrors the pattern in spaces.py). Rebuilt when the
# (region, access_key, secret_key) config tuple changes.
_ses_lock = threading.Lock()
_ses_client = None
_ses_client_cfg = None


def _from_address() -> str:
    return os.getenv("EMAIL_FROM", "noreply@localhost")


def _build_message(to_addr: str, subject: str, text_body: str,
                   html_body: Optional[str] = None,
                   extra_headers: Optional[dict] = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _from_address()
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    if extra_headers:
        for k, v in extra_headers.items():
            msg[k] = v
    return msg


def _get_ses_client():
    """Return a cached boto3 SES client, or None if not configured.

    SES-specific env vars (not the generic AWS_* chain) so boto3's ambient
    credential resolution can't interfere with the Spaces S3 client.
    """
    global _ses_client, _ses_client_cfg
    region = os.getenv("SES_REGION", "us-east-2")
    access_key = os.getenv("SES_ACCESS_KEY_ID", "")
    secret_key = os.getenv("SES_SECRET_ACCESS_KEY", "")
    if not (access_key and secret_key):
        return None
    key = (region, access_key, secret_key)
    with _ses_lock:
        if _ses_client is not None and _ses_client_cfg == key:
            return _ses_client
        try:
            import boto3
            _ses_client = boto3.client(
                "ses",
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            _ses_client_cfg = key
            return _ses_client
        except Exception as e:
            logger.error("Failed to init SES client: %s", e)
            _ses_client = None
            return None


def _send_via_ses(client, msg: EmailMessage, to_addr: str) -> tuple[bool, str]:
    """Send a composed MIME message through Amazon SES. Returns (ok, message)."""
    try:
        resp = client.send_raw_email(
            Source=_from_address(),
            Destinations=[to_addr],
            RawMessage={"Data": msg.as_bytes()},
        )
        return (True, resp.get("MessageId", "ok"))
    except Exception as e:
        logger.warning("SES send_email to %s failed: %s", to_addr, e)
        return (False, str(e)[:200])


def _send_via_postfix(msg: EmailMessage, to_addr: str) -> tuple[bool, str]:
    """Send a composed MIME message through the local Postfix MTA. Returns (ok, message)."""
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as s:
            s.send_message(msg)
        return (True, "ok")
    except (smtplib.SMTPException, OSError) as e:
        logger.warning("Postfix send_email to %s failed: %s", to_addr, e)
        return (False, str(e)[:200])


def send_email(to_addr: str, subject: str, text_body: str,
               html_body: Optional[str] = None,
               extra_headers: Optional[dict] = None) -> tuple[bool, str]:
    """Send an email via the configured backend (SES or Postfix). Returns (ok, message)."""
    if not to_addr:
        return (False, "no recipient")
    msg = _build_message(to_addr, subject, text_body, html_body, extra_headers)

    backend = os.getenv("EMAIL_BACKEND", "ses").lower()
    if backend == "ses":
        client = _get_ses_client()
        if client is not None:
            return _send_via_ses(client, msg, to_addr)
        # SES selected but unconfigured: degrade to Postfix rather than error.
        logger.warning("EMAIL_BACKEND=ses but SES is not configured; falling back to Postfix")
    return _send_via_postfix(msg, to_addr)
