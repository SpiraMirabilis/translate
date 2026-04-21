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
        if self._use_dict_cursor:
            return _MySQLDictCursorWrapper(self._conn.cursor())
        return _MySQLCursorWrapper(self._conn.cursor())

    def dict_cursor(self):
        """Return a cursor whose fetch* methods return dicts."""
        return _MySQLDictCursorWrapper(self._conn.cursor())

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
        """Return a plain sqlite3 connection."""
        return sqlite3.connect(self.db_path)

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

    def get_connection(self):
        """Return a wrapped mysql.connector connection."""
        import mysql.connector
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
        UNIQUE(title)
    )''',

    # chapters
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
        UNIQUE KEY uq_title (title(500))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''',

    # chapters — LONGTEXT for potentially huge content
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
