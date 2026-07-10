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
        except Exception as e:
            # MySQL raises on CREATE INDEX for an index that already exists
            # (no IF NOT EXISTS form pre-8.0.29) — tolerate exactly that;
            # any other DDL failure is real and must surface.
            msg = str(e).lower()
            if "duplicate key name" in msg or "already exists" in msg:
                continue
            logger.error(f"Baseline DDL failed: {e}\nStatement: {ddl.strip()[:200]}")
            raise


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
        # Same numeric guard as MySQL — SQLite happily stores text in an
        # INTEGER column, so an unguarded copy propagated junk values.
        cursor.execute(
            "UPDATE entities SET origin_chapter = CAST(last_chapter AS INTEGER) "
            "WHERE origin_chapter IS NULL AND last_chapter IS NOT NULL "
            "AND last_chapter GLOB '[0-9]*' AND NOT last_chapter GLOB '*[^0-9]*'")
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


_CHAPTER_REVISIONS_DDL = {
    "sqlite": """
        CREATE TABLE IF NOT EXISTS chapter_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            chapter_number INTEGER NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            word_count INTEGER,
            kind TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        )
    """,
    "mysql": """
        CREATE TABLE IF NOT EXISTS chapter_revisions (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            book_id INTEGER NOT NULL,
            chapter_number INTEGER NOT NULL,
            title VARCHAR(500),
            content LONGTEXT NOT NULL,
            word_count INTEGER,
            kind VARCHAR(10) NOT NULL DEFAULT 'manual',
            created_at VARCHAR(50) NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
}


def _m010_original_works(conn, cursor, backend, logger):
    if add_column_if_missing(conn, cursor, backend, "books", "is_original",
                             "ALTER TABLE books ADD COLUMN is_original INTEGER NOT NULL DEFAULT 0"):
        logger.info("Added is_original column to books table")
    cursor.execute(_CHAPTER_REVISIONS_DDL[backend.name])
    try:
        cursor.execute("CREATE INDEX idx_chapter_revisions_chapter "
                       "ON chapter_revisions(book_id, chapter_number)")
    except Exception:
        # Index already exists (fresh installs create it via baseline DDL)
        pass


def _m011_chapter_publishing(conn, cursor, backend, logger):
    """Per-chapter publish state: published_at NULL = draft, future =
    scheduled, past = publicly visible. Everything that existed before this
    feature was already live, so backfill to the translation date."""
    if add_column_if_missing(
            conn, cursor, backend, "chapters", "published_at",
            "ALTER TABLE chapters ADD COLUMN published_at TEXT",
            "ALTER TABLE chapters ADD COLUMN published_at VARCHAR(50)"):
        now = datetime.datetime.now().isoformat()
        cursor.execute(
            "UPDATE chapters SET published_at = COALESCE(translation_date, ?) "
            "WHERE published_at IS NULL", (now,))
        logger.info(f"Backfilled published_at for {cursor.rowcount} chapters")


_POLISH_JOBS_DDL = {
    "sqlite": ["""
        CREATE TABLE IF NOT EXISTS polish_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER,
            chapter_number INTEGER,
            status TEXT NOT NULL DEFAULT 'running',
            model TEXT,
            text_chars INTEGER,
            truncated INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        )
    """, """
        CREATE TABLE IF NOT EXISTS polish_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            ord INTEGER NOT NULL DEFAULT 0,
            find_text TEXT NOT NULL,
            replace_text TEXT NOT NULL,
            reason TEXT,
            occurrences INTEGER,
            status TEXT NOT NULL DEFAULT 'open',
            resolved_at TEXT,
            FOREIGN KEY(job_id) REFERENCES polish_jobs(id) ON DELETE CASCADE
        )
    """],
    "mysql": ["""
        CREATE TABLE IF NOT EXISTS polish_jobs (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            book_id INTEGER,
            chapter_number INTEGER,
            status VARCHAR(16) NOT NULL DEFAULT 'running',
            model VARCHAR(100),
            text_chars INTEGER,
            truncated TINYINT NOT NULL DEFAULT 0,
            error TEXT,
            created_at VARCHAR(50) NOT NULL,
            finished_at VARCHAR(50),
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, """
        CREATE TABLE IF NOT EXISTS polish_suggestions (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            job_id INTEGER NOT NULL,
            ord INTEGER NOT NULL DEFAULT 0,
            find_text TEXT NOT NULL,
            replace_text TEXT NOT NULL,
            reason TEXT,
            occurrences INTEGER,
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            resolved_at VARCHAR(50),
            FOREIGN KEY(job_id) REFERENCES polish_jobs(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """],
}


