"""
Google-compliant XML sitemap generation for the public reader.

The public surface is a React SPA with exactly three crawlable shapes
(see web/app_factory.py::_PUBLIC_SPA_PREFIXES and App.jsx):

    /library                        the catalog
    /library/book/{book_id}         a book's detail page
    /library/read/{book_id}/{n}     a chapter

`/read/{id}/{n}` is a second, older path to the same reader (the RSS feeds
link to it). It is deliberately NOT emitted here: a sitemap should name one
canonical URL per page, and the site's own links all use the /library form.

Output follows the sitemaps.org 0.9 schema. Two hard limits apply: 50,000
URLs and 50MB uncompressed per file. Past MAX_URLS_PER_FILE the URLs are
split across sitemap-1.xml, sitemap-2.xml, … and sitemap.xml becomes a
sitemap index pointing at them — the split is invisible to the caller,
which always submits sitemap.xml.

Generation is deliberately not exposed publicly: at ~38k chapters a build
walks every book's chapter list, which is far too much work to let a
crawler trigger on a whim. The admin API (web/api/sitemap.py) hands the
finished files to a human, who hosts them as static files.

The files are served publicly as plain static files from SITEMAP_DIR (see
web/app_factory.py) — Google will only accept a sitemap it can fetch from
the site itself. Nothing is generated on the request path: a cron job
rebuilds the directory periodically, and the public route only reads bytes
off disk.

CLI:
    python3 sitemap.py                       # rebuild the served directory
    python3 sitemap.py -o /some/other/dir    # write elsewhere
    python3 sitemap.py --stdout              # print sitemap.xml, write nothing
    python3 sitemap.py --base https://reader.example.com
"""
from __future__ import annotations

import os
from datetime import datetime
from xml.sax.saxutils import escape

# Google's ceiling is 50,000 URLs / 50MB per file. Leave headroom so a book
# added between two generations can't tip a file over the limit.
MAX_URLS_PER_FILE = 45000

_URLSET_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# changefreq/priority are hints most engines ignore, but they cost ~40 bytes
# on the handful of hub pages and nothing on the 38k chapter URLs, which
# carry loc+lastmod only.
_LIBRARY_HINTS = {"changefreq": "daily", "priority": "1.0"}
_BOOK_HINTS = {"changefreq": "daily", "priority": "0.8"}


# ----------------------------------------------------------------------
# Timestamps
# ----------------------------------------------------------------------

def w3c_datetime(value) -> str | None:
    """Format a stored timestamp as a W3C datetime for <lastmod>.

    Chapter/book dates are naive server-local ISO strings (the same
    convention as chapters.published_at — see PublishMenu.localIso). A naive
    datetime's .astimezone() interprets it as local time and attaches the
    offset that was in force on THAT date, so historical rows keep the right
    UTC instant across a DST boundary. Unparseable input yields None and the
    entry is emitted without a lastmod, which is valid.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")


def _newest(*values) -> str | None:
    """The latest of several timestamps, compared as W3C strings.

    All candidates come out of w3c_datetime, so they share a fixed-width
    prefix and sort lexicographically for a given offset. Mixed offsets can
    only occur across a DST change, where the ordering error is one hour —
    immaterial for a lastmod hint.
    """
    out = [v for v in (w3c_datetime(v) for v in values) if v]
    return max(out) if out else None


# ----------------------------------------------------------------------
# URL collection
# ----------------------------------------------------------------------

def collect_urls(db, base_url: str, include_chapters: bool = True) -> list[dict]:
    """Build the full URL list for the public reader.

    Mirrors the public API's visibility rules exactly: `is_public` books
    only (web/api/public.py::_get_public_book) and `published_only=True`
    chapters, so a draft or a not-yet-due scheduled chapter is never
    advertised to a crawler before the reader can open it.
    """
    base = base_url.rstrip("/")
    books = [b for b in db.list_books() if b.get("is_public", True)]

    entries: list[dict] = []
    # Placeholder for /library — its lastmod is the newest chapter on the
    # site, which we only know after the book loop below.
    library_lastmod = None

    for b in books:
        book_id = b["id"]
        book_lastmod = _newest(b.get("last_published_date"), b.get("modified_date"),
                               b.get("created_date"))
        entries.append({
            "loc": f"{base}/library/book/{book_id}",
            "lastmod": book_lastmod,
            **_BOOK_HINTS,
        })
        # last_published_date already excludes drafts and pending schedules.
        library_lastmod = max(filter(None, [library_lastmod,
                                            w3c_datetime(b.get("last_published_date"))]),
                              default=library_lastmod)

        if not include_chapters:
            continue
        for ch in db.list_chapters(book_id, published_only=True):
            entries.append({
                "loc": f"{base}/library/read/{book_id}/{ch['chapter']}",
                "lastmod": _newest(ch.get("translation_date"), ch.get("published_at")),
            })

    return [{"loc": f"{base}/library", "lastmod": library_lastmod, **_LIBRARY_HINTS}] + entries


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------

def render_urlset(entries) -> bytes:
    """Render a <urlset>. Built as a string rather than through ElementTree:
    a 38k-element tree costs tens of MB in the admin process, and every value
    here is either a URL or a timestamp."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n',
           f'<urlset xmlns="{_URLSET_NS}">\n']
    for e in entries:
        out.append("  <url>\n")
        out.append(f"    <loc>{escape(e['loc'])}</loc>\n")
        if e.get("lastmod"):
            out.append(f"    <lastmod>{e['lastmod']}</lastmod>\n")
        if e.get("changefreq"):
            out.append(f"    <changefreq>{e['changefreq']}</changefreq>\n")
        if e.get("priority"):
            out.append(f"    <priority>{e['priority']}</priority>\n")
        out.append("  </url>\n")
    out.append("</urlset>\n")
    return "".join(out).encode("utf-8")


