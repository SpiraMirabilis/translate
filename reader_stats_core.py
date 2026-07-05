"""
Shared helpers for reader activity stats.

Used by:
  - reader_stats.py  (CLI text output)
  - web/api/reader_stats_api.py  (web GUI JSON endpoint)
"""
import os
import re
import socket
import sqlite3
import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def _resolve_one(ip: str, ipinfo_handler) -> dict:
    """Pure-network resolution for a single IP (reverse DNS + IPInfo). No DB access."""
    info = {"hostname": _resolve_hostname(ip)}
    if ipinfo_handler:
        info.update(_lookup_ipinfo(ipinfo_handler, ip))
    return info


def resolve_ips(ips: list[str], max_workers: int = 32) -> dict:
    """Resolve a batch of IPs to geo/hostname info, parallelizing the cache misses.

    Returns {ip: {hostname, city, region, country, org, label}}.

    SQLite (ip_cache.db) is touched only single-threaded: cache reads happen up
    front, the network lookups for misses run in a thread pool (reverse DNS and
    IPInfo both release the GIL), and the results are written back serially. This
    keeps all DB access on one thread, avoiding 'database is locked'.
    """
    uniq = list(dict.fromkeys(ip for ip in ips if ip))
    if not uniq:
        return {}

    cache_conn = _init_cache()
    ipinfo_handler = get_ipinfo_handler()
    resolved: dict[str, dict] = {}
    misses: list[str] = []
    try:
        for ip in uniq:
            cached = _get_cached(cache_conn, ip)
            if cached is not None:
                resolved[ip] = cached
            else:
                misses.append(ip)

        if misses:
            fresh: dict[str, dict] = {}
            with ThreadPoolExecutor(max_workers=min(max_workers, len(misses))) as ex:
                futures = {ex.submit(_resolve_one, ip, ipinfo_handler): ip for ip in misses}
                for fut in as_completed(futures):
                    ip = futures[fut]
                    try:
                        fresh[ip] = fut.result()
                    except Exception:
                        fresh[ip] = {"hostname": ""}
            # Write-back is single-threaded — no concurrent SQLite writers.
            for ip, info in fresh.items():
                _set_cached(cache_conn, ip, info)
                resolved[ip] = info
    finally:
        cache_conn.close()

    return {
        ip: {
            "hostname": info.get("hostname") or "",
            "city": info.get("city"),
            "region": info.get("region"),
            "country": info.get("country"),
            "org": info.get("org"),
            "label": format_ip_label(ip, info),
        }
        for ip, info in resolved.items()
    }


