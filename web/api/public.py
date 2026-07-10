"""
Public (unauthenticated) read-only API endpoints for the book reader.

Protected by:
  - Origin/Referer header validation (requests must come from this site)
  - Per-IP rate limiting
"""
import os
import re
import json
import hashlib
import logging
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

import azw3
from web.services import media_urls, public_guard
from web.services.ip import client_ip

_log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Cache durations (seconds)
# ------------------------------------------------------------------

_CACHE_SHORT   = 5 * 60       # 5 min  — book list, chapter list, book metadata
_CACHE_CONTENT = 60           # 1 min  — chapter content (revalidated, see _chapter_cache)
_CACHE_STATIC  = 24 * 60 * 60 * 30  # 30 day  — cover images


def _cache(response: Response, max_age: int):
    # Text/content responses (chapter content, lists, metadata, rss, site info).
    # The admin-controlled "disable content cache" toggle lets corrections to
    # translated chapters reach readers immediately instead of waiting out the TTL.
    import settings_store
    if settings_store.get("disable_content_cache", False):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    else:
        response.headers["Cache-Control"] = f"public, max-age={max_age}"


def _chapter_cache(request: Request, response: Response, payload: dict,
                   max_age: int = _CACHE_CONTENT):
    """Cache chapter content with revalidation instead of purge-on-save.

    Chapter URLs cannot be invalidated by URL when a chapter is edited: the
    reader prefetches through /chapters/batch?nums=..., whose query string is
    an unbounded set of cache keys (?nums=302 and ?nums=302,303 are distinct
    entries). No purge list can enumerate them. So content goes stale quickly
    and carries an ETag: shared caches revalidate and pick up an edited
    chapter within max_age, and unchanged chapters cost a 304 rather than a
    re-transfer. Apache absorbs the reader fan-out, so the short TTL costs the
    origin roughly one request per chapter per minute.
    """
    import settings_store
    if settings_store.get("disable_content_cache", False):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return payload

    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    etag = '"%s"' % hashlib.md5(body.encode("utf-8")).hexdigest()
    cc = f"public, max-age={max_age}, must-revalidate"

    inm = request.headers.get("if-none-match", "")
    seen = {t.strip()[2:] if t.strip().startswith("W/") else t.strip()
            for t in inm.split(",") if t.strip()}
    if etag in seen:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cc})

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = cc
    return payload


def _media_cache_headers(max_age: int) -> dict:
    """Cache-Control headers for high-byte media (covers, illustrations, EPUBs).
    Gated by a separate toggle from text content so flipping the content cache
    doesn't force readers to re-download media that rarely changes."""
    import settings_store
    if settings_store.get("disable_media_cache", False):
        return {"Cache-Control": "no-store, max-age=0"}
    return {"Cache-Control": f"public, max-age={max_age}"}

router = APIRouter(prefix="/api/public")

_db = None
_config = None
_view_logger = None


def init(db_manager, config=None, view_logger=None):
    global _db, _config, _view_logger
    _db = db_manager
    _config = config
    _view_logger = view_logger


def _log_view(book_id: int, chapter_number: int, ip: str):
    """Buffered when the ViewLogger is wired (production), direct otherwise."""
    if _view_logger:
        _view_logger.log_view(book_id, chapter_number, ip)
    else:
        _db.log_reader_view(book_id, chapter_number, ip)


def _bump_book_view(book_id: int):
    if _view_logger:
        _view_logger.bump_book(book_id)
    else:
        _db.increment_book_view_count(book_id)


# ------------------------------------------------------------------
# Spaces / CDN helpers — shared with the admin router
# (web/services/media_urls.py); thin delegates keep call sites unchanged.
# ------------------------------------------------------------------

def _cdn_url(rel_path):
    return media_urls.cdn_url(_db, rel_path)


def _cover_urls(book):
    return media_urls.cover_urls(_db, book)


def _illustration_map(book_id, *line_lists):
    return media_urls.illustration_map(_db, book_id, *line_lists)


def _cdn_redirect_or_file(rel_path, local_filepath, headers=None):
    return media_urls.cdn_redirect_or_file(_db, rel_path, local_filepath,
                                           headers=headers or {})


@router.get("/site_info")
def site_info(response: Response):
    _cache(response, _CACHE_SHORT)
    return {
        "site_name": _config.site_name if _config else "T9",
        "public_site_name": _config.public_site_name if _config else "Boonnovels",
        "azw3_available": azw3.is_available(),
    }


# ------------------------------------------------------------------
# Rate limiting + Origin/Referer guard (shared, web/services/public_guard.py)
# ------------------------------------------------------------------

