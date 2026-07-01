"""
Versioned schema migrations.

Replaces the inline column-existence probes that DatabaseManager ran on every
startup: each migration runs exactly once per database, recorded in the
schema_migrations table.

Compatibility matrix (all must work):
  * fresh install            — no tables: migration 1 creates the baseline
    schema, 2..N find their columns present (baseline DDL is current) and
    no-op, then all are recorded.
  * legacy deployment        — all tables/columns exist but no
    schema_migrations table: every migration runs once; each is internally
    guarded (add_column_if_missing / WHERE ... IS NULL backfills) so the
    sweep is a no-op, then versions are recorded and never run again.
  * already-versioned DB     — versions recorded: nothing runs.

Each migration's `apply(conn, cursor, backend, logger)` must be idempotent.
"""
import datetime
from dataclasses import dataclass
from typing import Callable

_SCHEMA_MIGRATIONS_DDL = {
    "sqlite": """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """,
    "mysql": """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INT PRIMARY KEY,
            name       VARCHAR(255) NOT NULL,
            applied_at DATETIME NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
}


@dataclass
class Migration:
    version: int
    name: str
    apply: Callable  # (conn, cursor, backend, logger) -> None


def add_column_if_missing(conn, cursor, backend, table, column, ddl_sqlite, ddl_mysql=None):
    """ALTER TABLE ... ADD COLUMN guarded by a column-existence probe.

    Returns True if the column was added (lets callers gate one-time backfills
    on fresh column creation).
    """
    if column in backend.get_table_columns(conn, table):
        return False
    cursor.execute(ddl_mysql if (backend.name == "mysql" and ddl_mysql) else ddl_sqlite)
    return True


# ---------------------------------------------------------------------------
# Migrations (absorbed verbatim from DatabaseManager._initialize_database)
# ---------------------------------------------------------------------------

def _m001_baseline(conn, cursor, backend, logger):
    """Create all tables from the backend's baseline DDL (IF NOT EXISTS)."""
    for ddl in backend.create_tables_ddl():
        try:
            cursor.execute(ddl)
        except Exception:
            # Index may already exist (MySQL raises on IF NOT EXISTS for some
            # index forms)
            pass


def _m002_entities_origin_chapter(conn, cursor, backend, logger):
    add_column_if_missing(conn, cursor, backend, "entities", "origin_chapter",
                          "ALTER TABLE entities ADD COLUMN origin_chapter INTEGER")
    # Backfill: set origin_chapter = last_chapter for entities missing it.
    # Only copy values that are actually numeric (SQLite allows text in
    # INTEGER columns, MySQL doesn't).
    if backend.name == "mysql":
        cursor.execute(
            "UPDATE entities SET origin_chapter = CAST(last_chapter AS SIGNED) "
            "WHERE origin_chapter IS NULL AND last_chapter IS NOT NULL "
            "AND last_chapter REGEXP '^[0-9]+$'")
    else:
        cursor.execute(
            "UPDATE entities SET origin_chapter = last_chapter "
            "WHERE origin_chapter IS NULL AND last_chapter IS NOT NULL")
    if cursor.rowcount > 0:
        logger.info(f"Backfilled origin_chapter for {cursor.rowcount} entities")


def _m003_entities_note(conn, cursor, backend, logger):
    add_column_if_missing(conn, cursor, backend, "entities", "note",
                          "ALTER TABLE entities ADD COLUMN note TEXT")


def _m004_chapters_is_proofread(conn, cursor, backend, logger):
    added = add_column_if_missing(
        conn, cursor, backend, "chapters", "is_proofread",
        "ALTER TABLE chapters ADD COLUMN is_proofread TEXT",
        "ALTER TABLE chapters ADD COLUMN is_proofread DATETIME NULL")
    if added:
        return
    # Column pre-exists: migrate legacy INTEGER (0/1) values to timestamps.
    if backend.name == "mysql":
        cursor.execute(
            "SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chapters' "
            "AND COLUMN_NAME = 'is_proofread'")
        row = cursor.fetchone()
        if row and row[0] == "int":
            now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("ALTER TABLE chapters ADD COLUMN is_proofread_new DATETIME NULL")
            cursor.execute("UPDATE chapters SET is_proofread_new = ? WHERE is_proofread = 1", (now,))
            cursor.execute("ALTER TABLE chapters DROP COLUMN is_proofread")
            cursor.execute("ALTER TABLE chapters CHANGE COLUMN is_proofread_new is_proofread DATETIME NULL")
            logger.info("Migrated is_proofread from INT to DATETIME (MySQL)")
    else:
        cursor.execute("SELECT COUNT(*) FROM chapters WHERE is_proofread = '1' OR is_proofread = '0'")
        if cursor.fetchone()[0] > 0:
            now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            cursor.execute("UPDATE chapters SET is_proofread = ? WHERE is_proofread = '1'", (now,))
            cursor.execute("UPDATE chapters SET is_proofread = NULL WHERE is_proofread = '0'")
            logger.info("Migrated is_proofread from boolean to timestamp")


def _m005_books_columns(conn, cursor, backend, logger):
    for column, ddl in [
        ("cover_image", "ALTER TABLE books ADD COLUMN cover_image TEXT"),
        ("categories", "ALTER TABLE books ADD COLUMN categories TEXT"),
        ("is_public", "ALTER TABLE books ADD COLUMN is_public INTEGER DEFAULT 1"),
        ("total_source_chapters", "ALTER TABLE books ADD COLUMN total_source_chapters INTEGER"),
        ("status", "ALTER TABLE books ADD COLUMN status TEXT DEFAULT 'ongoing'"),
        ("comments_enabled", "ALTER TABLE books ADD COLUMN comments_enabled INTEGER DEFAULT 1"),
        ("source_url", "ALTER TABLE books ADD COLUMN source_url TEXT"),
        ("notes", "ALTER TABLE books ADD COLUMN notes TEXT"),
        ("trad_to_simp", "ALTER TABLE books ADD COLUMN trad_to_simp INTEGER DEFAULT NULL"),
        ("tags", "ALTER TABLE books ADD COLUMN tags TEXT"),
        ("modules", "ALTER TABLE books ADD COLUMN modules TEXT"),
    ]:
        if add_column_if_missing(conn, cursor, backend, "books", column, ddl):
            logger.info(f"Added {column} column to books table")


def _m006_books_view_count(conn, cursor, backend, logger):
    if add_column_if_missing(
            conn, cursor, backend, "books", "view_count",
            "ALTER TABLE books ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0"):
        cursor.execute("""
            UPDATE books
            SET view_count = COALESCE(
                (SELECT COUNT(*) FROM reader_log WHERE reader_log.book_id = books.id),
                0
            )
        """)
        logger.info("Backfilled view_count from reader_log")


def _m007_comments_notify_replies(conn, cursor, backend, logger):
    add_column_if_missing(conn, cursor, backend, "comments", "notify_replies",
                          "ALTER TABLE comments ADD COLUMN notify_replies INTEGER NOT NULL DEFAULT 0")


def _m008_queue_retranslation_reason(conn, cursor, backend, logger):
    add_column_if_missing(conn, cursor, backend, "queue", "retranslation_reason",
                          "ALTER TABLE queue ADD COLUMN retranslation_reason TEXT")


def _m009_wp_state_link_slug(conn, cursor, backend, logger):
    add_column_if_missing(conn, cursor, backend, "wp_publish_state", "wp_link",
                          "ALTER TABLE wp_publish_state ADD COLUMN wp_link TEXT")
    add_column_if_missing(conn, cursor, backend, "wp_publish_state", "wp_slug",
                          "ALTER TABLE wp_publish_state ADD COLUMN wp_slug TEXT")


MIGRATIONS = [
    Migration(1, "baseline_schema", _m001_baseline),
    Migration(2, "entities_origin_chapter", _m002_entities_origin_chapter),
    Migration(3, "entities_note", _m003_entities_note),
    Migration(4, "chapters_is_proofread_timestamp", _m004_chapters_is_proofread),
    Migration(5, "books_columns", _m005_books_columns),
    Migration(6, "books_view_count_backfill", _m006_books_view_count),
    Migration(7, "comments_notify_replies", _m007_comments_notify_replies),
    Migration(8, "queue_retranslation_reason", _m008_queue_retranslation_reason),
    Migration(9, "wp_state_link_slug", _m009_wp_state_link_slug),
]


def run_migrations(backend, logger):
    """Apply all unapplied migrations, committing after each one."""
    conn = backend.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(_SCHEMA_MIGRATIONS_DDL[backend.name])
        conn.commit()

        cursor.execute("SELECT version FROM schema_migrations")
        applied = {row[0] for row in cursor.fetchall()}

        for mig in MIGRATIONS:
            if mig.version in applied:
                continue
            mig.apply(conn, cursor, backend, logger)
            cursor.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (mig.version, mig.name,
                 datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            logger.info(f"Applied migration {mig.version}: {mig.name}")
    finally:
        conn.close()
