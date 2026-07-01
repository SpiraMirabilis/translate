"""Shared fixtures for the T9 test suite.

All database tests run against a throwaway SQLite file inside pytest's
tmp_path — the real /home/mdm/t9/database.db, settings.json and .env are
never touched.
"""
import sys
import os

import pytest

# Make the project root importable regardless of how pytest is invoked.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class FakeLogger:
    """No-op logger duck-typing the project's Logger."""

    def info(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


class FakeConfig:
    """Duck-types TranslationConfig for DatabaseManager / db_backend.

    Attributes actually read on the DatabaseManager init + smoke-test path:
      - db_backend        (db_backend.create_backend)
      - script_dir        (create_backend sqlite path, _check_legacy_queue,
                           covers/illustrations dirs, epub cache dir)
      - translation_model (save_chapter default model)
      - trad_to_simp      (modules.trad_to_simp auto_enabled; getattr default
                           False, set explicitly for clarity)
      - spaces_enabled    (spaces.is_enabled inside invalidate_epub_cache)
      - debug             (simple default, defensive)
    """

    db_backend = "sqlite"
    translation_model = "test:model"
    trad_to_simp = False
    spaces_enabled = False
    debug = False

    def __init__(self, tmp_path):
        self.script_dir = str(tmp_path) + "/"


class FakeWebConfig(FakeConfig):
    """FakeConfig plus the attributes web.app.create_app (and the request
    paths exercised by the HTTP tests) read. Kept separate from FakeConfig
    so the pure-DB tests keep their minimal surface."""

    site_name = "T9 Test"
    public_site_name = "T9 Test Public"
    advice_model = "test:advice"
    debug_mode = False


# Password installed via T9_PASSWORD for the HTTP tests.
TEST_PASSWORD = "test-password-123"


@pytest.fixture
def fake_logger():
    return FakeLogger()


@pytest.fixture
def db(tmp_path):
    """DatabaseManager backed by a fresh SQLite DB in tmp_path."""
    from database import DatabaseManager

    return DatabaseManager(FakeConfig(tmp_path), FakeLogger())


@pytest.fixture
def web_app(tmp_path, monkeypatch):
    """FastAPI app from web.app.create_app wired to a tmp SQLite DB.

    Shares tmp_path with the `db` fixture, so chapters saved through `db`
    are visible to the app.

    Monkeypatching notes:
    - T9_PASSWORD / T9_PUBLIC_LIBRARY are set BEFORE create_app so
      configure_auth() picks them up (auth enabled, public library open).
    - ViewLogger.start is a no-op so no daemon flush thread leaks per test.
    - The first `import web.app` executes the module-bottom
      `app = create_app()`; config.TranslationConfig / logger.Logger are
      patched around that first import so it builds against this fixture's
      fakes instead of the production .env config (DB_BACKEND=mysql).
    """
    monkeypatch.setenv("T9_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("T9_PUBLIC_LIBRARY", "1")
    monkeypatch.delenv("T9_SECURE_COOKIE", raising=False)

    from web.services.view_logger import ViewLogger
    monkeypatch.setattr(ViewLogger, "start", lambda self: None)

    config = FakeWebConfig(tmp_path)
    logger = FakeLogger()

    if "web.app" not in sys.modules:
        import config as config_module
        import logger as logger_module
        monkeypatch.setattr(config_module, "TranslationConfig", lambda: config)
        monkeypatch.setattr(logger_module, "Logger", lambda cfg: logger)

    import web.app as app_module

    return app_module.create_app(config=config, logger=logger)


@pytest.fixture
def api_client(web_app):
    """Unauthenticated client against the test app."""
    from tests.api_client import SyncASGIClient

    return SyncASGIClient(web_app)


@pytest.fixture
def admin_client(web_app):
    """Client holding a valid admin session cookie (real login flow)."""
    from tests.api_client import SyncASGIClient

    login = SyncASGIClient(web_app).post(
        "/api/auth/login", json={"password": TEST_PASSWORD})
    assert login.status_code == 200, login.text
    token = login.cookies.get("t9_session")
    assert token, "login did not set the session cookie"
    return SyncASGIClient(web_app, headers={"Cookie": f"t9_session={token}"})
