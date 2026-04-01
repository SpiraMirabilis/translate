#!/usr/bin/env python3
"""
Print a summary of recent reader activity from the reader_log table.

Usage:
    python reader_stats.py              # last 24 hours (default)
    python reader_stats.py 7d           # last 7 days
    python reader_stats.py 12h          # last 12 hours
    python reader_stats.py 30m          # last 30 minutes
"""
import sys
import os
import re
import socket
import sqlite3
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from db_backend import create_backend

# ---------------------------------------------------------------------------
# IP info cache  (simple SQLite DB next to this script)
# ---------------------------------------------------------------------------

_CACHE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ip_cache.db")
_CACHE_TTL_DAYS = 30


def _init_cache():
    conn = sqlite3.connect(_CACHE_DB)
    conn.execute('''CREATE TABLE IF NOT EXISTS ip_cache (
        ip TEXT PRIMARY KEY,
        hostname TEXT,
        city TEXT,
        region TEXT,
        country TEXT,
        org TEXT,
        cached_at TEXT NOT NULL
    )''')
    conn.commit()
    return conn


def _get_cached(cache_conn, ip: str) -> dict | None:
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=_CACHE_TTL_DAYS)).isoformat()
    row = cache_conn.execute(
        "SELECT hostname, city, region, country, org FROM ip_cache "
        "WHERE ip = ? AND cached_at >= ?", (ip, cutoff)
    ).fetchone()
    if row:
        return {"hostname": row[0], "city": row[1], "region": row[2],
                "country": row[3], "org": row[4]}
    return None


def _set_cached(cache_conn, ip: str, info: dict):
    cache_conn.execute(
        "INSERT OR REPLACE INTO ip_cache (ip, hostname, city, region, country, org, cached_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ip, info.get("hostname"), info.get("city"), info.get("region"),
         info.get("country"), info.get("org"),
         datetime.datetime.now(datetime.timezone.utc).isoformat())
    )
    cache_conn.commit()


# ---------------------------------------------------------------------------
# IP lookup
# ---------------------------------------------------------------------------

def _resolve_hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return ""


def _lookup_ipinfo(handler, ip: str) -> dict:
    try:
        details = handler.getDetails(ip)
        return {
            "city": getattr(details, "city", None),
            "region": getattr(details, "region", None),
            "country": getattr(details, "country_name",
                       getattr(details, "country", None)),
            "org": getattr(details, "org", None),
        }
    except Exception:
        return {}


def resolve_ip(ip: str, cache_conn, ipinfo_handler) -> dict:
    """Return {hostname, city, region, country, org} for an IP, using cache."""
    cached = _get_cached(cache_conn, ip)
    if cached:
        return cached

    info = {"hostname": _resolve_hostname(ip)}
    if ipinfo_handler:
        geo = _lookup_ipinfo(ipinfo_handler, ip)
        info.update(geo)

    _set_cached(cache_conn, ip, info)
    return info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_duration(s: str) -> datetime.timedelta:
    m = re.fullmatch(r'(\d+)\s*([dhm])', s.strip().lower())
    if not m:
        raise ValueError(f"Invalid duration: {s!r}  (use e.g. 7d, 12h, 30m)")
    n, unit = int(m.group(1)), m.group(2)
    if unit == 'd':
        return datetime.timedelta(days=n)
    elif unit == 'h':
        return datetime.timedelta(hours=n)
    return datetime.timedelta(minutes=n)


def collapse_ranges(numbers: list[int]) -> str:
    if not numbers:
        return ""
    nums = sorted(set(numbers))
    ranges = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}-{prev}" if prev != start else str(start))
            start = prev = n
    ranges.append(f"{start}-{prev}" if prev != start else str(start))
    return ", ".join(ranges)


def format_ip_label(ip: str, info: dict) -> str:
    """Build a display string like: 1.2.3.4 (host.example.com — Tokyo, Japan — AS1234 Acme)"""
    parts = []
    if info.get("hostname"):
        parts.append(info["hostname"])
    geo_parts = [p for p in [info.get("city"), info.get("region"), info.get("country")] if p]
    if geo_parts:
        parts.append(", ".join(geo_parts))
    if info.get("org"):
        parts.append(info["org"])
    if parts:
        return f"{ip}  ({' — '.join(parts)})"
    return ip


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    duration_str = sys.argv[1] if len(sys.argv) > 1 else "24h"
    try:
        delta = parse_duration(duration_str)
    except ValueError as e:
        print(e)
        sys.exit(1)

    cutoff = (datetime.datetime.now(datetime.timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%S")

    backend = create_backend()
    conn = backend.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, title FROM books")
    book_titles = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute(
        "SELECT book_id, chapter_number, ip, viewed_at "
        "FROM reader_log WHERE viewed_at >= ? ORDER BY ip, viewed_at",
        (cutoff,),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print(f"No reader activity in the last {duration_str}.")
        return

    # Group: ip -> book_id -> [chapter_numbers]
    ip_books: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    ip_count: dict[str, int] = defaultdict(int)

    for row in rows:
        book_id, chapter, ip = row[0], row[1], row[2]
        ip_books[ip][book_id].append(chapter)
        ip_count[ip] += 1

    sorted_ips = sorted(ip_books.keys(), key=lambda i: ip_count[i], reverse=True)

    # Set up IP lookups
    api_key = os.getenv("IPINFO_API_KEY", "").strip()
    ipinfo_handler = None
    if api_key:
        try:
            import ipinfo
            ipinfo_handler = ipinfo.getHandler(api_key)
        except Exception:
            pass

    cache_conn = _init_cache()

    print(f"Reader activity — last {duration_str} ({len(rows)} total views, {len(sorted_ips)} unique IPs)\n")

    for ip in sorted_ips:
        info = resolve_ip(ip, cache_conn, ipinfo_handler)
        label = format_ip_label(ip, info)
        books = ip_books[ip]

        print(f"  {label}")
        for bid in sorted(books.keys()):
            title = book_titles.get(bid, f"Book {bid}")
            epub_downloads = books[bid].count(0)
            chapter_nums = [c for c in books[bid] if c > 0]
            parts = []
            if chapter_nums:
                chapters = collapse_ranges(chapter_nums)
                parts.append(f"ch {chapters} ({len(chapter_nums)} views)")
            if epub_downloads:
                parts.append(f"EPUB download x{epub_downloads}")
            print(f"    {title}  {', '.join(parts)}")
        print()

    cache_conn.close()


if __name__ == "__main__":
    main()