def render_index(parts, base_url: str, lastmod: str | None = None) -> bytes:
    """Render a <sitemapindex> naming each part file at the site root.

    The parts must be served from the same directory as the index (or above
    it) — a sitemap may only list URLs at or below its own path.
    """
    base = base_url.rstrip("/")
    lastmod = lastmod or w3c_datetime(datetime.now())
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n',
           f'<sitemapindex xmlns="{_URLSET_NS}">\n']
    for name in parts:
        out.append("  <sitemap>\n")
        out.append(f"    <loc>{escape(f'{base}/{name}')}</loc>\n")
        out.append(f"    <lastmod>{lastmod}</lastmod>\n")
        out.append("  </sitemap>\n")
    out.append("</sitemapindex>\n")
    return "".join(out).encode("utf-8")


def build(db, base_url: str, include_chapters: bool = True) -> dict:
    """Generate the whole sitemap.

    Returns {"files": [(filename, bytes), …], "url_count": int,
             "index": bool} — files[0] is always sitemap.xml, the file to
    submit, whether it is a urlset or an index over the rest.
    """
    entries = collect_urls(db, base_url, include_chapters=include_chapters)

    if len(entries) <= MAX_URLS_PER_FILE:
        return {"files": [("sitemap.xml", render_urlset(entries))],
                "url_count": len(entries), "index": False}

    chunks = [entries[i:i + MAX_URLS_PER_FILE]
              for i in range(0, len(entries), MAX_URLS_PER_FILE)]
    files = [(f"sitemap-{i}.xml", render_urlset(c)) for i, c in enumerate(chunks, 1)]
    index = render_index([name for name, _ in files], base_url)
    return {"files": [("sitemap.xml", index)] + files,
            "url_count": len(entries), "index": True}


def resolve_output_dir(config=None) -> str:
    """Directory the generated files are served from.

    Overridable with SITEMAP_DIR; otherwise <project root>/sitemaps. Kept
    out of web/frontend/dist deliberately — `npm run build` empties that
    directory, which would silently delete a live sitemap on the next
    frontend deploy.
    """
    env = os.getenv("SITEMAP_DIR", "").strip()
    if env:
        return env.rstrip("/")
    root = getattr(config, "script_dir", None) if config else None
    root = root or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "sitemaps")


def write_files(files, out_dir: str) -> list[str]:
    """Write the generated files into out_dir, atomically, and drop stale ones.

    Two properties matter, because a crawler may fetch mid-rebuild:
      - each file lands via a temp file + os.replace, so a fetch sees either
        the old bytes or the new ones, never a half-written document;
      - sitemap*.xml files this build did not produce are deleted, so a
        catalog that shrinks back under MAX_URLS_PER_FILE doesn't leave
        orphaned sitemap-2.xml parts being served (and crawled) forever.
    """
    os.makedirs(out_dir, exist_ok=True)
    written = []
    keep = set()
    for name, data in files:
        path = os.path.join(out_dir, name)
        tmp = path + ".partial"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        written.append(path)
        keep.add(name)

    for existing in os.listdir(out_dir):
        if (existing.startswith("sitemap") and existing.endswith((".xml", ".xml.partial"))
                and existing not in keep):
            try:
                os.unlink(os.path.join(out_dir, existing))
            except OSError:
                pass
    return written


def resolve_base_url(config=None) -> str:
    """The public reader's origin, e.g. https://reader.boondollars.com.

    SITE_BASE_URL is the same value the reply-notification emails use to
    build absolute chapter links. Never derive it from the request: the
    sitemap is generated on the ADMIN host, and stamping every URL with
    t9.boondollars.com would advertise a login wall to Google.
    """
    for candidate in (getattr(config, "site_base_url", "") if config else "",
                      os.getenv("SITE_BASE_URL", ""),
                      os.getenv("READER_BASE_URL", "")):
        if candidate and candidate.strip():
            return candidate.strip().rstrip("/")
    return ""


def _main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate the public reader sitemap.")
    parser.add_argument("-o", "--output-dir",
                        help="Write here instead of the served directory (SITEMAP_DIR)")
    parser.add_argument("--stdout", action="store_true",
                        help="Print sitemap.xml and write nothing")
    parser.add_argument("--base", help="Public base URL (default: SITE_BASE_URL)")
    parser.add_argument("--no-chapters", action="store_true",
                        help="Library and book pages only")
    args = parser.parse_args()

    from config import TranslationConfig
    from logger import Logger
    from database import DatabaseManager

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))
    base = args.base or resolve_base_url(config)
    if not base:
        parser.error("No base URL: pass --base or set SITE_BASE_URL in .env")

    result = build(db, base, include_chapters=not args.no_chapters)
    if args.stdout:
        import sys
        sys.stdout.buffer.write(result["files"][0][1])
        if result["index"]:
            print(f"\n[{len(result['files']) - 1} part files not printed — "
                  f"drop --stdout to write them]", file=sys.stderr)
        return

    out_dir = args.output_dir or resolve_output_dir(config)
    for path in write_files(result["files"], out_dir):
        print(f"{path}  ({os.path.getsize(path):,} bytes)")
    print(f"{result['url_count']:,} URLs")


if __name__ == "__main__":
    _main()
