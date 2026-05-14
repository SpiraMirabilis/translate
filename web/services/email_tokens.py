"""
Signed unsubscribe tokens for reply-notification emails.

Reuses itsdangerous (already a dependency via web/auth.py). The salt
namespaces these tokens away from session cookies so the same secret
can sign both without crossover. Tokens effectively never expire so
unsubscribe links remain valid years after an email is sent.
"""

import os

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

_SALT = "comments-unsubscribe"
_MAX_AGE = 10 * 365 * 24 * 3600   # ~10 years; effectively non-expiring


def _secret() -> str:
    # Same precedence used by web/auth.py: explicit SECRET_KEY → T9_PASSWORD →
    # dev fallback. If the admin password rotates, outstanding unsubscribe
    # tokens become invalid; that's an acceptable cost of not introducing a
    # second long-lived secret.
    return os.getenv("SECRET_KEY") or os.getenv("T9_PASSWORD") or "dev-secret"


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
