"""
Public (unauthenticated) read-only API endpoints for the book reader.

Protected by:
  - Origin/Referer header validation (requests must come from this site)
  - Per-IP rate limiting
"""
import os
import re
import time
import threading
import logging
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

_log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Cache durations (seconds)
# ------------------------------------------------------------------

_CACHE_SHORT   = 5 * 60       # 5 min  — book list, chapter list, book metadata
_CACHE_LONG    = 60 * 60 * 24     # 1 day — individual chapter content
_CACHE_STATIC  = 24 * 60 * 60 * 30  # 30 day  — cover images


def _cache(response: Response, max_age: int):
    response.headers["Cache-Control"] = f"public, max-age={max_age}"

router = APIRouter(prefix="/api/public")

_db = None
_config = None


def init(db_manager, config=None):
    global _db, _config
    _db = db_manager
    _config = config


@router.get("/site_info")
async def site_info(response: Response):
    _cache(response, _CACHE_SHORT)
    return {
        "site_name": _config.site_name if _config else "T9",
        "public_site_name": _config.public_site_name if _config else "Boonnovels",
    }


# ------------------------------------------------------------------
# Rate limiting — simple in-memory sliding window per IP
# ------------------------------------------------------------------

_RATE_WINDOW = 60        # seconds
_RATE_LIMIT  = 60        # requests per window
_hits: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()


def _rate_check(ip: str):
    now = time.time()
    cutoff = now - _RATE_WINDOW
    with _lock:
        bucket = _hits[ip]
        # Prune old entries
        _hits[ip] = bucket = [t for t in bucket if t > cutoff]
        if len(bucket) >= _RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Too many requests")
        bucket.append(now)


# ------------------------------------------------------------------
# Origin / Referer guard
# ------------------------------------------------------------------

def _origin_check(request: Request):
    """
    Reject requests that don't originate from a browser viewing our site.
    This blocks casual scripted access while remaining transparent to
    normal page visitors.  Not a security boundary — just a speed bump.
    """
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    host = request.headers.get("host", "")

    # Build set of acceptable origins from the Host header
    allowed = set()
    if host:
        allowed.add(f"http://{host}")
        allowed.add(f"https://{host}")
    # Dev origins
    allowed.add("http://localhost:5173")
    allowed.add("http://127.0.0.1:5173")
    allowed.add("http://localhost:8000")
    allowed.add("http://127.0.0.1:8000")

    # Accept if either Origin or Referer matches
    if origin and any(origin.startswith(a) for a in allowed):
        return
    if referer and any(referer.startswith(a) for a in allowed):
        return

    # Also allow if neither header is present (direct browser navigation
    # to JSON endpoint — uncommon but harmless for read-only data)
    if not origin and not referer:
        return

    raise HTTPException(status_code=403, detail="Forbidden")


# ------------------------------------------------------------------
# Middleware-style guard applied to every endpoint
# ------------------------------------------------------------------

def _guard(request: Request):
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host
    _rate_check(ip)
    _origin_check(request)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

_VALID_SORTS = {'popular', 'title', 'updated'}


@router.get("/books")
async def list_books(request: Request, response: Response, sort: str = 'popular'):
    _guard(request)
    _cache(response, _CACHE_SHORT)
    if sort not in _VALID_SORTS:
        sort = 'popular'
    books = _db.list_books(order_by=sort)
    # Return only public-facing fields, filtered to public books
    return {"books": [
        {
            "id": b["id"],
            "title": b["title"],
            "author": b.get("author"),
            "description": b.get("description"),
            "cover_image": b.get("cover_image"),
            "chapter_count": b.get("chapter_count", 0),
            "total_source_chapters": b.get("total_source_chapters"),
            "status": b.get("status", "ongoing"),
            "tags": b.get("tags") or [],
        }
        for b in books if b.get("is_public", True)
    ]}


def _get_public_book(book_id: int):
    """Fetch a book and verify it's public, or raise 404."""
    book = _db.get_book(book_id=book_id)
    if not book or not book.get("is_public", True):
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@router.get("/books/{book_id}")
async def get_book(book_id: int, request: Request, response: Response):
    _guard(request)
    _cache(response, _CACHE_SHORT)
    book = _get_public_book(book_id)
    _db.increment_book_view_count(book_id)
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book.get("author"),
        "description": book.get("description"),
        "cover_image": book.get("cover_image"),
        "source_language": book.get("source_language"),
        "total_source_chapters": book.get("total_source_chapters"),
        "status": book.get("status", "ongoing"),
        "view_count": book.get("view_count", 0),
        "tags": book.get("tags") or [],
    }


@router.get("/books/{book_id}/chapters")
async def list_chapters(book_id: int, request: Request, response: Response):
    _guard(request)
    _cache(response, _CACHE_SHORT)
    _get_public_book(book_id)
    chapters = _db.list_chapters(book_id)
    return {"chapters": [
        {"chapter": c["chapter"], "title": c.get("title")}
        for c in chapters
    ]}


