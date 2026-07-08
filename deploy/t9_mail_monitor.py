#!/usr/bin/env python3
"""
T9 Mail Monitor

Watches the local `editor` mailbox for replies to translation-request emails and
pushes each new reply into the app so the admin sees it in the Recommendations
page — no need to log into the editor Unix account.

Runs as a systemd SYSTEM service dropped to User=editor (owns /var/mail/editor).
Self-contained: stdlib only, no repo imports, no DB access. It reads the mbox
READ-ONLY (never mutates it) and POSTs parsed replies to the app's ingest
endpoint, authenticated by a shared MAIL_INGEST_TOKEN.

Correlation (matching a reply to its request) is done server-side; this daemon
only forwards messages that *look* rec-correlated — a signed plus-address tag in
a Delivered-To/To header, or our <rec<id>.…> Message-ID echoed in In-Reply-To /
References. That filter keeps unrelated mail (e.g. comment-notification replies,
which share the editor@ From) out of the pipeline.

Environment variables:
    T9_API_URL            App base URL            (default: http://127.0.0.1:8000)
    MAIL_INGEST_TOKEN     Shared ingest secret    (required)
    MBOX_PATH             mbox file to watch      (default: /var/mail/editor)
    MAILMON_POLL_INTERVAL Seconds between polls   (default: 60)
    MAILMON_STATE         State/dedup JSON path   (default: /var/lib/t9-mail-monitor/state.json)
    MAILMON_LOG           Log file path           (default: <repo>/logs/t9_mail_monitor.log)
    MAILMON_SEEN_CAP      Max remembered msg-ids  (default: 5000)

Usage:
    python3 deploy/t9_mail_monitor.py           # run forever (poll loop)
    python3 deploy/t9_mail_monitor.py --once     # single poll cycle, then exit
"""

import hashlib
import json
import logging
import logging.handlers
import mailbox
import os
import re
import sys
import time
import urllib.request
import urllib.error

# ── Configuration ────────────────────────────────────────────────────

API_URL = os.environ.get("T9_API_URL", "http://127.0.0.1:8000").rstrip("/")
INGEST_TOKEN = os.environ.get("MAIL_INGEST_TOKEN", "")
MBOX_PATH = os.environ.get("MBOX_PATH", "/var/mail/editor")
POLL_INTERVAL = int(os.environ.get("MAILMON_POLL_INTERVAL", "60"))
STATE_PATH = os.environ.get("MAILMON_STATE", "/var/lib/t9-mail-monitor/state.json")
SEEN_CAP = int(os.environ.get("MAILMON_SEEN_CAP", "5000"))
LOG_FILE = os.environ.get(
    "MAILMON_LOG",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "logs", "t9_mail_monitor.log"),
)

# Only forward mail that looks rec-correlated (server verifies the signature).
_PLUS_TAG_RE = re.compile(r"\+r\d+-[0-9a-fA-F]{6,}@")
_MSGID_RE = re.compile(r"rec\d+\.[0-9a-fA-F]+@")

# ── Logging ──────────────────────────────────────────────────────────

_handlers = [logging.StreamHandler(sys.stdout)]
try:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    _handlers.append(logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"))
except OSError as _e:
    print(f"[mailmon] could not open log file {LOG_FILE}: {_e}", file=sys.stderr)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=_handlers)
log = logging.getLogger("t9-mail-monitor")


# ── State (dedup) ────────────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        return {"seen": list(data.get("seen", [])), "size": int(data.get("size", 0))}
    except (OSError, ValueError):
        return {"seen": [], "size": 0}


def save_state(state):
    # Cap the seen list to the most-recent N ids to bound the file size.
    seen = state["seen"][-SEEN_CAP:]
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"seen": seen, "size": state.get("size", 0)}, f)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        log.error("could not persist state to %s: %s", STATE_PATH, e)


# ── Message parsing ──────────────────────────────────────────────────

