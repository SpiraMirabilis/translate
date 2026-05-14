"""
Real-client IP resolution behind Cloudflare.

Prefers CF-Connecting-IP (set by Cloudflare's edge for every proxied
request), then falls back to X-Forwarded-For, then the direct socket
peer.

SECURITY: only trust CF-Connecting-IP if the deployment guarantees
all traffic enters through Cloudflare. The t9 service binds uvicorn
to localhost (web/app.py), so this is satisfied as long as Apache
(reverse proxy) only accepts CF-tagged requests. If the origin ever
becomes directly reachable, this header becomes spoofable.
"""

from fastapi import Request


def client_ip(request: Request) -> str:
    cf = request.headers.get("cf-connecting-ip", "").strip()
    if cf:
        return cf
    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if xff:
        return xff
    return request.client.host if request.client else ""
