"""
Simple single-user authentication for the T9 web app.

If T9_PASSWORD is set in the environment, all /api/* and /ws routes
require a valid signed session cookie.  If it is NOT set, auth is
completely disabled (local-dev mode).
"""

import hashlib
import os

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel

# ── Configuration ────────────────────────────────────────────────────

COOKIE_NAME = "t9_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds
SESSION_PAYLOAD = "t9_authenticated"

_password: str | None = None
_serializer: URLSafeTimedSerializer | None = None
_secure_cookie: bool = False


def configure_auth():
    """Read T9_PASSWORD from environment and set up the signer."""
    global _password, _serializer, _secure_cookie
    _password = os.getenv("T9_PASSWORD")
    _secure_cookie = os.getenv("T9_SECURE_COOKIE", "").lower() in ("1", "true", "yes")
    if _password:
        # Derive a signing secret from the password so a weak password
        # doesn't directly weaken the HMAC. The salt is fixed per-app
        # (not per-user) which is fine for single-user cookie signing.
        secret = hashlib.sha256(
            f"t9-session-signing:{_password}".encode()
        ).hexdigest()
        _serializer = URLSafeTimedSerializer(secret)


def auth_required() -> bool:
    return _password is not None


def validate_cookie(cookie_value: str) -> bool:
    """Return True if the cookie is a valid, non-expired session."""
    if not _serializer:
        return False
    try:
        data = _serializer.loads(cookie_value, max_age=COOKIE_MAX_AGE)
        return data == SESSION_PAYLOAD
    except (BadSignature, SignatureExpired):
        return False


# ── Middleware ───────────────────────────────────────────────────────

# Paths that never require auth
_PUBLIC_PREFIXES = ("/api/auth/",)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # If no password configured, skip auth entirely
        if not auth_required():
            return await call_next(request)

        path = request.url.path

        # Allow auth endpoints through
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        # Allow non-API, non-WS routes (static frontend files)
        if not path.startswith("/api") and path != "/ws":
            return await call_next(request)

        # WebSocket auth is handled in the WS endpoint itself (middleware
        # can't easily intercept WS upgrades in Starlette), so let it through.
        if path == "/ws":
            return await call_next(request)

        # Check session cookie
        cookie = request.cookies.get(COOKIE_NAME)
        if not cookie or not validate_cookie(cookie):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        return await call_next(request)


# ── Router ───────────────────────────────────────────────────────────

router = APIRouter()


class LoginRequest(BaseModel):
    password: str


@router.post("/api/auth/login")
async def login(req: LoginRequest, response: Response):
    if not auth_required():
        return {"authenticated": True}

    if req.password != _password:
        raise HTTPException(status_code=403, detail="Wrong password")

    token = _serializer.dumps(SESSION_PAYLOAD)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=_secure_cookie,
        samesite="lax",
        path="/",
    )
    return {"authenticated": True}


@router.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"authenticated": False}


@router.get("/api/auth/status")
async def auth_status(request: Request):
    if not auth_required():
        return {"authenticated": True, "auth_required": False}

    cookie = request.cookies.get(COOKIE_NAME)
    authenticated = bool(cookie and validate_cookie(cookie))
    return {"authenticated": authenticated, "auth_required": True}
