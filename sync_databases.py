#!/usr/bin/env python3
"""
Sync / transfer data between SQLite and MySQL backends.

Usage:
    python3 sync_databases.py                  # SQLite → MySQL (default)
    python3 sync_databases.py --direction s2m  # SQLite → MySQL
    python3 sync_databases.py --direction m2s  # MySQL → SQLite
    python3 sync_databases.py --dry-run        # Show counts, don't write

Reads connection details from .env (same vars the app uses).
Transfers all tables in foreign-key-safe order, preserving IDs.
The destination tables are truncated before insert (full mirror).
"""

import argparse
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Table definitions — ordered so parents come before children (FK safety)
# ---------------------------------------------------------------------------

TABLES = [
    {
        "name": "books",
        "columns": [
            "id", "title", "author", "language", "description",
            "created_date", "modified_date", "prompt_template",
            "source_language", "target_language", "cover_image", "categories",
        ],
    },
    {
        "name": "chapters",
        "columns": [
            "id", "book_id", "chapter_number", "title",
            "untranslated_content", "translated_content", "summary",
            "translation_date", "translation_model", "is_proofread",
        ],
    },
    {
        "name": "entities",
        "columns": [
            "id", "category", "untranslated", "translation",
            "last_chapter", "incorrect_translation", "gender",
            "book_id", "origin_chapter", "note",
        ],
    },
    {
        "name": "queue",
        "columns": [
            "id", "book_id", "chapter_number", "title", "source",
            "content", "metadata", "position", "created_date",
        ],
    },
    {
        "name": "token_ratios",
        "columns": [
            "book_id", "total_input_chars", "total_output_tokens",
            "sample_count",
        ],
    },
    {
        "name": "activity_log",
        "columns": [
            "id", "type", "message", "book_id", "chapter",
            "book_name", "entities_json", "created_at",
        ],
    },
    {
        "name": "wp_publish_state",
        "columns": [
            "id", "book_id", "chapter_number", "wp_post_id",
            "wp_post_type", "last_published", "content_hash",
        ],
    },
]


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_sqlite_conn():
    import sqlite3
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(script_dir, "database.db")
    if not os.path.exists(db_path):
        print(f"ERROR: SQLite database not found at {db_path}")
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")  # we control insert order
    return conn


def get_mysql_conn():
    try:
        import mysql.connector
    except ImportError:
        print("ERROR: mysql-connector-python is not installed.")
        print("       sudo apt-get install python3-mysql.connector")
        sys.exit(1)

    conn = mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        user=os.getenv("MYSQL_USER", ""),
        password=os.getenv("MYSQL_PASS", ""),
        database=os.getenv("MYSQL_DB", "t9"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        autocommit=False,
    )
    return conn


# ---------------------------------------------------------------------------
# Read / write helpers
# ---------------------------------------------------------------------------

def read_table(conn, table, columns, is_mysql=False):
    """Read all rows from a table.  Returns list of tuples."""
    col_list = ", ".join(columns)
    sql = f"SELECT {col_list} FROM {table}"
    if is_mysql:
        cursor = conn.cursor()
    else:
        cursor = conn.cursor()
    cursor.execute(sql)
    rows = cursor.fetchall()
    cursor.close()
    return rows


def _sanitize_int_column(rows, col_index):
    """Replace non-integer values with None at col_index in each row."""
    cleaned = []
    for row in rows:
        row = list(row)
        val = row[col_index]
        if val is not None:
            try:
                int(val)
            except (ValueError, TypeError):
                row[col_index] = None
        cleaned.append(tuple(row))
    return cleaned


def write_table(conn, table, columns, rows, is_mysql=False):
    """Truncate destination table, then bulk-insert rows."""
    # Sanitize entities.origin_chapter — SQLite may have text in this INTEGER column
    if table == "entities" and "origin_chapter" in columns:
        idx = columns.index("origin_chapter")
        rows = _sanitize_int_column(rows, idx)

    cursor = conn.cursor()

    # Disable FK checks for the truncation + insert
    if is_mysql:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        cursor.execute(f"TRUNCATE TABLE {table}")
    else:
        cursor.execute(f"DELETE FROM {table}")

    if not rows:
        if is_mysql:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
        cursor.close()
        return

    if is_mysql:
        placeholders = ", ".join(["%s"] * len(columns))
    else:
        placeholders = ", ".join(["?"] * len(columns))

    col_list = ", ".join(columns)
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    # Tables with LONGTEXT columns (chapters, queue) can have rows that
    # exceed max_allowed_packet when batched.  Insert those one-by-one.
    # For everything else, batch for performance.
    large_tables = {"chapters", "queue"}

    if table in large_tables:
        for row in rows:
            cursor.execute(insert_sql, row)
    else:
        BATCH = 500
        for i in range(0, len(rows), BATCH):
            batch = rows[i : i + BATCH]
            cursor.executemany(insert_sql, batch)

    # Reset auto-increment to max(id)+1 for tables with an id column
    if "id" in columns:
        if is_mysql:
            cursor.execute(f"SELECT MAX(id) FROM {table}")
            max_id = cursor.fetchone()[0] or 0
            cursor.execute(f"ALTER TABLE {table} AUTO_INCREMENT = {max_id + 1}")
        # SQLite auto-adjusts AUTOINCREMENT automatically

    if is_mysql:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    conn.commit()
    cursor.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Sync data between SQLite and MySQL databases."
    )
    parser.add_argument(
        "--direction", "-d",
        choices=["s2m", "m2s"],
        default="s2m",
        help="Transfer direction: s2m = SQLite→MySQL (default), m2s = MySQL→SQLite",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show row counts without writing anything.",
    )
    args = parser.parse_args()

    if args.direction == "s2m":
        src_label, dst_label = "SQLite", "MySQL"
        src_conn = get_sqlite_conn()
        src_is_mysql = False
        if not args.dry_run:
            dst_conn = get_mysql_conn()
            dst_is_mysql = True
    else:
        src_label, dst_label = "MySQL", "SQLite"
        src_conn = get_mysql_conn()
        src_is_mysql = True
        if not args.dry_run:
            dst_conn = get_sqlite_conn()
            dst_is_mysql = False

    print(f"Direction: {src_label} → {dst_label}")
    if args.dry_run:
        print("(dry run — no data will be written)\n")

    print(f"{'Table':<20} {'Rows':>8}")
    print("-" * 30)

    table_data = {}
    total_rows = 0
    for tbl in TABLES:
        rows = read_table(src_conn, tbl["name"], tbl["columns"], is_mysql=src_is_mysql)
        table_data[tbl["name"]] = rows
        total_rows += len(rows)
        print(f"{tbl['name']:<20} {len(rows):>8}")

    print("-" * 30)
    print(f"{'TOTAL':<20} {total_rows:>8}")

    if args.dry_run:
        src_conn.close()
        print("\nDry run complete. No data written.")
        return

    # Confirm before writing
    print(f"\nThis will REPLACE all data in the {dst_label} database.")
    answer = input("Continue? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        src_conn.close()
        dst_conn.close()
        return

    print()
    start = time.time()
    for tbl in TABLES:
        rows = table_data[tbl["name"]]
        status = f"  {tbl['name']:<20} {len(rows):>6} rows ... "
        print(status, end="", flush=True)
        write_table(dst_conn, tbl["name"], tbl["columns"], rows, is_mysql=dst_is_mysql)
        print("done")

    elapsed = time.time() - start
    print(f"\nSync complete in {elapsed:.1f}s. {total_rows} rows transferred to {dst_label}.")

    src_conn.close()
    dst_conn.close()


if __name__ == "__main__":
    main()
