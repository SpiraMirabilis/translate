"""Out-of-band alerts for NovelUpdates posting: email to the editor and an
ntfy push. The activity-log line is written by the caller (nu_sync), which
knows the book; this module only fans the alert out. Every channel is
best-effort — an alert that can't be delivered is logged, never raised, so a
broken mail setup can't stall the NU lane."""
import logging
import os
import urllib.request

_log = logging.getLogger("nu_alerts")


def _setting(key, default=""):
    try:
        import settings_store
        val = settings_store.get(key, default)
    except Exception:
        val = default
    return default if val is None else val


def _env_secret(key):
    try:
        import scrape_sync
        return scrape_sync._env_secret(key)
    except Exception:
        return os.environ.get(key, "")


def alert_email():
    """Where alerts go: nu_alert_email, else the address we send mail from
    (the editor mailbox)."""
    return (str(_setting("nu_alert_email") or "").strip()
            or os.environ.get("EMAIL_FROM", "").strip())


def send_email(db, subject, text):
    to = alert_email()
    if not to:
        return False
    try:
        from web.services import email_sender
        ok, msg = email_sender.send_logged(db, "nu_alert", to, subject, text)
        if not ok:
            _log.warning(f"NU alert email to {to} failed: {msg}")
        return ok
    except Exception as e:
        _log.warning(f"NU alert email failed: {e}")
        return False


def send_ntfy(title, text, click=None):
    url = str(_setting("nu_ntfy_url") or "").strip()
    if not url:
        return False
    headers = {"Title": title.encode("utf-8").decode("latin-1", "replace"), "Tags": "warning"}
    if click:
        headers["Click"] = click
    token = _env_secret("NU_NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception as e:
        _log.warning(f"NU ntfy push failed: {e}")
        return False


def alert(db, subject, text, link=None):
    """Email + ntfy. Returns {channel: delivered}."""
    body = text + (f"\n\n{link}" if link else "")
    return {"email": send_email(db, f"[NovelUpdates] {subject}", body),
            "ntfy": send_ntfy(f"NovelUpdates: {subject}", text, click=link)}