_public_limiter = public_guard.SlidingWindowLimiter(60, 60)


def _guard(request: Request):
    public_guard.guard(request, _public_limiter)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

_VALID_SORTS = {'popular', 'title', 'updated', 'newly_added'}


@router.get("/books")
def list_books(request: Request, response: Response, sort: str = 'popular'):
    _guard(request)
    _cache(response, _CACHE_SHORT)
    if sort not in _VALID_SORTS:
        sort = 'popular'
    books = _db.list_books(order_by=sort)
    # Return only public-facing fields, filtered to public books
    out = []
    for b in books:
        if not b.get("is_public", True):
            continue
        cover_url, cover_medium_url, cover_thumb_url = _cover_urls(b)
        out.append({
            "id": b["id"],
            "title": b["title"],
            "author": b.get("author"),
            "description": b.get("description"),
            "cover_image": b.get("cover_image"),
            "cover_url": cover_url,
            "cover_medium_url": cover_medium_url,
            "cover_thumb_url": cover_thumb_url,
            # Public counts/dates exclude drafts and not-yet-due scheduled chapters
            "chapter_count": b.get("published_chapter_count", 0),
            "total_source_chapters": b.get("total_source_chapters"),
            "status": b.get("status", "ongoing"),
            "source_language": b.get("source_language"),
            "tags": b.get("tags") or [],
            "created_date": b.get("created_date"),
            "last_chapter_date": b.get("last_published_date"),
        })
    return {"books": out}


def _get_public_book(book_id: int):
    """Fetch a book and verify it's public, or raise 404."""
    book = _db.get_book(book_id=book_id)
    if not book or not book.get("is_public", True):
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@router.get("/books/{book_id}")
def get_book(book_id: int, request: Request, response: Response):
    _guard(request)
    _cache(response, _CACHE_SHORT)
    book = _get_public_book(book_id)
    _bump_book_view(book_id)
    cover_url, cover_medium_url, cover_thumb_url = _cover_urls(book)
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book.get("author"),
        "description": book.get("description"),
        "cover_image": book.get("cover_image"),
        "cover_url": cover_url,
        "cover_medium_url": cover_medium_url,
        "cover_thumb_url": cover_thumb_url,
        "source_language": book.get("source_language"),
        "total_source_chapters": book.get("total_source_chapters"),
        "status": book.get("status", "ongoing"),
        "view_count": book.get("view_count", 0),
        "tags": book.get("tags") or [],
    }


@router.get("/books/{book_id}/chapters")
def list_chapters(book_id: int, request: Request, response: Response,
                        limit: Optional[int] = None, offset: int = 0):
    _guard(request)
    _cache(response, _CACHE_SHORT)
    _get_public_book(book_id)
    if limit is not None:
        limit = max(1, min(int(limit), 5000))
    chapters = _db.list_chapters(book_id, limit=limit, offset=max(0, int(offset)),
                                 published_only=True)
    return {"chapters": [
        {"chapter": c["chapter"], "title": c.get("title")}
        for c in chapters
    ]}


def _shape_public_chapter(ch: dict, book_id: int = None) -> dict:
    content = ch.get("content", [])
    if content and re.match(r'^Chapter\s+\d+', content[0], re.IGNORECASE):
        content = content[1:]
    result = {
        "chapter": ch["chapter"],
        "title": ch.get("title"),
        "content": content,
    }
    if ch.get("untranslated"):
        lines = [l for l in ch["untranslated"] if not l.startswith('#')]
        if lines and re.match(r'第\d', lines[0]):
            lines = lines[1:]
        if lines:
            result["untranslated"] = lines
    # CDN URLs for any in-chapter illustrations (omitted when Spaces disabled).
    if book_id is not None:
        imap = _illustration_map(book_id, content, result.get("untranslated"))
        if imap:
            result["illustrations"] = imap
    return result


_BATCH_MAX = 10


@router.get("/books/{book_id}/chapters/batch")
def get_chapters_batch(book_id: int, nums: str, request: Request, response: Response):
    _guard(request)
    _get_public_book(book_id)
    try:
        wanted = [int(n) for n in nums.split(",") if n.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="nums must be comma-separated integers")
    if not wanted:
        return {"chapters": []}
    if len(wanted) > _BATCH_MAX:
        raise HTTPException(status_code=400, detail=f"At most {_BATCH_MAX} chapters per batch")
    out = []
    for ch in _db.get_chapters_bulk(book_id, wanted, include_untranslated=True,
                                    published_only=True):
        out.append(_shape_public_chapter(ch, book_id))
    return _chapter_cache(request, response, {"chapters": out})


