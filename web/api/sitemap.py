"""
Sitemap generation — ADMIN-ONLY (session auth, admin process only).

Deliberately not part of the public surface. A build walks every public
book's chapter list (~38k rows), which is far too expensive to let crawlers
trigger; and this router is only registered by create_app(public_only=False),
so the path does not exist in the reader process at all.

Normal operation is unattended: a cron job runs `python3 sitemap.py` twice
a day, writing the files into SITEMAP_DIR, and the public process serves
them as static bytes (web/app_factory.py::sitemap_file). These endpoints
are for the human — check what the sitemap currently holds, force a rebuild
after publishing a batch, or download a copy — and nothing here is on the
crawl path.
"""
import io
import os
import time
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

import sitemap as sitemap_builder

router = APIRouter()

_db = None
_config = None

# One generation serves both the status poll and the download that follows
# it. Short TTL: the point is to not build twice for one click, not to serve
# a stale sitemap.
_CACHE_TTL = 300
_cache = {"key": None, "at": 0.0, "result": None}


def init(db_manager, config=None):
    global _db, _config
    _db = db_manager
    _config = config


def _base_url(override: str | None) -> str:
    base = (override or "").strip().rstrip("/") or sitemap_builder.resolve_base_url(_config)
    if not base:
        raise HTTPException(
            status_code=400,
            detail="No public base URL configured. Set SITE_BASE_URL in .env "
                   "(e.g. https://reader.boondollars.com) or pass ?base=",
        )
    if not base.startswith(("http://", "https://")):
        raise HTTPException(status_code=400,
                            detail=f"Base URL must start with http:// or https:// (got {base!r})")
    return base


def _generate(base: str, refresh: bool = False) -> dict:
    key = base
    if (not refresh and _cache["key"] == key and _cache["result"]
            and time.time() - _cache["at"] < _CACHE_TTL):
        return _cache["result"]
    result = sitemap_builder.build(_db, base)
    result["base_url"] = base
    result["generated_at"] = time.time()
    _cache.update(key=key, at=time.time(), result=result)
    return result


def _published_state() -> dict:
    """What is actually on disk right now, i.e. what crawlers are being served.

    Deliberately separate from a generated result: the whole point of the
    cron-plus-static-file arrangement is that the served file is a snapshot,
    not whatever a fresh build would produce this second.
    """
    out_dir = sitemap_builder.resolve_output_dir(_config)
    main = os.path.join(out_dir, "sitemap.xml")
    if not os.path.isfile(main):
        return {"dir": out_dir, "exists": False, "files": [], "modified_at": None,
                "url": None}
    names = sorted(n for n in os.listdir(out_dir)
                   if n.startswith("sitemap") and n.endswith(".xml"))
    base = sitemap_builder.resolve_base_url(_config)
    return {
        "dir": out_dir,
        "exists": True,
        "modified_at": os.path.getmtime(main),
        "url": f"{base}/sitemap.xml" if base else None,
        "files": [{"name": n, "bytes": os.path.getsize(os.path.join(out_dir, n))}
                  for n in names],
    }


@router.get("/api/sitemap/status")
def sitemap_status(base: str = None, refresh: bool = False):
    """Counts and file layout, for the Settings card — no file bodies."""
    result = _generate(_base_url(base), refresh=refresh)
    return {
        "base_url": result["base_url"],
        "url_count": result["url_count"],
        "index": result["index"],
        "max_urls_per_file": sitemap_builder.MAX_URLS_PER_FILE,
        "generated_at": result["generated_at"],
        "files": [{"name": name, "bytes": len(data)} for name, data in result["files"]],
        "total_bytes": sum(len(data) for _, data in result["files"]),
        "published": _published_state(),
    }


@router.post("/api/sitemap/publish")
def sitemap_publish(base: str = None):
    """Rebuild and write the files the public route serves.

    The same thing the cron job does — for when a batch of chapters has just
    gone live and waiting for the next scheduled run isn't wanted. Always
    builds fresh (no cache): publishing a stale snapshot is the one outcome
    that would be worse than not publishing at all.
    """
    result = _generate(_base_url(base), refresh=True)
    out_dir = sitemap_builder.resolve_output_dir(_config)
    try:
        sitemap_builder.write_files(result["files"], out_dir)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not write to {out_dir}: {e}")
    return {"status": "ok", "url_count": result["url_count"],
            "published": _published_state()}


@router.get("/api/sitemap.xml")
def sitemap_xml(base: str = None, refresh: bool = False):
    """The file to submit. A urlset normally; a sitemap index once the URL
    count passes MAX_URLS_PER_FILE, in which case the parts it names come
    from /api/sitemap.zip and must be hosted alongside it."""
    result = _generate(_base_url(base), refresh=refresh)
    name, data = result["files"][0]
    return Response(
        content=data,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Sitemap-Url-Count": str(result["url_count"]),
            "X-Sitemap-File-Count": str(len(result["files"])),
            "Cache-Control": "no-store",
        },
    )


@router.get("/api/sitemap.zip")
def sitemap_zip(base: str = None, refresh: bool = False):
    """Every file in one download — the index plus its parts. Always
    available, so the Settings button works the same before and after the
    catalog outgrows a single file."""
    result = _generate(_base_url(base), refresh=refresh)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in result["files"]:
            zf.writestr(name, data)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="sitemap.zip"',
            "Cache-Control": "no-store",
        },
    )