def get_ipinfo_handler():
    """Build an ipinfo handler if IPINFO_API_KEY is set; else None."""
    api_key = os.getenv("IPINFO_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import ipinfo
        return ipinfo.getHandler(api_key)
    except Exception:
        return None


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
# Orchestrator
# ---------------------------------------------------------------------------

def _query_reader_log(conn, cutoff: str):
    """Fetch (book_id, chapter_number, ip) rows since cutoff plus book titles."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM books")
    book_titles = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute(
        "SELECT book_id, chapter_number, ip, viewed_at "
        "FROM reader_log WHERE viewed_at >= ? ORDER BY ip, viewed_at",
        (cutoff,),
    )
    rows = cursor.fetchall()
    return rows, book_titles


def collect_reader_stats(db_manager, duration_str: str, group_by: str = "ip",
                         resolve: bool = True) -> dict:
    """Aggregate reader_log activity and return a JSON-ready dict.

    db_manager must expose get_connection() returning a sqlite3.Connection-like
    object (both DatabaseManager and db_backend.create_backend() satisfy this).

    group_by:
        "ip"   — entries grouped by IP, each containing the books that IP read
        "book" — entries grouped by book, each containing the IPs that read it

    resolve:
        True  — resolve each IP to hostname/geo inline (CLI path).
        False — return entries with blank geo (label == ip); the web path paints
                the page immediately and enriches IPs separately via resolve_ips().
    """
    if group_by not in ("ip", "book"):
        raise ValueError(f"Invalid group_by: {group_by!r} (use 'ip' or 'book')")

    delta = parse_duration(duration_str)
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%S")

    conn = db_manager.get_connection()
    try:
        rows, book_titles = _query_reader_log(conn, cutoff)
    finally:
        conn.close()

    base = {
        "duration": duration_str,
        "group_by": group_by,
        "total_views": len(rows),
        "unique_ips": 0,
    }

    if not rows:
        base["ips" if group_by == "ip" else "books"] = []
        return base

    # Build a single (ip, book_id) -> [chapter_numbers] table; pivot from there.
    pair_chapters: dict[tuple[str, int], list[int]] = defaultdict(list)
    ip_count: dict[str, int] = defaultdict(int)

    for row in rows:
        book_id, chapter, ip = row[0], row[1], row[2]
        pair_chapters[(ip, book_id)].append(chapter)
        ip_count[ip] += 1

    base["unique_ips"] = len(ip_count)

    ipinfo_handler = get_ipinfo_handler() if resolve else None
    cache_conn = _init_cache() if resolve else None

    try:
        if group_by == "ip":
            base["ips"] = _build_ip_view(
                pair_chapters, ip_count, book_titles, cache_conn, ipinfo_handler, resolve
            )
        else:
            base["books"] = _build_book_view(
                pair_chapters, book_titles, cache_conn, ipinfo_handler, resolve
            )
    finally:
        if cache_conn is not None:
            cache_conn.close()

    return base


def _summarize_chapters(chapters: list[int]) -> dict:
    """Split a chapter-number list into chapter views vs. EPUB downloads (chapter 0)."""
    epub_downloads = chapters.count(0)
    chapter_nums = [c for c in chapters if c > 0]
    return {
        "chapter_ranges": collapse_ranges(chapter_nums),
        "chapter_views": len(chapter_nums),
        "epub_downloads": epub_downloads,
    }


def _build_ip_view(pair_chapters, ip_count, book_titles, cache_conn, ipinfo_handler,
                   resolve=True):
    # ip -> {book_id: [chapters]}
    ip_books: dict[str, dict[int, list[int]]] = defaultdict(dict)
    for (ip, bid), chapters in pair_chapters.items():
        ip_books[ip][bid] = chapters

    sorted_ips = sorted(ip_books.keys(), key=lambda i: ip_count[i], reverse=True)

    entries = []
    for ip in sorted_ips:
        info = resolve_ip(ip, cache_conn, ipinfo_handler) if resolve else {}
        book_entries = []
        for bid in sorted(ip_books[ip].keys()):
            summary = _summarize_chapters(ip_books[ip][bid])
            book_entries.append({
                "book_id": bid,
                "title": book_titles.get(bid, f"Book {bid}"),
                **summary,
            })
        entries.append({
            "ip": ip,
            "label": format_ip_label(ip, info),
            "hostname": info.get("hostname") or "",
            "city": info.get("city"),
            "region": info.get("region"),
            "country": info.get("country"),
            "org": info.get("org"),
            "view_count": ip_count[ip],
            "books": book_entries,
        })
    return entries


def _build_book_view(pair_chapters, book_titles, cache_conn, ipinfo_handler, resolve=True):
    # book_id -> {ip: [chapters]}
    book_ips: dict[int, dict[str, list[int]]] = defaultdict(dict)
    for (ip, bid), chapters in pair_chapters.items():
        book_ips[bid][ip] = chapters

    # Rank books by total events (chapter views + EPUB downloads).
    def _book_total(bid):
        return sum(len(ch) for ch in book_ips[bid].values())

    sorted_books = sorted(book_ips.keys(), key=_book_total, reverse=True)

    entries = []
    for bid in sorted_books:
        ips_for_book = book_ips[bid]
        all_chapters: list[int] = []
        reader_entries = []
        for ip, chapters in sorted(ips_for_book.items(),
                                   key=lambda kv: len(kv[1]), reverse=True):
            all_chapters.extend(chapters)
            info = resolve_ip(ip, cache_conn, ipinfo_handler) if resolve else {}
            summary = _summarize_chapters(chapters)
            reader_entries.append({
                "ip": ip,
                "label": format_ip_label(ip, info),
                "hostname": info.get("hostname") or "",
                "city": info.get("city"),
                "region": info.get("region"),
                "country": info.get("country"),
                "org": info.get("org"),
                "view_count": len(chapters),
                **summary,
            })
        summary = _summarize_chapters(all_chapters)
        entries.append({
            "book_id": bid,
            "title": book_titles.get(bid, f"Book {bid}"),
            "unique_ips": len(ips_for_book),
            "view_count": len(all_chapters),
            **summary,
            "readers": reader_entries,
        })
    return entries