@router.get("/books/{book_id}/chapters/{chapter_number}")
def get_chapter(book_id: int, chapter_number: int, request: Request, response: Response):
    _guard(request)
    _get_public_book(book_id)
    ch = _db.get_chapter(book_id=book_id, chapter_number=chapter_number,
                         published_only=True)
    if not ch:
        # Without an explicit header, Apache's CacheDefaultExpire would cache
        # this 404 — so a chapter would stay "missing" after it goes live.
        raise HTTPException(status_code=404, detail="Chapter not found",
                            headers={"Cache-Control": "no-store"})
    return _chapter_cache(request, response, _shape_public_chapter(ch, book_id))


@router.post("/books/{book_id}/chapters/{chapter_number}/view", status_code=204)
def record_chapter_view(book_id: int, chapter_number: int, request: Request):
    """Record that a reader actually read this chapter.

    Views are NOT logged when chapter content is fetched: the reader prefetches
    the next two chapters (Reader.jsx), so a fetch means "might be read soon",
    and chapter GETs are cacheable, so a cache hit means a real
    read never reaches us at all. The reader beacons this endpoint instead,
    after a dwell on a visible chapter. Uncacheable by virtue of being a POST.

    Repeat views are collapsed by ViewLogger's dedupe window, not here.
    """
    _guard(request)
    _get_public_book(book_id)   # 404s unless the book is publicly visible
    _log_view(book_id, chapter_number, client_ip(request))


@router.get("/books/{book_id}/cover")
def get_cover(book_id: int, request: Request):
    _guard(request)
    book = _get_public_book(book_id)
    if not book.get("cover_image"):
        raise HTTPException(status_code=404, detail="No cover image")
    filepath = os.path.join(_db.config.script_dir, book["cover_image"])
    return _cdn_redirect_or_file(book["cover_image"], filepath, _media_cache_headers(_CACHE_STATIC))


def _serve_cover_derivative(book_id, kind):
    """Serve a cover derivative (thumb|medium), generating on the fly if missing,
    with CDN redirect + full-cover fallback."""
    import cover_images
    book = _get_public_book(book_id)
    if not book.get("cover_image"):
        raise HTTPException(status_code=404, detail="No cover image")
    path = cover_images.ensure_derivative(_db.config, book, kind)
    if not path:
        filepath = os.path.join(_db.config.script_dir, book["cover_image"])
        return _cdn_redirect_or_file(book["cover_image"], filepath, _media_cache_headers(_CACHE_STATIC))
    return _cdn_redirect_or_file(
        cover_images.derivative_relpath(book_id, kind), path, _media_cache_headers(_CACHE_STATIC)
    )


@router.get("/books/{book_id}/cover/thumb")
def get_cover_thumb(book_id: int, request: Request):
    _guard(request)
    return _serve_cover_derivative(book_id, "thumb")


@router.get("/books/{book_id}/cover/medium")
def get_cover_medium(book_id: int, request: Request):
    _guard(request)
    return _serve_cover_derivative(book_id, "medium")


@router.get("/books/{book_id}/illustration/{marker_id}")
def get_illustration(book_id: int, marker_id: str, request: Request):
    """Serve an in-chapter illustration referenced by ⟦IMG:<marker_id>⟧."""
    _guard(request)
    if not re.fullmatch(r"[0-9a-f]{4,}", marker_id or ""):
        raise HTTPException(status_code=400, detail="Invalid illustration id")
    _get_public_book(book_id)  # 404s if book missing / not public
    row = _db.get_book_illustration(book_id, marker_id)
    if not row or not row.get("filename"):
        raise HTTPException(status_code=404, detail="Illustration not found")
    filepath = os.path.join(_db.config.script_dir, row["filename"])
    return _cdn_redirect_or_file(row["filename"], filepath, _media_cache_headers(_CACHE_STATIC))


def _epub_version_basis(book_id: int, book: dict):
    """Version basis for cached artifacts: the book's modified_date (bumped by
    every save and explicit publish/unpublish) OR the latest publish time that
    has already passed — so a scheduled chapter crossing its publish time
    changes the version and forces a regenerate, without any cron.

    Returns (version_basis, latest_published)."""
    latest_published = _db.latest_published_at(book_id)
    version_basis = max(filter(None, [book.get("modified_date"), latest_published]),
                        default=None)
    return version_basis, latest_published


