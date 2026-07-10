#!/usr/bin/env python3
"""
Sync / transfer data between SQLite and MySQL backends.

Usage:
    python3 sync_databases.py                  # SQLite → MySQL (default)
    python3 sync_databases.py --direction s2m  # SQLite → MySQL
    python3 sync_databases.py --direction m2s  # MySQL → SQLite
    python3 sync_databases.py --dry-run        # Show counts, don't write

Reads connection details from .env (same vars the app uses).

The table and column lists are INTROSPECTED from the two databases at run
time — nothing is hardcoded, so new migrations are picked up automatically.
If the two schemas disagree (a table or column exists on one side only),
the script hard-fails before writing anything: run the app once on the
destination so its migrations catch up, then re-run the sync. The
destination tables are truncated before insert (full mirror, IDs preserved).

`schema_migrations` is excluded — each side's migration bookkeeping is its
own business.
"""

import argparse
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()

# Migration bookkeeping — never mirrored.
EXCLUDED_TABLES = {"schema_migrations"}

# Tables whose rows can individually be huge (LONGTEXT chapter bodies);
# inserted one-by-one so a batch never exceeds max_allowed_packet.
LARGE_TABLES = {"chapters", "queue", "chapter_revisions"}

# Parents before children, so a partially-completed run (crash mid-way)
# fails FK-forward rather than leaving child rows pointing at nothing.
# Tables not listed here sort after these, alphabetically.
PREFERRED_ORDER = ["books", "chapters", "entities", "queue"]


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
# Schema introspection
# ---------------------------------------------------------------------------

def list_tables(conn, is_mysql):
    cursor = conn.cursor()
    if is_mysql:
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
    else:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in cursor.fetchall()]
    cursor.close()
    return sorted(t for t in tables if t not in EXCLUDED_TABLES)


def list_columns(conn, table, is_mysql):
    cursor = conn.cursor()
    if is_mysql:
        cursor.execute(f"SHOW COLUMNS FROM `{table}`")
        cols = [row[0] for row in cursor.fetchall()]
    else:
        cursor.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cursor.fetchall()]
    cursor.close()
    return cols


def build_schema(src_conn, src_is_mysql, dst_conn, dst_is_mysql):
    """Introspect both sides; return ordered [(table, columns)] or die on
    any table/column mismatch."""
    src_tables = list_tables(src_conn, src_is_mysql)
    dst_tables = list_tables(dst_conn, dst_is_mysql) if dst_conn else None

    errors = []
    if dst_tables is not None:
        for t in src_tables:
            if t not in dst_tables:
                errors.append(f"table '{t}' exists in source but not destination")
        for t in dst_tables:
            if t not in src_tables:
                errors.append(f"table '{t}' exists in destination but not source")

    schema = []
    for t in src_tables:
        src_cols = list_columns(src_conn, t, src_is_mysql)
        if dst_conn is not None and t in (dst_tables or []):
            dst_cols = list_columns(dst_conn, t, dst_is_mysql)
            missing = [c for c in src_cols if c not in dst_cols]
            extra = [c for c in dst_cols if c not in src_cols]
            if missing:
                errors.append(f"table '{t}': destination missing column(s) {missing}")
            if extra:
                errors.append(f"table '{t}': destination has extra column(s) {extra}")
        schema.append((t, src_cols))

    if errors:
        print("ERROR: schema mismatch between source and destination — refusing to sync.")
        for e in errors:
            print(f"  - {e}")
        print("\nRun the app once against the out-of-date side so its migrations "
              "apply, then re-run this sync.")
        sys.exit(1)

    def sort_key(item):
        name = item[0]
        try:
            return (0, PREFERRED_ORDER.index(name))
        except ValueError:
            return (1, name)
    schema.sort(key=sort_key)
    return schema


# ---------------------------------------------------------------------------
# Read / write helpers
# ---------------------------------------------------------------------------

def read_table(conn, table, columns, is_mysql=False):
    """Read all rows from a table.  Returns list of tuples."""
    col_list = ", ".join(columns)
    cursor = conn.cursor()
    cursor.execute(f"SELECT {col_list} FROM {table}")
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

    if table in LARGE_TABLES:
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
        src_conn, src_is_mysql = get_sqlite_conn(), False
        dst_conn, dst_is_mysql = get_mysql_conn(), True
    else:
        src_label, dst_label = "MySQL", "SQLite"
        src_conn, src_is_mysql = get_mysql_conn(), True
        dst_conn, dst_is_mysql = get_sqlite_conn(), False

    print(f"Direction: {src_label} → {dst_label}")
    if args.dry_run:
        print("(dry run — no data will be written)\n")

    # Schema check runs even on dry runs — that's half the point of one.
    schema = build_schema(src_conn, src_is_mysql, dst_conn, dst_is_mysql)

    print(f"{'Table':<24} {'Rows':>8}")
    print("-" * 34)

    table_data = {}
    total_rows = 0
    for table, columns in schema:
        rows = read_table(src_conn, table, columns, is_mysql=src_is_mysql)
        table_data[table] = rows
        total_rows += len(rows)
        print(f"{table:<24} {len(rows):>8}")

    print("-" * 34)
    print(f"{'TOTAL':<24} {total_rows:>8}")

    if args.dry_run:
        src_conn.close()
        dst_conn.close()
        print("\nDry run complete. Schemas match; no data written.")
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
    for table, columns in schema:
        rows = table_data[table]
        status = f"  {table:<24} {len(rows):>6} rows ... "
        print(status, end="", flush=True)
        write_table(dst_conn, table, columns, rows, is_mysql=dst_is_mysql)
        print("done")

    elapsed = time.time() - start
    print(f"\nSync complete in {elapsed:.1f}s. {total_rows} rows transferred to {dst_label}.")

    src_conn.close()
    dst_conn.close()


if __name__ == "__main__":
    main()
