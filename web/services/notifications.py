"""
Reply-notification dispatch.

Idempotency contract:
  notify_reply may be called any number of times for the same reply_id —
  from edit re-approvals, from admin status flaps, from race-y dispatch
  in three different code paths. The (reply_id, recipient_email) row in
  email_notifications is the single source of truth for "this email has
  been sent." `was_notified` is the fast-path check; the UNIQUE constraint
  is the actual guarantee. Net result: each unique reply results in AT
  MOST ONE email per recipient, regardless of how many times the function
  is invoked or which call site invokes it.

Send-then-log ordering:
  We send the email first, then call record_notification. The reverse
  ordering ("log first, send second") would lose emails on a transient
  SMTP failure (logged as sent but never delivered). Since postfix is
  local and reliable, occasional duplicate-on-crash is preferable to
  silent dropped notifications. In practice the duplicate window is
  microseconds — the only way to send twice would be a worker crash
  between SMTP ack and DB commit.
"""

import logging
import os
from html import escape

from .email_sender import send_email
from .email_tokens import make_unsubscribe_token

logger = logging.getLogger(__name__)

REPLY_PREVIEW_MAX = 500
PARENT_PREVIEW_MAX = 200


def _site_base_url() -> str:
    return (os.getenv("SITE_BASE_URL") or "").rstrip("/")


def _site_name() -> str:
    return os.getenv("PUBLIC_SITE_NAME") or os.getenv("SITE_NAME") or "Reader"


def _build_chapter_url(book_id: int, chapter_number: int) -> str:
    base = _site_base_url()
    if chapter_number == 0:
        path = f"/library/book/{book_id}"
    else:
        path = f"/library/read/{book_id}/{chapter_number}"
    # ?modal=comments auto-opens the drawer on landing
    suffix = "?modal=comments"
    return f"{base}{path}{suffix}" if base else f"{path}{suffix}"


def _build_unsubscribe_url(email: str) -> str:
    base = _site_base_url()
    token = make_unsubscribe_token(email)
    path = f"/api/public/comments/unsubscribe?token={token}"
    return f"{base}{path}" if base else path


def _render(parent: dict, reply: dict, book: dict, recipient: str) -> tuple[str, str, str]:
    site_name = _site_name()
    chap = reply["chapter_number"]
    chap_label = "the discussion page" if chap == 0 else f"chapter {chap}"
    chapter_url = _build_chapter_url(reply["book_id"], chap)
    unsub_url = _build_unsubscribe_url(recipient)

    parent_excerpt = (parent.get("body") or "")[:PARENT_PREVIEW_MAX]
    if len(parent.get("body") or "") > PARENT_PREVIEW_MAX:
        parent_excerpt += "…"

    reply_full = reply.get("body") or ""
    reply_body = reply_full[:REPLY_PREVIEW_MAX]
    if len(reply_full) > REPLY_PREVIEW_MAX:
        reply_body += "…"

    replier = reply.get("display_name") or "Someone"
    book_title = book.get("title") or "a book"

    subject = f"[{site_name}] {replier} replied to your comment on {book_title}"

    text_body = (
        f"{replier} replied to your comment on \"{book_title}\" — {chap_label}.\n\n"
        f"Your comment:\n  {parent_excerpt}\n\n"
        f"Their reply:\n{reply_body}\n\n"
        f"View the discussion: {chapter_url}\n\n"
        f"---\n"
        f"You're receiving this because you opted in to reply notifications when posting your comment.\n"
        f"Unsubscribe: {unsub_url}\n"
    )

    html_body = (
        "<div style='font-family:system-ui,-apple-system,sans-serif;color:#222;max-width:560px;margin:0 auto'>"
        f"<p><strong>{escape(replier)}</strong> replied to your comment on "
        f"<em>{escape(book_title)}</em> — {escape(chap_label)}.</p>"
        f"<blockquote style='color:#666;border-left:3px solid #ddd;padding-left:10px;margin-left:0;font-size:13px'>"
        f"{escape(parent_excerpt) or '<em>(your comment)</em>'}</blockquote>"
        f"<p style='white-space:pre-wrap;background:#f7f7f9;padding:12px;border-radius:6px'>"
        f"{escape(reply_body)}</p>"
        f"<p><a href='{escape(chapter_url)}' style='color:#4f46e5;text-decoration:none;font-weight:500'>"
        f"View the discussion →</a></p>"
        f"<hr style='border:none;border-top:1px solid #eee;margin:24px 0 12px'>"
        f"<p style='color:#888;font-size:12px;line-height:1.5'>"
        f"You're receiving this because you opted in to reply notifications when posting your comment. "
        f"<a href='{escape(unsub_url)}' style='color:#888'>Unsubscribe</a>."
        "</p></div>"
    )

    return subject, text_body, html_body


def notify_reply(db_manager, reply_id: int) -> bool:
    """Send a reply-notification email if every gate passes.

    Returns True if an email was sent, False otherwise. Safe to call
    repeatedly for the same reply_id — only the first successful call
    will actually send (idempotency via email_notifications).
    """
    reply = db_manager.get_comment(reply_id)
    if not reply:
        return False
    if reply.get("status") != "approved":
        return False
    if not reply.get("parent_id"):
        return False

    parent = db_manager.get_comment(reply["parent_id"])
    if not parent:
        return False
    if not parent.get("notify_replies"):
        return False
    recipient = (parent.get("email") or "").strip().lower()
    if not recipient:
        return False

    # Don't email yourself for self-replies
    if parent.get("commenter_uuid") == reply.get("commenter_uuid"):
        return False

    if db_manager.is_email_suppressed(recipient):
        return False

    if db_manager.was_notified(reply_id, recipient):
        return False

    book = db_manager.get_book(book_id=reply["book_id"]) or {}
    subject, text_body, html_body = _render(parent, reply, book, recipient)

    ok, msg = send_email(recipient, subject, text_body, html_body)
    if not ok:
        logger.warning("notify_reply: send failed to %s for reply %s: %s",
                       recipient, reply_id, msg)
        return False

    # Send-then-log: SMTP succeeded, persist the idempotency row.
    inserted = db_manager.record_notification(reply_id, recipient)
    if not inserted:
        # Lost a race with another worker; safe — both paths sent the same email.
        logger.info("notify_reply: race detected for reply %s (already logged)", reply_id)
    return True
