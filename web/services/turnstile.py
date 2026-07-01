"""
Cloudflare Turnstile verification.

Single async helper that POSTs the user-supplied token + remote IP to
Cloudflare's siteverify endpoint and returns (ok, error_codes).
Falls open in dev mode (no CF_TURNSTILE_SECRET_KEY configured) so
local development doesn't require a real CF account.
"""

import os

import httpx

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT = 5.0


async def verify(token: str, ip: str) -> tuple[bool, str | None]:
    """
    Verify a Turnstile token. Returns (success, error_codes_string).

    - In production (CF_TURNSTILE_SECRET_KEY set): real verification.
    - In dev (no secret): always returns (True, None) — fail-open — unless
      TURNSTILE_REQUIRED=1, which fails closed on missing configuration.
    - On network/HTTP error: (False, 'network') — fail-closed for writes.
    """
    secret = os.getenv("CF_TURNSTILE_SECRET_KEY", "").strip()
    if not secret:
        if os.getenv("TURNSTILE_REQUIRED", "").strip() == "1":
            return (False, "unconfigured")
        return (True, None)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(VERIFY_URL, data={
                "secret": secret,
                "response": token,
                "remoteip": ip,
            })
            data = resp.json()
            if data.get("success"):
                return (True, None)
            errs = ",".join(data.get("error-codes") or []) or "verification-failed"
            return (False, errs)
    except httpx.HTTPError:
        return (False, "network")
    except Exception:
        return (False, "unknown")