@router.get("/books/{book_id}/chapters/{chapter_number}")
async def get_chapter(book_id: int, chapter_number: int, request: Request, response: Response):
    _guard(request)
    _cache(response, _CACHE_LONG)
    _get_public_book(book_id)
    ch = _db.get_chapter(book_id=book_id, chapter_number=chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="Chapter not found")
    # Log the chapter view
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host
    _db.log_reader_view(book_id, chapter_number, ip)
    content = ch.get("content", [])
    # Strip the first line if it's a chapter heading (Chapter X...)
    if content and re.match(r'^Chapter\s+\d+', content[0], re.IGNORECASE):
        content = content[1:]
    result = {
        "chapter": ch["chapter"],
        "title": ch.get("title"),
        "content": content,
    }
    if ch.get("untranslated"):
        lines = ch["untranslated"]
        # Strip all lines beginning with #
        lines = [l for l in lines if not l.startswith('#')]
        # Strip the first line if it's a chapter heading (第X章)
        if lines and re.match(r'第\d', lines[0]):
            lines = lines[1:]
        if lines:
            result["untranslated"] = lines
    return result


_COVER_CACHE_HEADERS = {"Cache-Control": f"public, max-age={_CACHE_STATIC}"}


@router.get("/books/{book_id}/cover")
async def get_cover(book_id: int, request: Request):
    _guard(request)
    book = _get_public_book(book_id)
    if not book.get("cover_image"):
        raise HTTPException(status_code=404, detail="No cover image")
    filepath = os.path.join(_db.config.script_dir, book["cover_image"])
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Cover file missing")
    return FileResponse(filepath, headers=_COVER_CACHE_HEADERS)


@router.get("/books/{book_id}/cover/thumb")
async def get_cover_thumb(book_id: int, request: Request):
    _guard(request)
    book = _get_public_book(book_id)
    if not book.get("cover_image"):
        raise HTTPException(status_code=404, detail="No cover image")
    # Reuse the covers directory from the authenticated endpoint
    covers_dir = os.path.join(_db.config.script_dir, "covers")
    thumb_path = os.path.join(covers_dir, f"{book_id}_thumb.webp")
    if os.path.exists(thumb_path):
        return FileResponse(thumb_path, media_type="image/webp", headers=_COVER_CACHE_HEADERS)
    # Fall back to full image
    filepath = os.path.join(_db.config.script_dir, book["cover_image"])
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Cover file missing")
    return FileResponse(filepath, headers=_COVER_CACHE_HEADERS)


@router.get("/books/{book_id}/epub")
async def download_epub(book_id: int, request: Request):
    """Download the cached EPUB for a public book, generating it if needed."""
    _guard(request)
    book = _get_public_book(book_id)

    cache_dir = _db._epub_cache_dir()
    cached_path = os.path.join(cache_dir, f"{book_id}.epub")

    if not os.path.exists(cached_path):
        # Generate the EPUB on demand
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from output_formatter import OutputFormatter

        chapters = _db.list_chapters(book_id)
        if not chapters:
            raise HTTPException(status_code=404, detail="No chapters available")

        # Need translator config for OutputFormatter — get it from the init-time db manager
        formatter = OutputFormatter(_db.config, _db.logger)
        book_info = {
            "title": book.get("title", "Unknown"),
            "author": book.get("author") or "Translator",
            "language": book.get("language") or "en",
        }
        if book.get("cover_image"):
            cover_full = os.path.join(_db.config.script_dir, book["cover_image"])
            if os.path.exists(cover_full):
                book_info["cover_image"] = cover_full

        all_chapters = []
        for ch_meta in chapters:
            ch = _db.get_chapter(book_id=book_id, chapter_number=ch_meta["chapter"])
            if ch:
                all_chapters.append({
                    "chapter": ch_meta["chapter"],
                    "title": ch.get("title", f"Chapter {ch_meta['chapter']}"),
                    "content": ch.get("content", []),
                })

        output_path = formatter.save_book_as_epub(all_chapters, book_info)
        if not output_path or not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="Failed to generate EPUB")

        os.makedirs(cache_dir, exist_ok=True)
        import shutil
        shutil.copy2(output_path, cached_path)

    # Log the EPUB download (chapter_number=0 signals an EPUB download)
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host
    _db.log_reader_view(book_id, 0, ip)

    filename = f"{book['title'].replace(' ', '_')}.epub"
    return FileResponse(
        cached_path,
        media_type="application/epub+zip",
        filename=filename,
        headers={
            "Cache-Control": f"public, max-age={_CACHE_SHORT}",
        },
    )


class PublicSearchRequest(BaseModel):
    query: str


