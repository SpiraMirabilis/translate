"""
Local-Postfix SMTP sender.

Postfix on the same host is already configured with DKIM/SPF and not
on any blocklists, so deliverability is a non-issue. We open an SMTP
connection to localhost:25 with no auth.
"""

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)

SMTP_HOST = "localhost"
SMTP_PORT = 25
SMTP_TIMEOUT = 10


def _from_address() -> str:
    return os.getenv("EMAIL_FROM", "noreply@localhost")


def send_email(to_addr: str, subject: str, text_body: str,
               html_body: Optional[str] = None,
               extra_headers: Optional[dict] = None) -> tuple[bool, str]:
    """Send an email via the local Postfix MTA. Returns (ok, message)."""
    if not to_addr:
        return (False, "no recipient")
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

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as s:
            s.send_message(msg)
        return (True, "ok")
    except (smtplib.SMTPException, OSError) as e:
        logger.warning("send_email to %s failed: %s", to_addr, e)
        return (False, str(e)[:200])
