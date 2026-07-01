"""Minimal sync HTTP client for API tests, built on httpx.ASGITransport.

starlette 0.31.1's TestClient passes ``app=`` to httpx.Client, which
httpx 0.28.1 removed, so the stock TestClient raises TypeError here.
This wrapper drives the ASGI app directly through httpx instead.

Notes:
- ASGITransport does NOT run lifespan (startup/shutdown) events.
- Each request runs in its own asyncio.run(); fine for sync-def handler
  routes (FastAPI executes them in a threadpool via anyio).
"""
import asyncio

import httpx


class SyncASGIClient:
    def __init__(self, app, base_url="http://testserver", headers=None):
        self._transport = httpx.ASGITransport(app=app)
        self._base_url = base_url
        self._headers = headers or {}

    def request(self, method, url, **kw):
        async def _go():
            async with httpx.AsyncClient(
                transport=self._transport,
                base_url=self._base_url,
                headers=self._headers,
            ) as c:
                return await c.request(method, url, **kw)
        return asyncio.run(_go())

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)
