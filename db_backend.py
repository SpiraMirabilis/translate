"""
Database backend abstraction layer.

Provides SQLiteBackend and MySQLBackend implementations behind a common
interface so the rest of the application can work with either database
without caring about dialect differences.

MySQL is an optional dependency — importing this module never fails.
The MySQLBackend is only instantiated (and mysql.connector imported)
when DB_BACKEND=mysql is configured.
"""

import os
import sqlite3
import threading
import time


# ---------------------------------------------------------------------------
# Placeholder-translating cursor wrapper (MySQL)
# ---------------------------------------------------------------------------

class _MySQLCursorWrapper:
    """Wraps a mysql.connector cursor so callers can use '?' placeholders."""

    def __init__(self, real_cursor):
        self._cursor = real_cursor

    # Translate ? → %s in the SQL string.  This is safe because
    # properly parameterised SQL never contains literal '?' — values
    # are always passed via the params tuple.
    def execute(self, sql, params=None):
        sql = sql.replace('?', '%s')
        return self._cursor.execute(sql, params)

    def executemany(self, sql, seq_of_params):
        sql = sql.replace('?', '%s')
        return self._cursor.executemany(sql, seq_of_params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        if size is None:
            return self._cursor.fetchmany()
        return self._cursor.fetchmany(size)

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        return self._cursor.close()

    def __iter__(self):
        return iter(self._cursor)


class _MySQLDictCursorWrapper(_MySQLCursorWrapper):
    """Same as _MySQLCursorWrapper but returns dict rows (like sqlite3.Row)."""

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._cursor.description]
        return dict(zip(cols, row))

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return rows
        cols = [d[0] for d in self._cursor.description]
        return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Connection wrappers
# ---------------------------------------------------------------------------

class _MySQLConnectionWrapper:
    """
    Wraps a mysql.connector connection so it looks like a sqlite3 connection
    to the rest of the codebase: cursor() returns placeholder-translating
    wrappers, commit/close/execute work as expected.
    """

    def __init__(self, real_conn):
        self._conn = real_conn
        self._use_dict_cursor = False

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, value):
        # When callers set row_factory = sqlite3.Row, switch to dict cursors
        if value is not None:
            self._use_dict_cursor = True
        else:
            self._use_dict_cursor = False

    def cursor(self):
        # buffered=True: the driver drains every result set immediately, so a
        # caller that doesn't fetch all rows (or fetches none, like the health
        # probe) can't leave "unread result" state on the connection. Harmless
        # for direct connections; ESSENTIAL for pooled ones, which get reused —
        # an undrained result poisons the next checkout ("Unread result found").
        if self._use_dict_cursor:
            return _MySQLDictCursorWrapper(self._conn.cursor(buffered=True))
        return _MySQLCursorWrapper(self._conn.cursor(buffered=True))

    def dict_cursor(self):
        """Return a cursor whose fetch* methods return dicts."""
        return _MySQLDictCursorWrapper(self._conn.cursor(buffered=True))

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def execute(self, sql, params=None):
        """Convenience — matches sqlite3.Connection.execute()."""
        cur = self.cursor()
        cur.execute(sql, params)
        return cur


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------

