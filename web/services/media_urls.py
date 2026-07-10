"""
Cover / illustration CDN URL helpers, shared by the admin (web/api/books.py)
and public (web/api/public.py) routers — previously two drifting copies.

All functions take the DatabaseManager explicitly (its .config carries the
Spaces settings); no module globals, so tests can call these directly.
Spaces failures degrade to None/local-file serving, never to a 500.
"""
import os

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse


def cdn_url(db, rel_path):
    """CDN URL for a local relative media path, or None when Spaces is disabled."""
    if not rel_path:
        return None
    try:
        import spaces
        return spaces.url_for_relpath(db.config, rel_path)
    except Exception:
        return None


def cover_urls(db, book):
    """(cover_url, cover_medium_url, cover_thumb_url) CDN urls, or Nones."""
    if not book.get("cover_image"):
        return None, None, None
    return (
        cdn_url(db, book["cover_image"]),
        cdn_url(db, f"covers/{book['id']}_medium.webp"),
        cdn_url(db, f"covers/{book['id']}_thumb.webp"),
    )


def attach_cover_urls(db, book):
    """Add cover_url / cover_medium_url / cover_thumb_url CDN fields in place."""
    if isinstance(book, dict) and book.get("cover_image"):
        book["cover_url"], book["cover_medium_url"], book["cover_thumb_url"] = \
            cover_urls(db, book)
    return book


def illustration_map(db, book_id, *line_lists):
    """Map {marker_id: cdn_url} for markers in the given content arrays.

    Returns None when Spaces is disabled (frontend then falls back to the API
    route), so image-free / local-only deployments carry no extra payload.
    """
    try:
        import spaces
        if not spaces.is_enabled(db.config):
            return None
        from illustrations import markers_in
        out = {}
        for lines in line_lists:
            for mid in markers_in(lines or []):
                if mid in out:
                    continue
                row = db.get_book_illustration(book_id, mid)
                if row and row.get("filename"):
                    out[mid] = spaces.url_for_relpath(db.config, row["filename"])
        return out or None
    except Exception:
        return None


def attach_illustrations(db, book_id, ch):
    """Add an {marker_id: cdn_url} map to a chapter dict in place (Spaces only)."""
    if isinstance(ch, dict):
        imap = illustration_map(db, book_id, ch.get("content"), ch.get("untranslated"))
        if imap:
            ch["illustrations"] = imap
    return ch


def cdn_redirect_or_file(db, rel_path, local_filepath, media_type=None, headers=None):
    """Redirect to the CDN object if present, else serve the local file."""
    try:
        import spaces
        cfg = db.config
        if spaces.is_enabled(cfg):
            key = spaces.key_for(cfg, rel_path)
            if spaces.exists(cfg, key):
                return RedirectResponse(spaces.public_url(cfg, key), status_code=302)
    except Exception:
        pass
    if not os.path.exists(local_filepath):
        raise HTTPException(status_code=404, detail="File missing.")
    kwargs = {}
    if media_type:
        kwargs["media_type"] = media_type
    if headers:
        kwargs["headers"] = dict(headers)
    if local_filepath.lower().endswith(".svg"):
        # SVG can carry <script>. <img> embedding still renders with these
        # headers, but direct navigation downloads instead of executing on
        # this origin, and the CSP sandbox kills scripts even if rendered.
        hdrs = kwargs.setdefault("headers", {})
        hdrs["Content-Security-Policy"] = "sandbox; script-src 'none'"
        hdrs["Content-Disposition"] = "attachment"
        hdrs["X-Content-Type-Options"] = "nosniff"
    return FileResponse(local_filepath, **kwargs)
