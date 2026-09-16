#!/usr/bin/env python3
"""Authenticated httpx client for the T9 admin API — without the login round-trip.

The admin session cookie is an itsdangerous token signed with a secret derived
from T9_PASSWORD, so a CLI that already knows the password can mint the cookie
itself instead of POSTing /api/auth/login.

That matters because login is rate-limited per IP — 5 per minute AND 20 per hour
(`web/auth.py::_login_short` / `_login_long`) — and these CLIs log in on every
invocation. Polling `translation_status.py` in a drain loop burns the hourly
budget in minutes and then locks the user out of the admin UI too; worse, the
lockout makes `stop_auto_process.py` fail, so a "stop the queue before sweeping"
safeguard can silently not run.

⚠️ The derivation below MUST match `web/auth.py::configure_auth`. If the cookie
name, session payload, or secret derivation changes there, change it here too.

Usage:
    from t9_client import client
    with client() as c:
        r = c.get("/api/translate/status")
"""
import hashlib
import os

import httpx
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer

# Mirrors web/auth.py
COOKIE_NAME = "t9_session"
SESSION_PAYLOAD = "t9_authenticated"
DEFAULT_URL = "http://127.0.0.1:8000"


def session_cookie(password: str) -> str:
    """Mint a session cookie the server will accept, exactly as login would."""
    secret = hashlib.sha256(
        f"t9-session-signing:{password}".encode()
    ).hexdigest()
    return URLSafeTimedSerializer(secret).dumps(SESSION_PAYLOAD)


def client(url: str = DEFAULT_URL, password: str | None = None,
           timeout: float = 30) -> httpx.Client:
    """An httpx.Client already carrying a valid admin session cookie.

    When no password is configured the server runs with auth disabled, so the
    cookie is simply omitted and every request is allowed through.
    """
    load_dotenv()
    if password is None:
        password = os.getenv("T9_PASSWORD")
    cookies = {COOKIE_NAME: session_cookie(password)} if password else {}
    return httpx.Client(base_url=url, timeout=timeout, cookies=cookies)


if __name__ == "__main__":
    # Smoke test: reach an authenticated endpoint without logging in.
    import json
    import sys

    with client() as c:
        r = c.get("/api/translate/status")
        print(r.status_code, json.dumps(r.json())[:200])
        sys.exit(0 if r.status_code == 200 else 1)