class SQLiteBackend:
    """SQLite database backend (default)."""

    name = 'sqlite'

    def __init__(self, db_path):
        self.db_path = db_path

    def get_connection(self):
        """Return a sqlite3 connection hardened for concurrent access.

        WAL lets readers proceed while the translation thread writes (and is
        a prerequisite for serving requests from a threadpool); busy_timeout
        makes writers wait out short lock contention instead of raising
        'database is locked' immediately. WAL persists in the DB file but
        setting it is idempotent.
        """
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        return conn

    # -- Dialect helpers ----------------------------------------------------

    def get_table_columns(self, conn, table_name):
        """Return a set of column names for *table_name*."""
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cursor.fetchall()}

    def enable_foreign_keys(self, conn):
        conn.execute("PRAGMA foreign_keys = ON")

    def upsert_entity_sql(self):
        return (
            "INSERT INTO entities (category, untranslated, translation, last_chapter, incorrect_translation, gender) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(book_id, untranslated) DO UPDATE SET "
            "category = excluded.category, translation = excluded.translation, "
            "last_chapter = excluded.last_chapter, incorrect_translation = excluded.incorrect_translation, "
            "gender = excluded.gender"
        )

    def upsert_token_ratio_sql(self):
        return (
            "INSERT INTO token_ratios (book_id, total_input_chars, total_output_tokens, sample_count) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(book_id) DO UPDATE SET "
            "total_input_chars = total_input_chars + excluded.total_input_chars, "
            "total_output_tokens = total_output_tokens + excluded.total_output_tokens, "
            "sample_count = sample_count + 1"
        )

    def upsert_wp_state_sql(self):
        return (
            "INSERT INTO wp_publish_state (book_id, chapter_number, wp_post_id, wp_post_type, last_published, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(book_id, chapter_number) DO UPDATE SET "
            "wp_post_id=excluded.wp_post_id, wp_post_type=excluded.wp_post_type, "
            "last_published=excluded.last_published, content_hash=excluded.content_hash"
        )

    def cap_activity_log_sql(self):
        return "DELETE FROM activity_log WHERE id NOT IN (SELECT id FROM activity_log ORDER BY id DESC LIMIT 500)"

    def create_tables_ddl(self):
        """Return list of DDL statements for SQLite."""
        return _COMMON_DDL_SQLITE


class MySQLBackend:
    """MySQL / MariaDB database backend (optional)."""

    name = 'mysql'

    def __init__(self, host, user, password, database, port=3306):
        # Defer import — never fails at module level
        try:
            import mysql.connector  # noqa: F811
        except ImportError:
            raise RuntimeError(
                "MySQL backend requires 'mysql-connector-python'.  "
                "Install it with:  pip install mysql-connector-python"
            )
        self._connect_args = dict(
            host=host,
            user=user,
            password=password,
            database=database,
            port=port,
            autocommit=False,
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci',
        )
        self.db_path = f"mysql://{user}@{host}:{port}/{database}"
        self._pool = None
        self._pool_disabled = False
        self._pool_lock = threading.Lock()

    def _get_pool(self):
        """Lazily create the connection pool (avoids a handshake per query)."""
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    from mysql.connector.pooling import MySQLConnectionPool
                    self._pool = MySQLConnectionPool(
                        pool_name="t9",
                        pool_size=int(os.getenv("MYSQL_POOL_SIZE", "10")),
                        pool_reset_session=True,
                        **self._connect_args,
                    )
        return self._pool

    def get_connection(self):
        """Return a wrapped pooled connection (.close() returns it to the pool).

        On pool exhaustion, retries briefly, then falls back to a one-off
        direct connection rather than failing the request.
        """
        import mysql.connector
        from mysql.connector.errors import PoolError

        if not self._pool_disabled:
            try:
                pool = self._get_pool()
            except Exception as e:
                # Pool construction failed (e.g. connector version quirk) —
                # fall back to direct connections permanently.
                print(f"[db_backend] MySQL pool unavailable, using direct connections: {e}")
                self._pool_disabled = True
            else:
                deadline = time.monotonic() + 5.0
                while True:
                    try:
                        return _MySQLConnectionWrapper(pool.get_connection())
                    except PoolError:
                        if time.monotonic() >= deadline:
                            print("[db_backend] MySQL pool exhausted for 5s — using one-off connection")
                            break
                        time.sleep(0.1)

        raw = mysql.connector.connect(**self._connect_args)
        return _MySQLConnectionWrapper(raw)

    # -- Dialect helpers ----------------------------------------------------

    def get_table_columns(self, conn, table_name):
        """Return a set of column names for *table_name*."""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?",
            (table_name,),
        )
        return {row[0] for row in cursor.fetchall()}

    def enable_foreign_keys(self, conn):
        # InnoDB has foreign keys on by default — nothing to do
        pass

    def upsert_entity_sql(self):
        return (
            "INSERT INTO entities (category, untranslated, translation, last_chapter, incorrect_translation, gender) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON DUPLICATE KEY UPDATE "
            "category = VALUES(category), translation = VALUES(translation), "
            "last_chapter = VALUES(last_chapter), incorrect_translation = VALUES(incorrect_translation), "
            "gender = VALUES(gender)"
        )

    def upsert_token_ratio_sql(self):
        return (
            "INSERT INTO token_ratios (book_id, total_input_chars, total_output_tokens, sample_count) "
            "VALUES (?, ?, ?, 1) "
            "ON DUPLICATE KEY UPDATE "
            "total_input_chars = total_input_chars + VALUES(total_input_chars), "
            "total_output_tokens = total_output_tokens + VALUES(total_output_tokens), "
            "sample_count = sample_count + 1"
        )

    def upsert_wp_state_sql(self):
        return (
            "INSERT INTO wp_publish_state (book_id, chapter_number, wp_post_id, wp_post_type, last_published, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON DUPLICATE KEY UPDATE "
            "wp_post_id=VALUES(wp_post_id), wp_post_type=VALUES(wp_post_type), "
            "last_published=VALUES(last_published), content_hash=VALUES(content_hash)"
        )

    def cap_activity_log_sql(self):
        # MySQL doesn't support LIMIT inside a subquery with NOT IN.
        # Use a derived-table workaround instead.
        return (
            "DELETE FROM activity_log WHERE id NOT IN "
            "(SELECT id FROM (SELECT id FROM activity_log ORDER BY id DESC LIMIT 500) AS keep_ids)"
        )

    def create_tables_ddl(self):
        """Return list of DDL statements for MySQL."""
        return _COMMON_DDL_MYSQL