@router.post("/books/{book_id}/search")
async def search_book(book_id: int, req: PublicSearchRequest, request: Request):
    _guard(request)
    _get_public_book(book_id)
    if not req.query or len(req.query) < 2:
        return {"results": [], "total_matches": 0}
    results = _db.search_book_chapters(book_id, req.query, scope="translated", is_regex=False)
    total = sum(r["match_count"] for r in results)
    return {"results": results, "total_matches": total}


# ------------------------------------------------------------------
# RSS feed — recently translated chapters
# ------------------------------------------------------------------

_FEED_DEFAULT_LIMIT = 100
_FEED_MAX_LIMIT = 400
_ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", _ATOM_NS)


def _reader_base_url(request: Request) -> str:
    """Base URL for links pointing at the reader SPA.

    READER_BASE_URL env var wins; else falls back to the request scheme+host
    (for deployments where the API and reader share a domain)."""
    env = os.getenv("READER_BASE_URL")
    if env:
        return env.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


def _chapter_link(base: str, book_id: int, chapter_number: int) -> str:
    return f"{base}/read/{book_id}/{chapter_number}"


def _rfc822(iso_str: Optional[str]) -> str:
    """Convert an ISO-8601-ish translation_date to an RFC 822 string.

    translation_date in the DB looks like '2026-04-23T20:19:46.844657' (naive).
    We treat naive timestamps as UTC for feed purposes. Falls back to 'now'
    on parse failure rather than 500-ing the whole feed."""
    if iso_str:
        try:
            s = iso_str.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return format_datetime(dt)
        except (ValueError, TypeError) as e:
            _log.warning("Could not parse translation_date %r: %s", iso_str, e)
    return format_datetime(datetime.now(timezone.utc))


def _item_description(row: dict) -> str:
    parts = [f"{row['book_title']} — Chapter {row['chapter']}"]
    if row.get("title"):
        parts[0] += f": {row['title']}"
    if row.get("summary"):
        parts.append(row["summary"].strip())
    return "\n\n".join(parts)


def _build_rss(channel_title: str,
               channel_link: str,
               channel_desc: str,
               self_href: str,
               items: list[dict]) -> bytes:
    """Assemble an RSS 2.0 document. items is a list of dicts from
    list_recent_translated_chapters + a precomputed 'link' key."""
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = channel_title
    ET.SubElement(channel, "link").text = channel_link
    ET.SubElement(channel, "description").text = channel_desc
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(channel, f"{{{_ATOM_NS}}}link", {
        "rel": "self",
        "type": "application/rss+xml",
        "href": self_href,
    })

    for row in items:
        item = ET.SubElement(channel, "item")
        title = f"{row['book_title']} — Chapter {row['chapter']}"
        if row.get("title"):
            title += f": {row['title']}"
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = row["link"]
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = row["link"]
        ET.SubElement(item, "pubDate").text = _rfc822(row.get("translation_date"))
        ET.SubElement(item, "description").text = _item_description(row)
        if row.get("book_title"):
            ET.SubElement(item, "category").text = row["book_title"]

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="utf-8")


def _feed_response(xml_bytes: bytes) -> Response:
    resp = Response(content=xml_bytes, media_type="application/rss+xml; charset=utf-8")
    _cache(resp, _CACHE_SHORT)
    return resp


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, _FEED_MAX_LIMIT))


@router.get("/feed.rss")
async def global_feed(request: Request, limit: int = _FEED_DEFAULT_LIMIT):
    _guard(request)
    limit = _clamp_limit(limit)
    rows = _db.list_recent_translated_chapters(limit=limit)
    base = _reader_base_url(request)
    for r in rows:
        r["link"] = _chapter_link(base, r["book_id"], r["chapter"])
    site = _config.public_site_name if _config else "Boonnovels"
    xml = _build_rss(
        channel_title=f"{site} — Recently Translated Chapters",
        channel_link=base,
        channel_desc=f"Latest chapters translated on {site}.",
        self_href=str(request.url),
        items=rows,
    )
    return _feed_response(xml)


@router.get("/books/{book_id}/feed.rss")
async def book_feed(book_id: int, request: Request, limit: int = _FEED_DEFAULT_LIMIT):
    _guard(request)
    book = _get_public_book(book_id)
    limit = _clamp_limit(limit)
    rows = _db.list_recent_translated_chapters(limit=limit, book_id=book_id)
    base = _reader_base_url(request)
    for r in rows:
        r["link"] = _chapter_link(base, r["book_id"], r["chapter"])
    site = _config.public_site_name if _config else "Boonnovels"
    xml = _build_rss(
        channel_title=f"{site} — {book['title']}",
        channel_link=f"{base}/read/{book_id}/1",
        channel_desc=f"Latest translated chapters of {book['title']}.",
        self_href=str(request.url),
        items=rows,
    )
    return _feed_response(xml)
