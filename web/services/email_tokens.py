"""
Signed unsubscribe tokens for reply-notification emails.

Reuses itsdangerous (already a dependency via web/auth.py). The salt
namespaces these tokens away from session cookies so the same secret
can sign both without crossover. Tokens effectively never expire so
unsubscribe links remain valid years after an email is sent.
"""

import hashlib
import hmac
import os

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

_SALT = "comments-unsubscribe"
_MAX_AGE = 10 * 365 * 24 * 3600   # ~10 years; effectively non-expiring


def _secret() -> str:
    # Same precedence used by web/auth.py: explicit SECRET_KEY → T9_PASSWORD →
    # dev fallback. If the admin password rotates, outstanding unsubscribe
    # tokens become invalid; that's an acceptable cost of not introducing a
    # second long-lived secret.
    secret = os.getenv("SECRET_KEY") or os.getenv("T9_PASSWORD")
    if secret:
        return secret
    # EMAIL_FROM configured means these tokens go to real recipients — a
    # well-known signing key would make unsubscribe/reply-correlation tokens
    # forgeable. Refuse rather than sign with the dev fallback.
    if os.getenv("EMAIL_FROM"):
        raise RuntimeError(
            "Refusing to sign email tokens with the dev fallback secret; "
            "set SECRET_KEY (or T9_PASSWORD) in .env")
    return "dev-secret"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt=_SALT)


def make_unsubscribe_token(email: str) -> str:
    return _serializer().dumps((email or "").strip().lower())


def verify_unsubscribe_token(token: str):
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# --- Recommendation reply-correlation tags -------------------------------
# Short HMAC signature embedded in the plus-addressed Reply-To
# (editor+r<id>-<sig>@…). Hex-only so it's safe in an email local-part; the
# recommendation id travels in the clear (it's not secret) and the signature
# just prevents forged correlation.

def sign_rec(rec_id: int) -> str:
    digest = hmac.new(_secret().encode(), f"rec:{int(rec_id)}".encode(),
                      hashlib.sha256).hexdigest()
    return digest[:10]


def verify_rec(rec_id: int, sig: str) -> bool:
    if not sig:
        return False
    return hmac.compare_digest(sign_rec(rec_id), sig.strip().lower())
