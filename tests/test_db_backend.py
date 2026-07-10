"""Behavior lock-in tests for db_backend.py (no real MySQL needed)."""
import os

import db_backend
from db_backend import (
    MySQLBackend,
    SQLiteBackend,
    _MySQLCursorWrapper,
    _MySQLDictCursorWrapper,
    create_backend,
)


class StubCursor:
    """Records execute/executemany calls; fakes fetch results."""

    def __init__(self, rows=None, description=None):
        self.calls = []
        self._rows = rows or []
        self.description = description

    def execute(self, sql, params=None):
        self.calls.append(("execute", sql, params))

    def executemany(self, sql, seq_of_params):
        self.calls.append(("executemany", sql, seq_of_params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


# ── ? → %s placeholder translation ─────────────────────────────────


def test_cursor_wrapper_translates_placeholders():
    stub = StubCursor()
    cur = _MySQLCursorWrapper(stub)
    cur.execute("SELECT * FROM books WHERE id = ? AND title = ?", (1, "t"))
    assert stub.calls == [
        ("execute", "SELECT * FROM books WHERE id = %s AND title = %s",
         (1, "t")),
    ]


def test_cursor_wrapper_translates_executemany():
    stub = StubCursor()
    cur = _MySQLCursorWrapper(stub)
    cur.executemany("INSERT INTO t (a) VALUES (?)", [(1,), (2,)])
    assert stub.calls == [
        ("executemany", "INSERT INTO t (a) VALUES (%s)", [(1,), (2,)]),
    ]


def test_cursor_wrapper_no_placeholders_untouched():
    stub = StubCursor()
    _MySQLCursorWrapper(stub).execute("SELECT 1")
    assert stub.calls == [("execute", "SELECT 1", None)]


def test_cursor_wrapper_preserves_literal_question_marks():
    # The translation skips quoted regions, so a literal '?' inside a string
    # constant survives while real placeholders outside quotes are rewritten.
    stub = StubCursor()
    _MySQLCursorWrapper(stub).execute("SELECT 'what?' WHERE a = ? AND b = 'x''?' AND c = ?")
    assert stub.calls == [
        ("execute", "SELECT 'what?' WHERE a = %s AND b = 'x''?' AND c = %s", None)
    ]


def test_dict_cursor_wrapper_returns_dict_rows():
    stub = StubCursor(
        rows=[(1, "Book A"), (2, "Book B")],
        description=[("id",), ("title",)],
    )
    cur = _MySQLDictCursorWrapper(stub)
    assert cur.fetchone() == {"id": 1, "title": "Book A"}
    assert cur.fetchall() == [
        {"id": 1, "title": "Book A"},
        {"id": 2, "title": "Book B"},
    ]


def test_dict_cursor_wrapper_none_and_empty():
    stub = StubCursor(rows=[], description=[("id",)])
    cur = _MySQLDictCursorWrapper(stub)
    assert cur.fetchone() is None
    assert cur.fetchall() == []


# ── create_backend ─────────────────────────────────────────────────


class _SqliteConfig:
    db_backend = "sqlite"

    def __init__(self, tmp_path):
        self.script_dir = str(tmp_path) + "/"


def test_create_backend_sqlite(tmp_path):
    backend = create_backend(_SqliteConfig(tmp_path))
    assert isinstance(backend, SQLiteBackend)
    assert backend.name == "sqlite"
    assert backend.db_path == os.path.join(str(tmp_path) + "/", "database.db")


def test_create_backend_sqlite_case_insensitive(tmp_path):
    cfg = _SqliteConfig(tmp_path)
    cfg.db_backend = "  SQLite "
    assert isinstance(create_backend(cfg), SQLiteBackend)


def test_create_backend_unknown_falls_back_to_sqlite(tmp_path):
    cfg = _SqliteConfig(tmp_path)
    cfg.db_backend = "postgres"
    assert isinstance(create_backend(cfg), SQLiteBackend)


# ── dialect helper SQL ─────────────────────────────────────────────


def _mysql_backend_no_connect():
    """MySQLBackend instance without __init__ (avoids requiring a server;
    the dialect helpers don't touch connection state)."""
    return MySQLBackend.__new__(MySQLBackend)


# upsert_entity_sql was removed: its ON CONFLICT(book_id, untranslated) clause
# never fired for NULL book_id (NULL never conflicts on either backend), so the
# lone caller (import_from_json) duplicated the global entity set on every
# re-import. That caller now does an explicit update-else-insert.


def test_upsert_token_ratio_sql_dialects():
    lite = SQLiteBackend("ignored.db").upsert_token_ratio_sql()
    my = _mysql_backend_no_connect().upsert_token_ratio_sql()
    assert "INSERT INTO token_ratios" in lite
    assert "ON CONFLICT(book_id) DO UPDATE SET" in lite
    assert "ON DUPLICATE KEY UPDATE" in my
    assert "sample_count = sample_count + 1" in lite
    assert "sample_count = sample_count + 1" in my


def test_upsert_wp_state_sql_dialects():
    lite = SQLiteBackend("ignored.db").upsert_wp_state_sql()
    my = _mysql_backend_no_connect().upsert_wp_state_sql()
    assert "INSERT INTO wp_publish_state" in lite
    assert "ON CONFLICT(book_id, chapter_number) DO UPDATE SET" in lite
    assert "ON DUPLICATE KEY UPDATE" in my


def test_cap_activity_log_sql_dialects():
    lite = SQLiteBackend("ignored.db").cap_activity_log_sql()
    my = _mysql_backend_no_connect().cap_activity_log_sql()
    assert lite.startswith("DELETE FROM activity_log")
    assert my.startswith("DELETE FROM activity_log")
    # MySQL needs the derived-table workaround for LIMIT-in-subquery.
    assert "AS keep_ids" in my


def test_create_tables_ddl_nonempty_both_dialects():
    assert len(SQLiteBackend("ignored.db").create_tables_ddl()) > 0
    assert len(_mysql_backend_no_connect().create_tables_ddl()) > 0
    assert db_backend._COMMON_DDL_SQLITE is not db_backend._COMMON_DDL_MYSQL