# ---------------------------------------------------------------------------
# Table DDL
# ---------------------------------------------------------------------------

_COMMON_DDL_SQLITE = [
    # entities
    '''CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        untranslated TEXT NOT NULL,
        translation TEXT NOT NULL,
        last_chapter TEXT,
        incorrect_translation TEXT,
        gender TEXT,
        book_id INTEGER,
        UNIQUE(book_id, untranslated)
    )''',
    'CREATE INDEX IF NOT EXISTS idx_category ON entities(category)',
    'CREATE INDEX IF NOT EXISTS idx_untranslated ON entities(untranslated)',
    'CREATE INDEX IF NOT EXISTS idx_book_id ON entities(book_id)',

    # books
    '''CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        author TEXT,
        language TEXT DEFAULT 'en',
        description TEXT,
        created_date TEXT,
        modified_date TEXT,
        prompt_template TEXT,
        source_language TEXT DEFAULT 'zh',
        target_language TEXT DEFAULT 'en',
        source_url TEXT,
        notes TEXT,
        view_count INTEGER NOT NULL DEFAULT 0,
        trad_to_simp INTEGER DEFAULT NULL,
        tags TEXT,
        modules TEXT,
        is_original INTEGER NOT NULL DEFAULT 0,
        UNIQUE(title)
    )''',

    # chapters — published_at: NULL = draft, future = scheduled, past = live
    '''CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER NOT NULL,
        title TEXT NOT NULL,
        untranslated_content TEXT NOT NULL,
        translated_content TEXT NOT NULL,
        summary TEXT,
        translation_date TEXT,
        translation_model TEXT,
        published_at TEXT,
        UNIQUE(book_id, chapter_number),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    )''',
    'CREATE INDEX IF NOT EXISTS idx_chapters_book_id ON chapters(book_id)',
    'CREATE INDEX IF NOT EXISTS idx_chapter_number ON chapters(chapter_number)',

    # queue
    '''CREATE TABLE IF NOT EXISTS queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER,
        title TEXT NOT NULL,
        source TEXT,
        content TEXT NOT NULL,
        metadata TEXT,
        position INTEGER NOT NULL,
        created_date TEXT NOT NULL,
        retranslation_reason TEXT,
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    )''',
    'CREATE INDEX IF NOT EXISTS idx_queue_book_id ON queue(book_id)',
    'CREATE INDEX IF NOT EXISTS idx_queue_position ON queue(position)',
    'CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_position_unique ON queue(position)',
    'CREATE INDEX IF NOT EXISTS idx_queue_book_id_position ON queue(book_id, position)',

    # token_ratios
    '''CREATE TABLE IF NOT EXISTS token_ratios (
        book_id INTEGER PRIMARY KEY,
        total_input_chars INTEGER NOT NULL DEFAULT 0,
        total_output_tokens INTEGER NOT NULL DEFAULT 0,
        sample_count INTEGER NOT NULL DEFAULT 0
    )''',

    # activity_log
    '''CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        message TEXT NOT NULL,
        book_id INTEGER,
        chapter INTEGER,
        book_name TEXT,
        entities_json TEXT,
        created_at TEXT NOT NULL
    )''',

    # wp_publish_state
    '''CREATE TABLE IF NOT EXISTS wp_publish_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER,
        wp_post_id INTEGER NOT NULL,
        wp_post_type TEXT NOT NULL,
        last_published TEXT,
        content_hash TEXT,
        wp_link TEXT,
        wp_slug TEXT,
        UNIQUE(book_id, chapter_number),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    )''',

    # reader_log — tracks chapter views from the public reader
    '''CREATE TABLE IF NOT EXISTS reader_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER NOT NULL,
        ip TEXT NOT NULL,
        viewed_at TEXT NOT NULL,
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    )''',
    'CREATE INDEX IF NOT EXISTS idx_reader_log_book ON reader_log(book_id)',
    'CREATE INDEX IF NOT EXISTS idx_reader_log_viewed ON reader_log(viewed_at)',

    # api_calls — logs every LLM API call made during translation
    '''CREATE TABLE IF NOT EXISTS api_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        book_id INTEGER,
        chapter_number INTEGER,
        chunk_index INTEGER NOT NULL,
        total_chunks INTEGER NOT NULL,
        system_prompt TEXT,
        user_prompt TEXT,
        response_text TEXT,
        model_name TEXT,
        provider TEXT,
        prompt_tokens INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        duration_ms INTEGER DEFAULT 0,
        success INTEGER DEFAULT 1,
        attempt INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    )''',
    'CREATE INDEX IF NOT EXISTS idx_api_calls_book ON api_calls(book_id)',
    'CREATE INDEX IF NOT EXISTS idx_api_calls_session ON api_calls(session_id)',
    'CREATE INDEX IF NOT EXISTS idx_api_calls_chapter ON api_calls(book_id, chapter_number)',

    # recommendations — novel translation requests from public users
    '''CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        novel_title TEXT NOT NULL,
        author TEXT,
        source_url TEXT NOT NULL,
        source_language TEXT DEFAULT 'zh',
        description TEXT,
        requester_name TEXT NOT NULL,
        requester_email TEXT NOT NULL,
        notes TEXT,
        status TEXT DEFAULT 'new',
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        admin_notes TEXT
    )''',
    'CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations(status)',

    # comments — reader comment threads on chapters
    '''CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER NOT NULL,
        parent_id INTEGER,
        depth INTEGER NOT NULL DEFAULT 0,
        root_id INTEGER,
        commenter_uuid TEXT NOT NULL,
        display_name TEXT NOT NULL,
        email TEXT,
        body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        edited_at TEXT,
        deleted_at TEXT,
        automod_state TEXT,
        automod_reason TEXT,
        ip TEXT NOT NULL,
        user_agent TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
        FOREIGN KEY(parent_id) REFERENCES comments(id) ON DELETE SET NULL
    )''',
    'CREATE INDEX IF NOT EXISTS idx_comments_book_chapter ON comments(book_id, chapter_number)',
    'CREATE INDEX IF NOT EXISTS idx_comments_uuid ON comments(commenter_uuid)',
    'CREATE INDEX IF NOT EXISTS idx_comments_status ON comments(status)',
    'CREATE INDEX IF NOT EXISTS idx_comments_book_chap_status ON comments(book_id, chapter_number, status)',

    # commenters — trust + identity index
    '''CREATE TABLE IF NOT EXISTS commenters (
        uuid TEXT PRIMARY KEY,
        display_name TEXT,
        email TEXT,
        is_trusted INTEGER NOT NULL DEFAULT 0,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        comment_count INTEGER NOT NULL DEFAULT 0
    )''',
    'CREATE INDEX IF NOT EXISTS idx_commenters_email ON commenters(email)',

    # comment_bans — admin bans (uuid/email/ip); IP bans also pushed to Cloudflare edge
    '''CREATE TABLE IF NOT EXISTS comment_bans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        reason TEXT,
        cf_pushed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(kind, value)
    )''',
    'CREATE INDEX IF NOT EXISTS idx_bans_kind_value ON comment_bans(kind, value)',

    # email_suppressions — silent block list for unsubscribed / bounced addresses
    '''CREATE TABLE IF NOT EXISTS email_suppressions (
        email TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''',

    # email_notifications — idempotency log: one row per (reply, recipient).
    # Guarantees a single email per recipient per reply even if the dispatch
    # path fires multiple times (status flap, race between admin/automod, etc.)
    '''CREATE TABLE IF NOT EXISTS email_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        comment_id INTEGER NOT NULL,
        recipient_email TEXT NOT NULL,
        sent_at TEXT NOT NULL,
        UNIQUE(comment_id, recipient_email)
    )''',
    'CREATE INDEX IF NOT EXISTS idx_email_notif_comment ON email_notifications(comment_id)',

    # illustrations — in-chapter images extracted at import; the marker_id is
    # embedded inline in chapter content as ⟦IMG:<marker_id>⟧ and is the stable
    # link between text and file (see illustrations.py).
    '''CREATE TABLE IF NOT EXISTS illustrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        marker_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        alt TEXT,
        original_href TEXT,
        ordinal INTEGER,
        queue_id INTEGER,
        chapter_id INTEGER,
        created_date TEXT,
        UNIQUE(book_id, marker_id),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    )''',
    'CREATE INDEX IF NOT EXISTS idx_illustrations_book ON illustrations(book_id)',
    'CREATE INDEX IF NOT EXISTS idx_illustrations_chapter ON illustrations(chapter_id)',
    'CREATE INDEX IF NOT EXISTS idx_illustrations_queue ON illustrations(queue_id)',

    # footnotes — persistent store for chapter footnotes so they survive
    # retranslation. The body lives here; the inline "[n]" marker + definition
    # block in chapters.translated_content is a derived rendering re-applied on
    # every save by anchor (the English term the marker hugs). See footnotes.py.
    '''CREATE TABLE IF NOT EXISTS footnotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        chapter_id INTEGER NOT NULL,
        anchor TEXT NOT NULL,
        source_term TEXT,
        body TEXT NOT NULL,
        occurrence INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'active',
        is_source INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER,
        created_date TEXT,
        modified_date TEXT,
        UNIQUE(chapter_id, is_source, anchor, occurrence),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
        FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
    )''',
    'CREATE INDEX IF NOT EXISTS idx_footnotes_book ON footnotes(book_id)',
    'CREATE INDEX IF NOT EXISTS idx_footnotes_chapter ON footnotes(chapter_id)',
    'CREATE INDEX IF NOT EXISTS idx_footnotes_status ON footnotes(status)',

    # book_module_settings — per-book, per-module structured settings. A module
    # declares a settings_schema (see modules/base.py); values are JSON-encoded so
    # one column carries bool/text/object alike. set_module_settings replaces all
    # rows for a (book, module) authoritatively. (Column is 'setting_key', not
    # 'key', because 'key' is a reserved word in MySQL.)
    '''CREATE TABLE IF NOT EXISTS book_module_settings (
        book_id INTEGER NOT NULL,
        module_id TEXT NOT NULL,
        setting_key TEXT NOT NULL,
        value_json TEXT,
        PRIMARY KEY (book_id, module_id, setting_key),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    )''',
    'CREATE INDEX IF NOT EXISTS idx_book_module_settings_book ON book_module_settings(book_id)',

    # chapter_revisions — saved-version history for chapters edited in the web
    # editors (write editor snapshots on every explicit save, coalesced on
    # autosave). content is a JSON line array, same encoding as
    # chapters.translated_content. Pruned per (book, chapter) by kind.
    '''CREATE TABLE IF NOT EXISTS chapter_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER NOT NULL,
        title TEXT,
        content TEXT NOT NULL,
        word_count INTEGER,
        kind TEXT NOT NULL DEFAULT 'manual',
        created_at TEXT NOT NULL,
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    )''',
    'CREATE INDEX IF NOT EXISTS idx_chapter_revisions_chapter ON chapter_revisions(book_id, chapter_number)',
]

