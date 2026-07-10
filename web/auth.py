"""
Simple single-user authentication for the T9 web app.

If T9_PASSWORD is set in the environment, all /api/* and /ws routes
require a valid signed session cookie.  If it is NOT set, auth is
completely disabled (local-dev mode).
"""

import hashlib
import hmac
import os

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import BaseModel

from web.services import public_guard
from web.services.ip import client_ip, is_trusted_proxy

# ── Configuration ────────────────────────────────────────────────────

COOKIE_NAME = "t9_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds
SESSION_PAYLOAD = "t9_authenticated"

_password: str | None = None
_serializer: URLSafeTimedSerializer | None = None
_secure_cookie: bool | None = None  # None = auto-detect from request
_public_library: bool = True  # Whether /api/public/* is accessible without auth

# Login brute-force guards (per-IP sliding windows).
_login_short = public_guard.SlidingWindowLimiter(
    60, 5, "Too many login attempts. Try again in a minute.")
_login_long = public_guard.SlidingWindowLimiter(
    3600, 20, "Too many login attempts. Try again later.")


def is_public_library() -> bool:
    return _public_library


def reset_login_limiters():
    """Clear login rate-limit state (test hook / after configure_auth)."""
    _login_short.reset()
    _login_long.reset()


def configure_auth():
    """Read T9_PASSWORD from environment and set up the signer."""
    global _password, _serializer, _secure_cookie, _public_library
    _password = os.getenv("T9_PASSWORD")
    _public_library = os.getenv("T9_PUBLIC_LIBRARY", "1").lower() not in ("0", "false", "no")
    env_val = os.getenv("T9_SECURE_COOKIE", "").lower()
    if env_val in ("1", "true", "yes"):
        _secure_cookie = True
    elif env_val in ("0", "false", "no"):
        _secure_cookie = False
    else:
        _secure_cookie = None  # auto-detect from X-Forwarded-Proto
    if _password:
        # Derive a signing secret from the password so a weak password
        # doesn't directly weaken the HMAC. The salt is fixed per-app
        # (not per-user) which is fine for single-user cookie signing.
        secret = hashlib.sha256(
            f"t9-session-signing:{_password}".encode()
        ).hexdigest()
        _serializer = URLSafeTimedSerializer(secret)
    # Fresh limiters whenever auth is (re)configured so test suites that
    # rebuild apps and re-login don't trip the 5/min guard on 127.0.0.1.
    reset_login_limiters()


def auth_required() -> bool:
    return _password is not None


def _is_secure(request: Request) -> bool:
    """Determine whether the cookie Secure flag should be set."""
    if _secure_cookie is not None:
        return _secure_cookie
    # Only trust X-Forwarded-Proto from a trusted reverse proxy (same
    # allowlist as client_ip). A direct client forging the header must not
    # force Secure=False (or True) on the session cookie.
    if is_trusted_proxy(request):
        return request.headers.get("x-forwarded-proto", "").lower() == "https"
    # Direct connection: Secure if the request itself is HTTPS.
    return request.url.scheme == "https"


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
_ALWAYS_PUBLIC_PREFIXES = ("/api/auth/", "/api/health", "/api/mail/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # If no password configured, skip auth entirely
        if not auth_required():
            return await call_next(request)

        path = request.url.path

        # Always-public endpoints (auth, health)
        if any(path.startswith(p) for p in _ALWAYS_PUBLIC_PREFIXES):
            return await call_next(request)

        # Public library API — only bypass auth when the setting is on
        if path.startswith("/api/public/") and _public_library:
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
async def login(req: LoginRequest, request: Request, response: Response):
    if not auth_required():
        return {"authenticated": True}

    ip = client_ip(request) or "unknown"
    _login_short.check(ip)
    _login_long.check(ip)

    # Constant-time compare via fixed-length digests so unequal password
    # lengths don't short-circuit or raise on hmac.compare_digest.
    offered_h = hashlib.sha256((req.password or "").encode("utf-8")).digest()
    expected_h = hashlib.sha256((_password or "").encode("utf-8")).digest()
    if not hmac.compare_digest(offered_h, expected_h):
        raise HTTPException(status_code=403, detail="Wrong password")

    secure = _is_secure(request)
    token = _serializer.dumps(SESSION_PAYLOAD)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return {"authenticated": True}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/", secure=_is_secure(request))
    return {"authenticated": False}


@router.get("/api/auth/status")
async def auth_status(request: Request):
    if not auth_required():
        return {"authenticated": True, "auth_required": False, "public_library": _public_library}

    cookie = request.cookies.get(COOKIE_NAME)
    authenticated = bool(cookie and validate_cookie(cookie))
    return {"authenticated": authenticated, "auth_required": True, "public_library": _public_library}
