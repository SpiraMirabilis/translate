"""Tests for the versioned migration runner (db/migrations.py)."""
import sqlite3

import pytest

from db.migrations import MIGRATIONS, run_migrations
from db_backend import SQLiteBackend


class CapturingLogger:
    def __init__(self):
        self.lines = []

    def info(self, msg, *a, **k):
        self.lines.append(str(msg))

    debug = warning = error = critical = info


@pytest.fixture
def backend(tmp_path):
    return SQLiteBackend(str(tmp_path / "mig.db"))


def _applied_versions(backend):
    conn = backend.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT version FROM schema_migrations ORDER BY version")
    out = [r[0] for r in cur.fetchall()]
    conn.close()
    return out


def _columns(backend, table):
    conn = backend.get_connection()
    cols = backend.get_table_columns(conn, table)
    conn.close()
    return cols


def test_fresh_db_applies_all_and_creates_schema(backend):
    log = CapturingLogger()
    run_migrations(backend, log)
    assert _applied_versions(backend) == [m.version for m in MIGRATIONS]
    assert {"origin_chapter", "note"} <= _columns(backend, "entities")
    assert "is_proofread" in _columns(backend, "chapters")
    assert {"cover_image", "view_count", "tags", "modules"} <= _columns(backend, "books")
    assert "retranslation_reason" in _columns(backend, "queue")
    assert {"wp_link", "wp_slug"} <= _columns(backend, "wp_publish_state")


def test_second_run_is_silent_noop(backend):
    run_migrations(backend, CapturingLogger())
    log2 = CapturingLogger()
    run_migrations(backend, log2)
    assert not any("Applied migration" in l for l in log2.lines)
    assert _applied_versions(backend) == [m.version for m in MIGRATIONS]


def test_legacy_db_sweep_no_data_loss(backend):
    """A pre-runner deployment: tables + columns exist, data present, but no
    schema_migrations table. The one-time sweep must not touch the data."""
    # Build the schema the way the app does today, then drop the version table
    run_migrations(backend, CapturingLogger())
    conn = backend.get_connection()
    cur = conn.cursor()
    cur.execute("DROP TABLE schema_migrations")
    cur.execute("INSERT INTO books (title, author) VALUES ('Legacy Book', 'A')")
    book_id = cur.lastrowid
    cur.execute(
        "INSERT INTO entities (category, untranslated, translation, last_chapter, book_id) "
        "VALUES ('characters', '张三', 'Zhang San', '7', ?)", (book_id,))
    conn.commit()
    conn.close()

    run_migrations(backend, CapturingLogger())

    conn = backend.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT title FROM books WHERE id = ?", (book_id,))
    assert cur.fetchone()[0] == "Legacy Book"
    # m002's backfill applies to the pre-existing entity (origin_chapter was NULL)
    cur.execute("SELECT origin_chapter FROM entities WHERE untranslated = '张三'")
    assert cur.fetchone()[0] == 7
    conn.close()
    assert _applied_versions(backend) == [m.version for m in MIGRATIONS]


def test_partially_stripped_legacy_db_gets_columns(backend):
    """Legacy DB missing newer columns: migrations add them and backfill."""
    run_migrations(backend, CapturingLogger())
    conn = backend.get_connection()
    cur = conn.cursor()
    cur.execute("DROP TABLE schema_migrations")
    # Simulate an old schema: recreate books without tags/modules/view_count
    cur.execute("ALTER TABLE books DROP COLUMN tags")
    cur.execute("ALTER TABLE books DROP COLUMN modules")
    cur.execute("ALTER TABLE books DROP COLUMN view_count")
    cur.execute("INSERT INTO books (title) VALUES ('Old Book')")
    book_id = cur.lastrowid
    cur.execute(
        "INSERT INTO reader_log (book_id, chapter_number, ip, viewed_at) "
        "VALUES (?, 1, '1.1.1.1', '2026-01-01T00:00:00')", (book_id,))
    conn.commit()
    conn.close()

    log = CapturingLogger()
    run_migrations(backend, log)

    assert {"tags", "modules", "view_count"} <= _columns(backend, "books")
    conn = backend.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT view_count FROM books WHERE id = ?", (book_id,))
    assert cur.fetchone()[0] == 1  # backfilled from reader_log
    conn.close()


def test_versions_are_unique_and_ordered():
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
