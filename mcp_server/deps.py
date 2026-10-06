"""Process-wide state the tools share: the DatabaseManager, the admin client.

Held in a module-level holder rather than only in a lifespan context because
``FastMCP.call_tool()`` — what the tests drive — runs without a session. In
production the DB is built lazily on the first tool call, so the stdio
handshake never waits on migrations and the entity-cache load.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from mcp.server.fastmcp.exceptions import ToolError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ADMIN_URL = "http://127.0.0.1:8000"


class AdminUnreachable(ToolError):
    """The admin server could not be reached (or answered with a 5xx)."""


class AdminAuthError(ToolError):
    """The admin server rejected our session cookie."""


class AdminHTTPError(ToolError):
    """A 4xx the caller should see verbatim (e.g. 404 queue empty, 409 busy)."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Admin server returned {status_code}: {detail}")


class AdminClient:
    """The three admin endpoints the MCP server needs.

    Job status and queue control live only in the admin process's memory, so
    they cannot be read from the DB. Uses t9_client's locally minted session
    cookie — never POST /api/auth/login from here: login is rate-limited
    (5/min, 20/h) and pause_translation polls.
    """

    def __init__(self, url: str = DEFAULT_ADMIN_URL, password: Optional[str] = None,
                 timeout: float = 15):
        self.url = url
        self.password = password
        self.timeout = timeout

    def _request(self, method: str, path: str, json: Any = None) -> Any:
        import t9_client
        try:
            with t9_client.client(self.url, self.password, timeout=self.timeout) as c:
                r = c.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise AdminUnreachable(
                f"Could not reach the admin server at {self.url}: {exc}") from exc
        if r.status_code == 401:
            raise AdminAuthError(
                "Admin server rejected the session — is T9_PASSWORD in .env current?")
        if r.status_code >= 500:
            raise AdminUnreachable(
                f"Admin server error {r.status_code} on {path}: {r.text[:300]}")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise AdminHTTPError(r.status_code, str(detail))
        return r.json()

    def status(self) -> dict:
        return self._request("GET", "/api/translate/status")

    def stop_auto(self, book_id: int) -> dict:
        return self._request("POST", "/api/queue/stop-auto", json={"book_id": book_id})

    def process_next(self, payload: dict) -> dict:
        return self._request("POST", "/api/queue/process-next", json=payload)


@dataclass
class AppContext:
    db: Any = None
    admin: Any = None
    config: Any = None
    logger: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def ensure_db(self):
        """Build config/logger/DatabaseManager on first use (production path)."""
        if self.db is not None:
            return self.db
        with self._lock:
            if self.db is None:
                from config import TranslationConfig
                from db import DatabaseManager
                from logger import Logger

                cfg = self.config or TranslationConfig()
                log = self.logger or Logger(cfg)
                self.db = DatabaseManager(cfg, log, strict_writes=True)
                self.config, self.logger = cfg, log
        return self.db

    def ensure_config(self):
        """The real TranslationConfig — needed only by tools that call a model."""
        if self.config is None:
            self.ensure_db()
        if self.config is None:
            raise ToolError("No TranslationConfig available in this server.")
        return self.config


_app: Optional[AppContext] = None


def set_app(ctx: Optional[AppContext]) -> None:
    global _app
    _app = ctx


def app() -> AppContext:
    if _app is None:
        raise ToolError("T9 MCP server is not initialised.")
    return _app


def db():
    return app().ensure_db()
