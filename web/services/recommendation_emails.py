"""
Email dispatch for novel-translation recommendation requests.

Three requester-facing email types, all routed through the shared SES/Postfix
sender (email_sender.send_email) and all carrying an unsubscribe link:

  - send_admin_message   : admin-composed free-text message ("we need more info…")
  - send_submit_confirmation : auto "we received your request" on submit
  - send_status_change   : auto notice when a request is accepted or dismissed

Unlike reply-notifications, these are deliberate single-shot sends (one admin
action, one submit, one status transition), so there's no idempotency log — we
only honour the global email suppression list, which is shared with the comment
system. An unsubscribe from ANY email type suppresses the address for all of
them (the suppression table is keyed on email alone).

Replies to these emails go to EMAIL_FROM via an explicit Reply-To header, so a
requester answering an "info needed" message reaches the site's mailbox.
"""

import logging
import os
import uuid
from html import escape

from .email_sender import send_email
from .email_tokens import sign_rec
# Reuse the comment system's site/unsubscribe helpers — the unsubscribe token is
# email-only and its endpoint is generic per-address, so it works unchanged here.
from .notifications import _site_base_url, _site_name, _build_unsubscribe_url

logger = logging.getLogger(__name__)

MESSAGE_MAX = 4000


def _from_address() -> str:
    return (os.getenv("EMAIL_FROM") or "").strip()


def _reply_headers(rec_id: int) -> dict:
    """Reply-To + Message-ID tagged so the mail-monitor daemon can correlate a
    reply back to this request. The signed plus-address is the primary tag; the
    Message-ID is a backup for clients that reply to From instead of Reply-To.
    Falls back to a plain Reply-To if EMAIL_FROM isn't a normal addr@domain."""
    from_addr = _from_address()
    headers = {"Reply-To": from_addr}
    if "@" not in from_addr:
        return headers
    localpart, domain = from_addr.rsplit("@", 1)
    tagged = f"{localpart}+r{int(rec_id)}-{sign_rec(rec_id)}@{domain}"
    display = f"{_site_name()} Editor"
    headers["Reply-To"] = f'"{display}" <{tagged}>'
    headers["Message-ID"] = f"<rec{int(rec_id)}.{uuid.uuid4().hex}@{domain}>"
    return headers


def _wrap_html(inner: str, unsub_url: str) -> str:
    return (
        "<div style='font-family:system-ui,-apple-system,sans-serif;color:#222;max-width:560px;margin:0 auto'>"
        f"{inner}"
        "<hr style='border:none;border-top:1px solid #eee;margin:24px 0 12px'>"
        "<p style='color:#888;font-size:12px;line-height:1.5'>"
        f"You're receiving this because you submitted a translation request to {escape(_site_name())}. "
        f"<a href='{escape(unsub_url)}' style='color:#888'>Unsubscribe</a>."
        "</p></div>"
    )


def _footer_text(unsub_url: str) -> str:
    return (
        "\n\n---\n"
        f"You're receiving this because you submitted a translation request to {_site_name()}.\n"
        f"Unsubscribe: {unsub_url}\n"
    )


def _deliver(db_manager, rec: dict, subject: str,
             text_main: str, html_main: str) -> tuple[bool, str]:
    """Shared gate + send. Returns (ok, detail)."""
    recipient = (rec.get("requester_email") or "").strip().lower()
    if not recipient:
        return (False, "no recipient")
    if not _from_address():
        return (False, "email disabled (EMAIL_FROM unset)")
    if db_manager.is_email_suppressed(recipient):
        return (False, "suppressed")

    unsub_url = _build_unsubscribe_url(recipient)
    text_body = text_main + _footer_text(unsub_url)
    html_body = _wrap_html(html_main, unsub_url)
    extra_headers = _reply_headers(rec.get("id"))

    ok, msg = send_email(recipient, subject, text_body, html_body, extra_headers=extra_headers)
    if not ok:
        logger.warning("recommendation email to %s (rec %s) failed: %s",
                       recipient, rec.get("id"), msg)
    return (ok, msg)


# ------------------------------------------------------------------
# Public senders
# ------------------------------------------------------------------

