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


@pytest.fixture
def fake_logger():
    return FakeLogger()


@pytest.fixture
def db(tmp_path):
    """DatabaseManager backed by a fresh SQLite DB in tmp_path."""
    from database import DatabaseManager

    return DatabaseManager(FakeConfig(tmp_path), FakeLogger())