_COMMON_DDL_MYSQL = [
    # entities
    '''CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        category VARCHAR(255) NOT NULL,
        untranslated TEXT NOT NULL,
        translation TEXT NOT NULL,
        last_chapter VARCHAR(255),
        incorrect_translation TEXT,
        gender VARCHAR(50),
        book_id INTEGER,
        UNIQUE KEY uq_entity (book_id, untranslated(255))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_category ON entities(category)',
    'CREATE INDEX idx_untranslated ON entities(untranslated(255))',
    'CREATE INDEX idx_book_id ON entities(book_id)',

    # books
    '''CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        title VARCHAR(500) NOT NULL,
        author VARCHAR(500),
        language VARCHAR(10) DEFAULT 'en',
        description TEXT,
        created_date VARCHAR(50),
        modified_date VARCHAR(50),
        prompt_template LONGTEXT,
        source_language VARCHAR(10) DEFAULT 'zh',
        target_language VARCHAR(10) DEFAULT 'en',
        source_url TEXT,
        notes TEXT,
        view_count INTEGER NOT NULL DEFAULT 0,
        trad_to_simp INTEGER DEFAULT NULL,
        tags TEXT,
        modules TEXT,
        is_original INTEGER NOT NULL DEFAULT 0,
        UNIQUE KEY uq_title (title(500))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',

    # chapters — LONGTEXT for potentially huge content;
    # published_at: NULL = draft, future = scheduled, past = live
    '''CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER NOT NULL,
        title VARCHAR(500) NOT NULL,
        untranslated_content LONGTEXT NOT NULL,
        translated_content LONGTEXT NOT NULL,
        summary TEXT,
        translation_date VARCHAR(50),
        translation_model VARCHAR(255),
        published_at VARCHAR(50),
        UNIQUE KEY uq_chapter (book_id, chapter_number),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_chapters_book_id ON chapters(book_id)',
    'CREATE INDEX idx_chapter_number ON chapters(chapter_number)',

    # queue
    '''CREATE TABLE IF NOT EXISTS queue (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER,
        title VARCHAR(500) NOT NULL,
        source TEXT,
        content LONGTEXT NOT NULL,
        metadata TEXT,
        position INTEGER NOT NULL,
        created_date VARCHAR(50) NOT NULL,
        retranslation_reason TEXT,
        UNIQUE KEY uq_queue_position (position),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_queue_book_id ON queue(book_id)',
    'CREATE INDEX idx_queue_position ON queue(position)',
    'CREATE INDEX idx_queue_book_id_position ON queue(book_id, position)',

    # token_ratios
    '''CREATE TABLE IF NOT EXISTS token_ratios (
        book_id INTEGER PRIMARY KEY,
        total_input_chars INTEGER NOT NULL DEFAULT 0,
        total_output_tokens INTEGER NOT NULL DEFAULT 0,
        sample_count INTEGER NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',

    # activity_log
    '''CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        type VARCHAR(255) NOT NULL,
        message TEXT NOT NULL,
        book_id INTEGER,
        chapter INTEGER,
        book_name VARCHAR(500),
        entities_json LONGTEXT,
        created_at VARCHAR(50) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',

    # wp_publish_state
    '''CREATE TABLE IF NOT EXISTS wp_publish_state (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER,
        wp_post_id INTEGER NOT NULL,
        wp_post_type VARCHAR(50) NOT NULL,
        last_published VARCHAR(50),
        content_hash VARCHAR(255),
        wp_link VARCHAR(512),
        wp_slug VARCHAR(255),
        UNIQUE KEY uq_wp_state (book_id, chapter_number),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',

    # reader_log — tracks chapter views from the public reader
    '''CREATE TABLE IF NOT EXISTS reader_log (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER NOT NULL,
        ip VARCHAR(45) NOT NULL,
        viewed_at VARCHAR(50) NOT NULL,
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_reader_log_book ON reader_log(book_id)',
    'CREATE INDEX idx_reader_log_viewed ON reader_log(viewed_at)',

    # api_calls — logs every LLM API call made during translation
    '''CREATE TABLE IF NOT EXISTS api_calls (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        session_id VARCHAR(36) NOT NULL,
        book_id INTEGER,
        chapter_number INTEGER,
        chunk_index INTEGER NOT NULL,
        total_chunks INTEGER NOT NULL,
        system_prompt LONGTEXT,
        user_prompt LONGTEXT,
        response_text LONGTEXT,
        model_name VARCHAR(255),
        provider VARCHAR(255),
        prompt_tokens INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        duration_ms INTEGER DEFAULT 0,
        success INTEGER DEFAULT 1,
        attempt INTEGER DEFAULT 0,
        created_at VARCHAR(50) NOT NULL,
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_api_calls_book ON api_calls(book_id)',
    'CREATE INDEX idx_api_calls_session ON api_calls(session_id)',
    'CREATE INDEX idx_api_calls_chapter ON api_calls(book_id, chapter_number)',

    # recommendations — novel translation requests from public users
    '''CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        novel_title VARCHAR(500) NOT NULL,
        author VARCHAR(500),
        source_url TEXT NOT NULL,
        source_language VARCHAR(10) DEFAULT 'zh',
        description TEXT,
        requester_name VARCHAR(255) NOT NULL,
        requester_email VARCHAR(255) NOT NULL,
        notes TEXT,
        status VARCHAR(20) DEFAULT 'new',
        created_at VARCHAR(50) NOT NULL,
        reviewed_at VARCHAR(50),
        admin_notes TEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_recommendations_status ON recommendations(status)',

    # comments — reader comment threads on chapters
    '''CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER NOT NULL,
        parent_id INTEGER,
        depth INTEGER NOT NULL DEFAULT 0,
        root_id INTEGER,
        commenter_uuid VARCHAR(36) NOT NULL,
        display_name VARCHAR(40) NOT NULL,
        email VARCHAR(255),
        body LONGTEXT NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        edited_at VARCHAR(50),
        deleted_at VARCHAR(50),
        automod_state VARCHAR(20),
        automod_reason VARCHAR(255),
        ip VARCHAR(45) NOT NULL,
        user_agent VARCHAR(256),
        created_at VARCHAR(50) NOT NULL,
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
        FOREIGN KEY(parent_id) REFERENCES comments(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_comments_book_chapter ON comments(book_id, chapter_number)',
    'CREATE INDEX idx_comments_uuid ON comments(commenter_uuid)',
    'CREATE INDEX idx_comments_status ON comments(status)',
    'CREATE INDEX idx_comments_book_chap_status ON comments(book_id, chapter_number, status)',

    # commenters — trust + identity index
    '''CREATE TABLE IF NOT EXISTS commenters (
        uuid VARCHAR(36) PRIMARY KEY,
        display_name VARCHAR(40),
        email VARCHAR(255),
        is_trusted INTEGER NOT NULL DEFAULT 0,
        first_seen VARCHAR(50) NOT NULL,
        last_seen VARCHAR(50) NOT NULL,
        comment_count INTEGER NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_commenters_email ON commenters(email)',

    # comment_bans — admin bans (uuid/email/ip); IP bans also pushed to Cloudflare edge
    '''CREATE TABLE IF NOT EXISTS comment_bans (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        kind VARCHAR(10) NOT NULL,
        value VARCHAR(255) NOT NULL,
        reason TEXT,
        cf_pushed INTEGER NOT NULL DEFAULT 0,
        created_at VARCHAR(50) NOT NULL,
        UNIQUE KEY uq_ban (kind, value)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_bans_kind_value ON comment_bans(kind, value)',

    # email_suppressions — silent block list for unsubscribed / bounced addresses
    '''CREATE TABLE IF NOT EXISTS email_suppressions (
        email VARCHAR(255) PRIMARY KEY,
        reason VARCHAR(40) NOT NULL,
        created_at VARCHAR(50) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',

    # email_notifications — idempotency log: one row per (reply, recipient)
    '''CREATE TABLE IF NOT EXISTS email_notifications (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        comment_id INTEGER NOT NULL,
        recipient_email VARCHAR(255) NOT NULL,
        sent_at VARCHAR(50) NOT NULL,
        UNIQUE KEY uq_notif (comment_id, recipient_email)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_email_notif_comment ON email_notifications(comment_id)',

    # illustrations — in-chapter images extracted at import (see SQLite note)
    '''CREATE TABLE IF NOT EXISTS illustrations (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        book_id INTEGER NOT NULL,
        marker_id VARCHAR(64) NOT NULL,
        filename VARCHAR(512) NOT NULL,
        alt TEXT,
        original_href TEXT,
        ordinal INTEGER,
        queue_id INTEGER,
        chapter_id INTEGER,
        created_date VARCHAR(50),
        UNIQUE KEY uq_illustration (book_id, marker_id),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_illustrations_book ON illustrations(book_id)',
    'CREATE INDEX idx_illustrations_chapter ON illustrations(chapter_id)',
    'CREATE INDEX idx_illustrations_queue ON illustrations(queue_id)',

    # footnotes — persistent store (see SQLite note)
    '''CREATE TABLE IF NOT EXISTS footnotes (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        book_id INTEGER NOT NULL,
        chapter_id INTEGER NOT NULL,
        anchor VARCHAR(512) NOT NULL,
        source_term VARCHAR(512),
        body TEXT NOT NULL,
        occurrence INTEGER NOT NULL DEFAULT 1,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        is_source TINYINT NOT NULL DEFAULT 0,
        sort_order INTEGER,
        created_date VARCHAR(50),
        modified_date VARCHAR(50),
        UNIQUE KEY uq_footnote (chapter_id, is_source, anchor(255), occurrence),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
        FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_footnotes_book ON footnotes(book_id)',
    'CREATE INDEX idx_footnotes_chapter ON footnotes(chapter_id)',
    'CREATE INDEX idx_footnotes_status ON footnotes(status)',

    # book_module_settings — per-book, per-module structured settings (see SQLite note)
    '''CREATE TABLE IF NOT EXISTS book_module_settings (
        book_id INTEGER NOT NULL,
        module_id VARCHAR(64) NOT NULL,
        setting_key VARCHAR(128) NOT NULL,
        value_json TEXT,
        PRIMARY KEY (book_id, module_id, setting_key),
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_book_module_settings_book ON book_module_settings(book_id)',

    # chapter_revisions — saved-version history (see SQLite note)
    '''CREATE TABLE IF NOT EXISTS chapter_revisions (
        id INTEGER PRIMARY KEY AUTO_INCREMENT,
        book_id INTEGER NOT NULL,
        chapter_number INTEGER NOT NULL,
        title VARCHAR(500),
        content LONGTEXT NOT NULL,
        word_count INTEGER,
        kind VARCHAR(10) NOT NULL DEFAULT 'manual',
        created_at VARCHAR(50) NOT NULL,
        FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',
    'CREATE INDEX idx_chapter_revisions_chapter ON chapter_revisions(book_id, chapter_number)',
]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_backend(config=None):
    """
    Create the appropriate backend based on configuration.

    If *config* is a TranslationConfig instance, reads attributes from it.
    Also consults environment variables as fallback so callers that don't
    have a config object (e.g. health-check) can still work.
    """
    backend_type = 'sqlite'  # safe default

    if config is not None and hasattr(config, 'db_backend'):
        backend_type = config.db_backend
    else:
        from dotenv import load_dotenv
        load_dotenv()
        backend_type = os.getenv('DB_BACKEND', 'sqlite')

    backend_type = backend_type.lower().strip()

    if backend_type == 'mysql':
        if config is not None:
            host = getattr(config, 'mysql_host', None) or os.getenv('MYSQL_HOST', 'localhost')
            user = getattr(config, 'mysql_user', None) or os.getenv('MYSQL_USER', '')
            password = getattr(config, 'mysql_pass', None) or os.getenv('MYSQL_PASS', '')
            database = getattr(config, 'mysql_db', None) or os.getenv('MYSQL_DB', 't9')
            port = getattr(config, 'mysql_port', None) or int(os.getenv('MYSQL_PORT', '3306'))
        else:
            from dotenv import load_dotenv
            load_dotenv()
            host = os.getenv('MYSQL_HOST', 'localhost')
            user = os.getenv('MYSQL_USER', '')
            password = os.getenv('MYSQL_PASS', '')
            database = os.getenv('MYSQL_DB', 't9')
            port = int(os.getenv('MYSQL_PORT', '3306'))

        return MySQLBackend(host=host, user=user, password=password,
                            database=database, port=port)
    else:
        if config is not None:
            db_path = os.path.join(config.script_dir, "database.db")
        else:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")
        return SQLiteBackend(db_path)