def _ensure_cached_epub(book_id: int, book: dict, version_basis, latest_published) -> str:
    """Ensure the book's EPUB exists on disk (generating on demand), mirror it to
    Spaces/CDN, and return the local cached path. Shared by the EPUB and AZW3
    endpoints. Raises HTTPException on generation failure / no chapters."""
    cache_dir = _db._epub_cache_dir()
    cached_path = os.path.join(cache_dir, f"{book_id}.epub")

    # Disk cache built before a scheduled chapter went live is stale — rebuild.
    if os.path.exists(cached_path) and latest_published:
        try:
            import datetime as _dt
            went_live = _dt.datetime.fromisoformat(latest_published).timestamp()
            if os.path.getmtime(cached_path) < went_live:
                os.remove(cached_path)
        except (ValueError, OSError):
            pass

    if not os.path.exists(cached_path):
        # Generate the EPUB on demand
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from output_formatter import OutputFormatter

        # Need translator config for OutputFormatter — get it from the init-time db manager
        formatter = OutputFormatter(_db.config, _db.logger)
        book_info = {
            "id": book_id,
            "title": book.get("title", "Unknown"),
            "author": book.get("author") or "Translator",
            "language": book.get("language") or "en",
        }
        if book.get("cover_image"):
            cover_full = os.path.join(_db.config.script_dir, book["cover_image"])
            if os.path.exists(cover_full):
                book_info["cover_image"] = cover_full

        all_chapters = [
            {
                "chapter": ch["chapter"],
                "title": ch.get("title") or f"Chapter {ch['chapter']}",
                "content": ch.get("content", []),
            }
            for ch in _db.get_chapters_bulk(book_id, published_only=True)
        ]
        if not all_chapters:
            raise HTTPException(status_code=404, detail="No chapters available")

        output_path = formatter.save_book_as_epub(all_chapters, book_info)
        if not output_path or not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="Failed to generate EPUB")

        os.makedirs(cache_dir, exist_ok=True)
        import shutil
        shutil.copy2(output_path, cached_path)

    # Mirror the EPUB to Spaces under its version key, prune stale versions.
    try:
        import spaces
        if spaces.is_enabled(_db.config):
            ver = spaces.epub_version(book_id, version_basis)
            key = spaces.epub_key(_db.config, book_id, ver)
            if spaces.upload(_db.config, cached_path, key, "application/epub+zip"):
                spaces.prune_epub_versions(_db.config, book_id, keep_key=key)
    except Exception:
        pass

    return cached_path


@router.get("/books/{book_id}/epub")
def download_epub(book_id: int, request: Request):
    """Download the cached EPUB for a public book, generating it if needed."""
    _guard(request)
    book = _get_public_book(book_id)

    ip = client_ip(request)
    version_basis, latest_published = _epub_version_basis(book_id, book)

    # If Spaces holds the current content version, redirect to the immutable CDN
    # URL — the VM serves no bytes.
    try:
        import spaces
        from fastapi.responses import RedirectResponse
        if spaces.is_enabled(_db.config):
            ver = spaces.epub_version(book_id, version_basis)
            key = spaces.epub_key(_db.config, book_id, ver)
            if spaces.exists(_db.config, key):
                _log_view(book_id, 0, ip)
                return RedirectResponse(spaces.public_url(_db.config, key), status_code=302)
    except Exception:
        pass

    cached_path = _ensure_cached_epub(book_id, book, version_basis, latest_published)

    # Log the EPUB download (chapter_number=0 signals an EPUB download)
    _log_view(book_id, 0, ip)

    filename = f"{book['title'].replace(' ', '_')}.epub"
    return FileResponse(
        cached_path,
        media_type="application/epub+zip",
        filename=filename,
        headers=_media_cache_headers(_CACHE_SHORT),
    )