def _body_text(msg):
    """Extract the text/plain body from an email.message.Message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and \
                    "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True)
                if payload is not None:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return msg.get_payload() or ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _msg_uid(msg, from_email, subject, date):
    """Stable dedup key: Message-ID if present, else a hash of headers+body."""
    mid = (msg.get("Message-ID") or "").strip()
    if mid:
        return mid
    h = hashlib.sha256(
        f"{from_email}|{subject}|{date}|{_body_text(msg)[:500]}".encode(
            "utf-8", errors="replace")).hexdigest()
    return f"sha256:{h}"


def _looks_correlated(msg):
    for header in msg.get_all("Delivered-To", []) + msg.get_all("X-Original-To", []):
        if _PLUS_TAG_RE.search(header or ""):
            return True
    if _PLUS_TAG_RE.search(msg.get("To", "") or ""):
        return True
    for header in ("In-Reply-To", "References"):
        if _MSGID_RE.search(msg.get(header, "") or ""):
            return True
    return False


def _parse(msg):
    from email.utils import parseaddr
    name, addr = parseaddr(msg.get("From", ""))
    return {
        "from_email": addr or None,
        "from_name": name or None,
        "subject": msg.get("Subject", "") or None,
        "body": _body_text(msg),
        "message_id": (msg.get("Message-ID") or "").strip() or None,
        "in_reply_to": (msg.get("In-Reply-To") or "").strip() or None,
        "references": (msg.get("References") or "").strip() or None,
        "to": msg.get("To", "") or None,
        "delivered_to": [h for h in (msg.get_all("Delivered-To", []) +
                                     msg.get_all("X-Original-To", [])) if h],
        "date": msg.get("Date", "") or None,
    }


# ── Ingest ───────────────────────────────────────────────────────────

def post_reply(payload):
    """POST one parsed reply to the app. Returns True on 2xx."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_URL}/api/mail/ingest-reply", data=data, method="POST",
        headers={"Content-Type": "application/json", "X-Ingest-Token": INGEST_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            log.info("ingested msg (matched=%s rec=%s corr=%s dup=%s)",
                     body.get("matched"), body.get("recommendation_id"),
                     body.get("correlation"), body.get("duplicate"))
            return True
    except urllib.error.HTTPError as e:
        log.error("ingest HTTP %s: %s", e.code, e.read()[:200])
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.error("ingest failed: %s", e)
    return False


# ── Poll cycle ───────────────────────────────────────────────────────

def poll_once(state):
    """One pass over the mbox. Mutates and returns state. Non-destructive."""
    if not os.path.exists(MBOX_PATH):
        log.debug("mbox %s does not exist yet", MBOX_PATH)
        return state

    try:
        size = os.path.getsize(MBOX_PATH)
    except OSError as e:
        log.error("cannot stat mbox %s: %s", MBOX_PATH, e)
        return state

    # Mailbox truncated/rotated (Postfix or the user cleared it): keep the seen
    # set (dedup is by id, not offset) but reset the recorded size.
    if size < state.get("size", 0):
        log.info("mbox shrank (%d -> %d); rotation/clear detected", state["size"], size)
    state["size"] = size

    seen = set(state["seen"])
    new_ids = []
    forwarded = 0

    # Read-only: iterate messages but never call .flush()/.lock()/mutate.
    box = mailbox.mbox(MBOX_PATH)
    try:
        for msg in box:
            parsed_from = ""
            try:
                from email.utils import parseaddr
                parsed_from = parseaddr(msg.get("From", ""))[1]
                uid = _msg_uid(msg, parsed_from, msg.get("Subject", ""), msg.get("Date", ""))
                if uid in seen:
                    continue
                if not _looks_correlated(msg):
                    # Not a request reply — remember it so we don't re-scan it,
                    # but don't forward.
                    seen.add(uid)
                    new_ids.append(uid)
                    continue
                payload = _parse(msg)
                if post_reply(payload):
                    seen.add(uid)
                    new_ids.append(uid)
                    forwarded += 1
                # On failure, leave uid unseen so it's retried next cycle.
            except Exception as e:  # never let one bad message kill the pass
                log.exception("error handling a message (from=%s): %s", parsed_from, e)
    finally:
        box.close()

    if new_ids:
        state["seen"].extend(new_ids)
        save_state(state)
        log.info("cycle done: %d new message(s), %d forwarded", len(new_ids), forwarded)
    return state


def main():
    once = "--once" in sys.argv[1:]
    if not INGEST_TOKEN:
        log.error("MAIL_INGEST_TOKEN is not set — refusing to start.")
        sys.exit(1)

    log.info("Mail monitor starting — mbox=%s api=%s interval=%ds%s",
             MBOX_PATH, API_URL, POLL_INTERVAL, " (once)" if once else "")
    state = load_state()

    if once:
        poll_once(state)
        return

    while True:
        try:
            state = poll_once(state)
        except Exception as e:
            log.exception("poll cycle failed: %s", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
