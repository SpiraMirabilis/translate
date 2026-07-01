"""
Shared guards for the public (unauthenticated) API surface.

Consolidates what were three per-module copies of the same machinery in
web/api/public.py, comments_public.py and recommendations_public.py:

- sliding-window rate limiting, now with stale-key eviction (the old
  copies grew their per-IP dicts without bound for the process lifetime)
- the Origin/Referer speed-bump check
- the TTL'd "human verified" cache used to skip repeat Turnstile prompts

None of this is a security boundary — it blocks casual scripted abuse
while staying transparent to normal visitors.
"""
import threading
import time

from fastapi import HTTPException, Request

from web.services.ip import client_ip


class SlidingWindowLimiter:
    """Per-key sliding-window rate limiter.

    check() raises HTTPException(429) when `key` has made `limit` calls in
    the last `window_seconds`. Stale keys are evicted every `prune_every`
    checks (and whenever the table grows past 50k keys) so the table stays
    bounded by recent traffic instead of process lifetime.
    """

    def __init__(self, window_seconds: int, limit: int,
                 detail: str = "Too many requests", prune_every: int = 1000):
        self.window = window_seconds
        self.limit = limit
        self.detail = detail
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._prune_every = prune_every
        self._checks = 0

    def check(self, key: str) -> None:
        now = time.time()
        cutoff = now - self.window
        with self._lock:
            self._checks += 1
            if self._checks % self._prune_every == 0 or len(self._hits) > 50_000:
                self._prune_locked(cutoff)
            bucket = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(bucket) >= self.limit:
                self._hits[key] = bucket
                raise HTTPException(status_code=429, detail=self.detail)
            bucket.append(now)
            self._hits[key] = bucket

    def _prune_locked(self, cutoff: float) -> None:
        stale = [k for k, bucket in self._hits.items()
                 if not bucket or bucket[-1] <= cutoff]
        for k in stale:
            del self._hits[k]

    def reset(self) -> None:
        """Clear all state (test hook)."""
        with self._lock:
            self._hits.clear()
            self._checks = 0


class TTLSet:
    """Set membership that expires after `ttl_seconds`. Falsy keys are never members."""

    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._expiry: dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, key) -> None:
        if not key:
            return
        with self._lock:
            self._expiry[key] = time.time() + self.ttl

    def __contains__(self, key) -> bool:
        if not key:
            return False
        now = time.time()
        with self._lock:
            if self._expiry.get(key, 0) > now:
                return True
            self._expiry.pop(key, None)
            if len(self._expiry) > 10_000:
                stale = [k for k, exp in self._expiry.items() if exp <= now]
                for k in stale:
                    del self._expiry[k]
            return False


def origin_check(request: Request) -> None:
    """
    Reject requests that don't originate from a browser viewing our site.
    This blocks casual scripted access while remaining transparent to
    normal page visitors.  Not a security boundary — just a speed bump.
    """
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    host = request.headers.get("host", "")

    # Build set of acceptable origins from the Host header
    allowed = set()
    if host:
        allowed.add(f"http://{host}")
        allowed.add(f"https://{host}")
    # Dev origins
    allowed.add("http://localhost:5173")
    allowed.add("http://127.0.0.1:5173")
    allowed.add("http://localhost:8000")
    allowed.add("http://127.0.0.1:8000")

    # Accept if either Origin or Referer matches
    if origin and any(origin.startswith(a) for a in allowed):
        return
    if referer and any(referer.startswith(a) for a in allowed):
        return

    # Also allow if neither header is present (direct browser navigation
    # to JSON endpoint — uncommon but harmless for read-only data)
    if not origin and not referer:
        return

    raise HTTPException(status_code=403, detail="Forbidden")


def guard(request: Request, limiter: SlidingWindowLimiter) -> None:
    """Standard public-endpoint guard: rate limit by client IP, then origin check."""
    limiter.check(client_ip(request))
    origin_check(request)