def send_submit_confirmation(db_manager, rec_id: int) -> tuple[bool, str]:
    """Auto-confirm receipt of a freshly submitted request."""
    rec = db_manager.get_recommendation(rec_id)
    if not rec:
        return (False, "not found")
    site = _site_name()
    name = rec.get("requester_name") or "there"
    title = rec.get("novel_title") or "the novel"

    subject = f"[{site}] We received your translation request"
    text_main = (
        f"Hi {name},\n\n"
        f"Thanks for requesting a translation of \"{title}\". We've logged your "
        f"request and will review it. If we need any more information, we'll reach "
        f"out to you at this address.\n\n"
        f"— {site}"
    )
    html_main = (
        f"<p>Hi {escape(name)},</p>"
        f"<p>Thanks for requesting a translation of <em>{escape(title)}</em>. "
        f"We've logged your request and will review it. If we need any more "
        f"information, we'll reach out to you at this address.</p>"
        f"<p style='color:#666'>— {escape(site)}</p>"
    )
    return _deliver(db_manager, rec, subject, text_main, html_main)


def send_status_change(db_manager, rec_id: int, new_status: str) -> tuple[bool, str]:
    """Notify the requester when their request is accepted or dismissed."""
    if new_status not in ("accepted", "dismissed"):
        return (False, "status not notifiable")
    rec = db_manager.get_recommendation(rec_id)
    if not rec:
        return (False, "not found")
    site = _site_name()
    name = rec.get("requester_name") or "there"
    title = rec.get("novel_title") or "the novel"
    base = _site_base_url()

    if new_status == "accepted":
        subject = f"[{site}] Your translation request was accepted"
        text_main = (
            f"Hi {name},\n\n"
            f"Good news — we're planning to translate \"{title}\", which you "
            f"requested. Keep an eye on {site}"
            + (f" ({base})" if base else "")
            + f" for new chapters.\n\n— {site}"
        )
        html_main = (
            f"<p>Hi {escape(name)},</p>"
            f"<p>Good news — we're planning to translate <em>{escape(title)}</em>, "
            f"which you requested. "
            + (f"Keep an eye on <a href='{escape(base)}' style='color:#4f46e5'>{escape(site)}</a> for new chapters."
               if base else f"Keep an eye on {escape(site)} for new chapters.")
            + "</p>"
            f"<p style='color:#666'>— {escape(site)}</p>"
        )
    else:  # dismissed
        subject = f"[{site}] Update on your translation request"
        text_main = (
            f"Hi {name},\n\n"
            f"Thanks for suggesting \"{title}\". After reviewing it, we won't be "
            f"able to take this one on right now. We appreciate the recommendation "
            f"and hope you'll suggest others.\n\n— {site}"
        )
        html_main = (
            f"<p>Hi {escape(name)},</p>"
            f"<p>Thanks for suggesting <em>{escape(title)}</em>. After reviewing it, "
            f"we won't be able to take this one on right now. We appreciate the "
            f"recommendation and hope you'll suggest others.</p>"
            f"<p style='color:#666'>— {escape(site)}</p>"
        )
    return _deliver(db_manager, rec, subject, text_main, html_main)


def send_admin_message(db_manager, rec_id: int, message: str) -> tuple[bool, str]:
    """Send an admin-composed free-text message to the requester."""
    body = (message or "").strip()
    if not body:
        return (False, "empty message")
    body = body[:MESSAGE_MAX]
    rec = db_manager.get_recommendation(rec_id)
    if not rec:
        return (False, "not found")
    site = _site_name()
    name = rec.get("requester_name") or "there"
    title = rec.get("novel_title") or "your translation request"

    subject = f"[{site}] Regarding your translation request for {title}"
    text_main = (
        f"Hi {name},\n\n"
        f"{body}\n\n"
        f"(Regarding your request to translate \"{title}\". You can reply directly "
        f"to this email.)\n\n"
        f"— {site}"
    )
    # Preserve the admin's line breaks; escape everything user/admin-provided.
    html_message = escape(body).replace("\n", "<br>")
    html_main = (
        f"<p>Hi {escape(name)},</p>"
        f"<p style='white-space:pre-wrap'>{html_message}</p>"
        f"<p style='color:#666;font-size:13px'>Regarding your request to translate "
        f"<em>{escape(title)}</em>. You can reply directly to this email.</p>"
        f"<p style='color:#666'>— {escape(site)}</p>"
    )
    return _deliver(db_manager, rec, subject, text_main, html_main)