@router.get("/books/{book_id}/azw3")
def download_azw3(book_id: int, request: Request):
    """Download the cached AZW3 (Kindle) file for a public book.

    Derived from the book's EPUB via Calibre's ebook-convert; generated on
    demand, cached to disk and mirrored to Spaces/CDN just like the EPUB."""
    _guard(request)
    book = _get_public_book(book_id)

    if not azw3.is_available():
        raise HTTPException(status_code=503, detail="AZW3 conversion not available")

    ip = client_ip(request)
    version_basis, latest_published = _epub_version_basis(book_id, book)

    # Redirect to the immutable CDN copy if Spaces already holds this version.
    try:
        import spaces
        from fastapi.responses import RedirectResponse
        if spaces.is_enabled(_db.config):
            ver = spaces.epub_version(book_id, version_basis)
            key = spaces.azw3_key(_db.config, book_id, ver)
            if spaces.exists(_db.config, key):
                _log_view(book_id, 0, ip)
                return RedirectResponse(spaces.public_url(_db.config, key), status_code=302)
    except Exception:
        pass

    # Ensure the source EPUB is present, then convert to AZW3. Rebuild the AZW3
    # whenever it is missing or older than the EPUB it derives from.
    epub_path = _ensure_cached_epub(book_id, book, version_basis, latest_published)
    cache_dir = _db._epub_cache_dir()
    azw3_path = os.path.join(cache_dir, f"{book_id}.azw3")

    needs_build = (not os.path.exists(azw3_path)
                   or os.path.getmtime(azw3_path) < os.path.getmtime(epub_path))
    if needs_build:
        if not azw3.convert_epub_to_azw3(epub_path, azw3_path, _db.logger):
            raise HTTPException(status_code=500, detail="Failed to generate AZW3")

    # Mirror the AZW3 to Spaces under its version key, prune stale versions.
    try:
        import spaces
        if spaces.is_enabled(_db.config):
            ver = spaces.epub_version(book_id, version_basis)
            key = spaces.azw3_key(_db.config, book_id, ver)
            if spaces.upload(_db.config, azw3_path, key, "application/x-mobi8-ebook"):
                spaces.prune_azw3_versions(_db.config, book_id, keep_key=key)
    except Exception:
        pass

    # Log the download (chapter_number=0 signals an ebook download)
    _log_view(book_id, 0, ip)

    filename = f"{book['title'].replace(' ', '_')}.azw3"
    return FileResponse(
        azw3_path,
        media_type="application/x-mobi8-ebook",
        filename=filename,
        headers=_media_cache_headers(_CACHE_SHORT),
    )


@router.get("/books/{book_id}/azw3/status")
def azw3_status(book_id: int, request: Request, response: Response):
    """Whether the current AZW3 is already in Spaces/CDN (fast download) or would
    be generated on demand (slow). Never triggers a build — lets the reader warn
    the user before a multi-minute wait. `available` is False when conversion
    isn't installed at all."""
    _guard(request)
    response.headers["Cache-Control"] = "no-store"  # must reflect live cache state
    book = _get_public_book(book_id)
    if not azw3.is_available():
        return {"available": False, "cached": False}
    version_basis, _ = _epub_version_basis(book_id, book)
    cached = False
    try:
        import spaces
        if spaces.is_enabled(_db.config):
            ver = spaces.epub_version(book_id, version_basis)
            key = spaces.azw3_key(_db.config, book_id, ver)
            cached = spaces.exists(_db.config, key)
    except Exception:
        cached = False
    return {"available": True, "cached": cached}


class PublicSearchRequest(BaseModel):
    query: str


@router.post("/books/{book_id}/search")
def search_book(book_id: int, req: PublicSearchRequest, request: Request):
    _guard(request)
    _get_public_book(book_id)
    if not req.query or len(req.query) < 2:
        return {"results": [], "total_matches": 0}
    results = _db.search_book_chapters(book_id, req.query, scope="translated", is_regex=False,
                                       published_only=True)
    total = sum(r["match_count"] for r in results)
    return {"results": results, "total_matches": total}


# ------------------------------------------------------------------
# RSS feed — recently translated chapters
# ------------------------------------------------------------------

_FEED_DEFAULT_LIMIT = 100
_FEED_MAX_LIMIT = 400
# ?around=N window for the per-book feed: chapters N-50 .. N+100. Wide on
# purpose — feed readers (e.g. Novel Updates) discover the feed from a
# chapter URL, and a recency-only feed may already have scrolled past the
# chapters the reader still needs to pick up.
_FEED_AROUND_BEFORE = 50
_FEED_AROUND_AFTER = 100
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
def global_feed(request: Request, limit: int = _FEED_DEFAULT_LIMIT):
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
def book_feed(book_id: int, request: Request, limit: int = _FEED_DEFAULT_LIMIT,
              around: Optional[int] = None):
    _guard(request)
    book = _get_public_book(book_id)
    limit = _clamp_limit(limit)
    chapter_min = chapter_max = None
    if around is not None:
        chapter_min = around - _FEED_AROUND_BEFORE
        chapter_max = around + _FEED_AROUND_AFTER
        # A recency limit would defeat the window: make sure the whole
        # window fits (still capped by _FEED_MAX_LIMIT).
        limit = _clamp_limit(max(limit, _FEED_AROUND_BEFORE + _FEED_AROUND_AFTER + 1))
    rows = _db.list_recent_translated_chapters(limit=limit, book_id=book_id,
                                               chapter_min=chapter_min,
                                               chapter_max=chapter_max)
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
