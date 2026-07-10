"""
Real-client IP resolution behind Cloudflare.

Prefers CF-Connecting-IP (set by Cloudflare's edge for every proxied
request), then falls back to X-Forwarded-For, then the direct socket
peer.

SECURITY: forwarded headers are only honoured when the direct socket
peer is a trusted proxy (default: loopback, since Apache proxies to a
localhost-bound uvicorn — override with TRUSTED_PROXY_IPS, a
comma-separated list). Apache must also strip/overwrite these headers
for non-Cloudflare peers (see deploy/apache2-reader.conf); the checks
here are the app-side backstop. Every candidate value must parse as a
real IP address, so a forged header can't poison reader_log, reach
gethostbyaddr(), or frame an arbitrary string as a "client IP".
"""

import ipaddress
import os

from fastapi import Request

_DEFAULT_TRUSTED = ("127.0.0.1", "::1")


def _trusted_proxies() -> tuple:
    env = os.getenv("TRUSTED_PROXY_IPS", "").strip()
    if not env:
        return _DEFAULT_TRUSTED
    return tuple(p.strip() for p in env.split(",") if p.strip())


def _valid_ip(value: str) -> str:
    """Return the normalized IP if value parses as one, else ''."""
    value = (value or "").strip()
    if not value:
        return ""
    # Bracketed IPv6 ("[::1]") as some proxies format it.
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def is_trusted_proxy(request: Request) -> bool:
    """True when the direct socket peer is on the trusted-proxy allowlist."""
    peer = request.client.host if request.client else ""
    return peer in _trusted_proxies()


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if not is_trusted_proxy(request):
        # Direct connection (or untrusted proxy): never trust headers.
        return _valid_ip(peer) or peer

    cf = _valid_ip(request.headers.get("cf-connecting-ip", ""))
    if cf:
        return cf
    xff = _valid_ip(request.headers.get("x-forwarded-for", "").split(",")[0])
    if xff:
        return xff
    return peer