def _m012_polish_jobs(conn, cursor, backend, logger):
    """Persisted LLM-polish jobs + per-suggestion resolution state for the
    write editor (results survive navigation and restarts)."""
    for ddl in _POLISH_JOBS_DDL[backend.name]:
        cursor.execute(ddl)
    for index_ddl in (
        "CREATE INDEX idx_polish_jobs_chapter ON polish_jobs(book_id, chapter_number)",
        "CREATE INDEX idx_polish_suggestions_job ON polish_suggestions(job_id)",
    ):
        try:
            cursor.execute(index_ddl)
        except Exception:
            # Index already exists (fresh installs create it via baseline DDL)
            pass


_RECOMMENDATION_REPLIES_DDL = {
    "sqlite": """
        CREATE TABLE IF NOT EXISTS recommendation_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER,
            from_email TEXT,
            from_name TEXT,
            subject TEXT,
            body TEXT NOT NULL,
            message_id TEXT,
            in_reply_to TEXT,
            correlation TEXT NOT NULL DEFAULT 'unmatched',
            received_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(recommendation_id) REFERENCES recommendations(id) ON DELETE CASCADE
        )
    """,
    "mysql": """
        CREATE TABLE IF NOT EXISTS recommendation_replies (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            recommendation_id INTEGER,
            from_email VARCHAR(255),
            from_name VARCHAR(255),
            subject TEXT,
            body LONGTEXT NOT NULL,
            message_id VARCHAR(255),
            in_reply_to VARCHAR(255),
            correlation VARCHAR(20) NOT NULL DEFAULT 'unmatched',
            received_at VARCHAR(50) NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            UNIQUE KEY uq_rec_replies_msgid (message_id),
            KEY idx_rec_replies_rec (recommendation_id),
            FOREIGN KEY(recommendation_id) REFERENCES recommendations(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
}


def _m013_recommendation_replies(conn, cursor, backend, logger):
    """Ingested email replies from translation-request requesters (surfaced in
    the Recommendations admin page by the mail-monitor daemon)."""
    cursor.execute(_RECOMMENDATION_REPLIES_DDL[backend.name])
    # SQLite needs its indexes created separately; MySQL declares them inline.
    if backend.name == "sqlite":
        for index_ddl in (
            "CREATE INDEX IF NOT EXISTS idx_rec_replies_rec ON recommendation_replies(recommendation_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_rec_replies_msgid ON recommendation_replies(message_id)",
        ):
            cursor.execute(index_ddl)


def _m014_chapters_translation_date_index(conn, cursor, backend, logger):
    """Index for the RSS/recent-chapters query, which orders by
    translation_date and previously full-scanned the chapters table."""
    if backend.name == "mysql":
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chapters' "
            "AND INDEX_NAME = 'idx_chapters_translation_date'")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "CREATE INDEX idx_chapters_translation_date "
                "ON chapters(translation_date)")
    else:
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_chapters_translation_date "
            "ON chapters(translation_date)")


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
    Migration(10, "original_works", _m010_original_works),
    Migration(11, "chapter_publishing", _m011_chapter_publishing),
    Migration(12, "polish_jobs", _m012_polish_jobs),
    Migration(13, "recommendation_replies", _m013_recommendation_replies),
    Migration(14, "chapters_translation_date_index", _m014_chapters_translation_date_index),
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
