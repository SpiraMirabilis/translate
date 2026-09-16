"""
Shared FastAPI application factory for the two T9 web processes.

- ``create_app()``                 — the full admin app (web.app:app, port 8000):
  translation engine, job manager, WebSocket, auth, all admin API routers,
  plus the public reader surface.
- ``create_app(public_only=True)`` — the public reader app (web.public_app:app,
  port 8001): ONLY the unauthenticated public surface (/api/public/*,
  /api/health, /simple, the Library/Reader SPA routes and static assets).
  No admin routes exist in this process at all, so the reverse proxy in
  front of it needs no path whitelist, and heavy admin/translation work
  in the main process cannot slow public serving.

Both processes share the same database (MySQL; per-process pools) and the
same built frontend in web/frontend/dist. Runtime settings propagate across
processes via settings_store's mtime-based reload of settings.json.
"""
import os
import re
from html import escape

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, HTMLResponse, JSONResponse, PlainTextResponse

import sitemap as sitemap_builder
from config import TranslationConfig
from logger import Logger
from database import DatabaseManager

from web.services.view_logger import ViewLogger
from web.api import health, public, recommendations_public, comments_public

# ------------------------------------------------------------------
# Application setup
# ------------------------------------------------------------------

def create_app(config=None, logger=None, public_only: bool = False) -> FastAPI:
    config = config or TranslationConfig()
    logger = logger or Logger(config)
    entity_manager = DatabaseManager(config, logger, strict_writes=True)

    # Public surface — wired in both processes (the admin UI's Library and
    # Reader pages call /api/public/* too).
    view_logger = ViewLogger(entity_manager)
    view_logger.start()
    public.init(entity_manager, config, view_logger)
    recommendations_public.init(entity_manager)
    comments_public.init(entity_manager, config)
    health.init(entity_manager)

    if not public_only:
        # Admin machinery — translation engine, job manager, module hooks.
        # Imported lazily so the public process never loads any of it.
        from translation_engine import TranslationEngine
        from web.services.job_manager import job_manager
        from web.services.web_interface import WebInterface
        from web.api import (
            translation, books, entities, queue_api, settings_api,
            dictionary_api, activity_log_api, api_calls, wordpress_api,
            recommendations_admin, reader_stats_api, comments_admin,
            revisions, grammar, mail_ingest, deps,
        )

        translator = TranslationEngine(config, logger, entity_manager)
        web_interface = WebInterface(translator, entity_manager, logger, job_manager)
        job_manager.db_manager = entity_manager
        # Module activity summaries go through job_manager so they hit the DB AND
        # broadcast live over WebSocket (plain db.add_activity_log otherwise).
        from modules import set_activity_notifier
        set_activity_notifier(job_manager.log_activity)

        deps.init(entity_manager)
        translation.init(web_interface, job_manager)
        books.init(entity_manager, translator, logger)
        revisions.init(entity_manager)
        grammar.init(entity_manager, config)
        entities.init(entity_manager, translator)
        queue_api.init(entity_manager, job_manager, web_interface)
        settings_api.init(config)
        settings_api.init_db(entity_manager)
        dictionary_api.init(entity_manager, translator)
        activity_log_api.init(entity_manager)
        api_calls.init(entity_manager)
        wordpress_api.init(config, entity_manager, job_manager)
        recommendations_admin.init(entity_manager)
        mail_ingest.init(entity_manager)
        reader_stats_api.init(entity_manager)
        comments_admin.init(entity_manager)

    if public_only:
        # No docs/openapi on the public surface — nothing to advertise.
        site = getattr(config, "public_site_name", None) or "Public"
        app = FastAPI(title=f"{site} Reader", version="1.0.0",
                      docs_url=None, redoc_url=None, openapi_url=None)
    else:
        app = FastAPI(title=f"{config.site_name} Translation GUI", version="1.0.0")

    # Flush buffered reader views on clean shutdown (best-effort; up to a few
    # seconds of views are accepted-lost on a hard kill).
    app.add_event_handler("shutdown", view_logger.stop)

    if not public_only:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Static asset cache headers
    class CacheHeaderMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            path = request.url.path
            ok = response.status_code == 200
            if path.startswith("/assets/") and ok:
                # Vite-built assets have content hashes — cache for 1 year
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif path.startswith("/assets/"):
                # Never let a failed asset fetch inherit the immutable header.
                # A deploy that serves index.html before its hashed chunks land
                # would otherwise pin the 404 in Cloudflare and mod_cache_disk
                # for a year, and no purge of either could be trusted to stick.
                response.headers["Cache-Control"] = "no-store"
            elif path.endswith((".ico", ".png", ".jpg", ".svg", ".webp", ".woff2", ".woff")):
                # Other static files — cache for 1 day
                response.headers["Cache-Control"] = "public, max-age=86400" if ok else "no-store"
            elif path == "/" or (not path.startswith("/api/") and not path.startswith("/ws") and "." not in path.split("/")[-1]):
                # SPA HTML pages — always revalidate
                response.headers["Cache-Control"] = "no-cache"
            return response

    app.add_middleware(CacheHeaderMiddleware)

    # WordPress-style feed URL fallback: some RSS readers probe /?feed=rss2
    # at the site root before trying link-rel autodiscovery. Catch anywhere
    # outside /api/ and redirect to our canonical feed.
    class FeedRedirectMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if not request.url.path.startswith("/api/"):
                feed = request.query_params.get("feed", "").lower()
                if feed in ("rss", "rss2", "atom", "rdf"):
                    return RedirectResponse("/api/public/feed.rss", status_code=302)
            return await call_next(request)

    app.add_middleware(FeedRedirectMiddleware)

    if public_only:
        # The only gate the public process needs: when the public library is
        # switched off in Settings, the whole public API disappears. The
        # setting is read live (settings_store reloads settings.json on
        # mtime change), so the admin process flipping it takes effect here
        # without a restart. /api/health stays open for the watchdog.
        class PublicGateMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.url.path
                if path.startswith("/api/public/") or path == "/simple":
                    import settings_store
                    if not settings_store.get("public_library", True):
                        return JSONResponse({"detail": "Not found"}, status_code=404)
                return await call_next(request)

        app.add_middleware(PublicGateMiddleware)

        # The SPA polls /api/auth/status on load (including on Library and
        # Reader pages). Answer it here so the poll gets a clean "not
        # authenticated" instead of a 404 — historically noisy failures on
        # this path tripped fail2ban. There is deliberately NO login
        # endpoint in this process.
        @app.get("/api/auth/status")
        async def auth_status_stub():
            import settings_store
            return {
                "authenticated": False,
                "auth_required": True,
                "public_library": bool(settings_store.get("public_library", True)),
            }
    else:
        # Auth — must be added after CORS so CORS headers are still set on 401s
        from web.auth import configure_auth, AuthMiddleware, router as auth_router
        configure_auth()
        app.add_middleware(AuthMiddleware)

        if not os.getenv("CF_TURNSTILE_SECRET_KEY", "").strip():
            logger.warning(
                "=" * 62 + "\n"
                "TURNSTILE DISABLED — CF_TURNSTILE_SECRET_KEY is not set.\n"
                "Comment/recommendation spam protection is limited to rate\n"
                "limiting only. Set TURNSTILE_REQUIRED=1 to fail closed instead.\n"
                + "=" * 62
            )

        # Auth routes (login/logout/status) — before other API routes
        app.include_router(auth_router)

        # Admin API routes
        from web.api import (
            translation, books, entities, queue_api, settings_api,
            dictionary_api, activity_log_api, api_calls, wordpress_api,
            recommendations_admin, reader_stats_api, comments_admin,
            revisions, grammar, mail_ingest,
        )
        app.include_router(translation.router)
        app.include_router(books.router)
        app.include_router(revisions.router)
        app.include_router(grammar.router)
        app.include_router(entities.router)
        app.include_router(queue_api.router)
        app.include_router(settings_api.router)
        app.include_router(dictionary_api.router)
        app.include_router(activity_log_api.router)
        app.include_router(api_calls.router)
        app.include_router(wordpress_api.router)
        app.include_router(recommendations_admin.router)
        app.include_router(mail_ingest.router)
        app.include_router(reader_stats_api.router)
        app.include_router(comments_admin.router)

    # Public API routes — both processes
    app.include_router(health.router)
    app.include_router(public.router)
    app.include_router(recommendations_public.router)
    app.include_router(comments_public.router)

    # ------------------------------------------------------------------
    # robots.txt
    #
    # Registered here (before the SPA catch-all) so it isn't swallowed by
    # the index.html route. Served by both processes, with different bodies:
    # the admin host is disallowed wholesale, the public reader allows the
    # SPA but keeps crawlers off the generated ebook downloads.
    #
    # robots.txt matches URL paths, not rendered links, so blocking the API
    # paths is what actually protects them — the reader's download buttons
    # and /simple both point at the same /api/public/... URLs. The SPA book
    # and chapter routes stay crawlable. `*` inside a path is the widely
    # supported wildcard extension (Google/Bing/etc.); the download handlers
    # also send X-Robots-Tag as a backstop for crawlers that ignore it.
    # ------------------------------------------------------------------
    _ROBOTS_ADMIN = "User-agent: *\nDisallow: /\n"

    _ROBOTS_PUBLIC = (
        "User-agent: *\n"
        "\n"
        "# Generated ebook downloads: a crawl miss can kick off a multi-minute\n"
        "# EPUB/AZW3 build, and the files are not content we want indexed.\n"
        "Disallow: /api/public/books/*/epub\n"
        "Disallow: /api/public/books/*/azw3\n"
        "\n"
        "# Same downloads as reached from the plain-HTML book list.\n"
        "Disallow: /simple\n"
    )

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots_txt():
        if not public_only:
            return PlainTextResponse(_ROBOTS_ADMIN)
        import settings_store
        if not settings_store.get("public_library", True):
            # Library switched off — the whole public surface 404s, so don't
            # invite crawling of it.
            return PlainTextResponse(_ROBOTS_ADMIN)
        body = _ROBOTS_PUBLIC
        # Advertise the sitemap only once one exists on disk: a Sitemap: line
        # pointing at a 404 is a standing error in Search Console. The cron
        # rebuild (python3 sitemap.py) is what makes it appear.
        base = sitemap_builder.resolve_base_url(config)
        if base and os.path.isfile(os.path.join(_sitemap_dir, "sitemap.xml")):
            body += f"\nSitemap: {base}/sitemap.xml\n"
        return PlainTextResponse(body)

    # ------------------------------------------------------------------
    # Generated sitemap files.
    #
    # Served as plain static bytes off disk — Google only accepts a sitemap
    # it can fetch from the site itself, but generating one walks every
    # chapter row of every public book (~38k), which no crawler may trigger.
    # A cron job runs `python3 sitemap.py` (see sitemap.py::write_files,
    # which writes atomically) and this route only reads the result.
    # Registered before the SPA catch-all so /sitemap.xml isn't swallowed by
    # index.html, and before /simple for the same reason.
    # ------------------------------------------------------------------
    _sitemap_dir = sitemap_builder.resolve_output_dir(config)
    _sitemap_name_re = re.compile(r"^sitemap(?:-\d{1,4})?\.xml$")

    @app.get("/sitemap.xml", include_in_schema=False)
    @app.get("/sitemap-{part}.xml", include_in_schema=False)
    async def sitemap_file(part: str = None):
        from fastapi.responses import FileResponse
        name = "sitemap.xml" if part is None else f"sitemap-{part}.xml"
        # part comes straight off the URL: pin it to the generated shape so
        # nothing resembling a path can be assembled here.
        if not _sitemap_name_re.match(name):
            return PlainTextResponse("Not found", status_code=404)
        if public_only:
            import settings_store
            if not settings_store.get("public_library", True):
                return PlainTextResponse("Not found", status_code=404)
        path = os.path.join(_sitemap_dir, name)
        if not os.path.isfile(path):
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(path, media_type="application/xml",
                            headers={"Cache-Control": "public, max-age=3600"})

    # ------------------------------------------------------------------
    # Plain-HTML book list for primitive / e-ink browsers.
    #
    # No SPA, no JS, minimal CSS — a bare <ul> of public books linking to
    # the existing public EPUB endpoint. Registered here (before the SPA
    # catch-all below) so /simple isn't swallowed by the index.html route.
    # Lives outside /api so the auth middleware treats it as a public
    # static route; the EPUB links carry a same-host Referer, which passes
    # the public origin check.
    # ------------------------------------------------------------------
    @app.get("/simple", response_class=HTMLResponse)
    async def simple_book_list():
        # Honour the same public-library gate as /api/public/* — otherwise
        # turning the public library off still leaks the catalog at /simple.
        if public_only:
            import settings_store
            allowed = bool(settings_store.get("public_library", True))
        else:
            from web.auth import is_public_library, auth_required
            allowed = not auth_required() or is_public_library()
        if not allowed:
            return HTMLResponse(
                "<!DOCTYPE html><title>Not found</title><p>Not found.</p>",
                status_code=404,
            )

        import azw3
        site = getattr(config, "public_site_name", None) or "Library"
        azw3_on = azw3.is_available()  # only show the AZW3 column when we can build them

        rows = []
        for b in entity_manager.list_books(order_by="popular"):
            if not b.get("is_public", True):
                continue
            if not b.get("published_chapter_count", 0):
                continue  # no published chapters -> ebook would 404
            bid = b["id"]
            title = escape(str(b.get("title") or "Untitled"))
            author = escape(str(b.get("author") or ""))
            count = b.get("published_chapter_count", 0)
            meta_parts = []
            if author:
                meta_parts.append(f"by {author}")
            meta_parts.append(f"{count} chapter{'s' if count != 1 else ''}")
            meta = " &mdash; ".join(meta_parts)

            cells = [
                f'<td class="title">{title}<div class="meta">{meta}</div></td>',
                f'<td class="dl"><a href="/api/public/books/{bid}/epub">EPUB</a></td>',
            ]
            if azw3_on:
                cells.append(f'<td class="dl"><a href="/api/public/books/{bid}/azw3">AZW3</a></td>')
            rows.append("<tr>" + "".join(cells) + "</tr>")

        ncols = 3 if azw3_on else 2
        if rows:
            head_cells = "<th>Title</th><th>EPUB</th>" + ("<th>AZW3</th>" if azw3_on else "")
            body = (f"<thead><tr>{head_cells}</tr></thead>\n<tbody>\n"
                    + "\n".join(rows) + "\n</tbody>")
        else:
            body = f'<tbody><tr><td colspan="{ncols}">No books available.</td></tr></tbody>'
        table = f'<table>\n{body}\n</table>'

        kinds = "EPUB &amp; AZW3 (Kindle)" if azw3_on else "EPUB"
        site_esc = escape(site)
        html = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n<head>\n'
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{site_esc} &mdash; eBook Library</title>\n"
            "<style>\n"
            "body{font-family:Georgia,serif;margin:1em;max-width:44em;color:#000;background:#fff;}\n"
            "h1{font-size:1.4em;border-bottom:2px solid #000;padding-bottom:.3em;}\n"
            "table{width:100%;border-collapse:collapse;}\n"
            "th,td{text-align:left;padding:.5em .4em;border-bottom:1px solid #999;vertical-align:top;}\n"
            "th{border-bottom:2px solid #000;font-size:.9em;}\n"
            "td.title{font-size:1.05em;}\n"
            "td.dl{white-space:nowrap;}\n"
            "a{text-decoration:underline;color:#000;}\n"
            ".meta{font-size:.8em;color:#333;margin-top:.2em;}\n"
            "</style>\n</head>\n<body>\n"
            f"<h1>{site_esc} &mdash; Download eBooks</h1>\n"
            f"<p>Download books as {kinds}.</p>\n"
            f"{table}\n"
            "</body>\n</html>\n"
        )
        return HTMLResponse(html)

    # Serve built frontend (production)
    static_dir = os.path.join(os.path.dirname(__file__), "frontend", "dist")
    if os.path.isdir(static_dir):
        from fastapi.responses import FileResponse

        index_html = os.path.join(static_dir, "index.html")
        notfound_html = os.path.join(static_dir, "404.html")

        # Serve actual static assets (JS, CSS, images, etc.)
        app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="static-assets")

        # Per-book RSS autodiscovery: feed readers fetch the page URL server-side
        # and don't run JS, so the React-injected <link> is invisible to them.
        # For book-detail and chapter-reader routes we splice a per-book
        # <link rel="alternate"> into the served index.html so non-JS crawlers
        # discover the book's feed.
        _book_path_re = re.compile(
            r"^(?P<kind>library/book|library/read|read)/(?P<book>\d+)(?:/(?P<chapter>\d+))?/?$")
        _library_path_re = re.compile(r"^library/?$")
        # The global-feed autodiscovery tag baked into index.html. On book
        # and chapter pages it is REPLACED by the book's own feed tag, so
        # single-feed autodiscovery tools (e.g. Novel Updates) can't pick
        # the site-wide feed by mistake.
        _global_feed_re = re.compile(
            r'<link rel="alternate" type="application/rss\+xml"[^>]*href="/api/public/feed\.rss"[^>]*>')
        with open(index_html, "r", encoding="utf-8") as fh:
            _index_html_text = fh.read()

        def _canonical_url(kind: str, book_id: int, chapter: int | None) -> str | None:
            """Absolute canonical URL for a reader route, or None if unknown.

            The reader is reachable at two path shapes for the same page —
            /read/{id}/{n} (what the RSS feeds link to) and
            /library/read/{id}/{n} (what the site itself links to, and what
            the sitemap lists). Without a canonical those are two URLs for
            one chapter. The /library form wins everywhere.

            Absolute, because a canonical is only useful across hosts: the
            admin host serves these same paths behind a login wall, and the
            tag points Google at the reader host either way. With no
            SITE_BASE_URL configured we emit nothing rather than guess.
            """
            base = sitemap_builder.resolve_base_url(config)
            if not base:
                return None
            if kind == "library/book" or chapter is None:
                # A chapterless /read/{id} or /library/read/{id} lands on the
                # book (the reader picks a chapter client-side and rewrites
                # the URL), so the book page is the canonical target.
                return f"{base}/library/book/{book_id}"
            return f"{base}/library/read/{book_id}/{chapter}"

        def _with_head_tags(html: str, tags: list) -> str:
            tags = [t for t in tags if t]
            if not tags:
                return html
            return html.replace("</head>", "".join(tags) + "</head>", 1)

        def _canonical_tag(url: str | None) -> str | None:
            return f'<link rel="canonical" href="{escape(url, quote=True)}" />' if url else None

        def _index_for_book(kind: str, book_id: int, chapter: int | None):
            """index.html with this book's RSS autodiscovery + canonical tags.

            Both are invisible to non-JS clients otherwise: feed readers and
            crawlers fetch the URL server-side and never run the React that
            would inject them.
            """
            book = entity_manager.get_book(book_id=book_id)
            if not book or not book.get("is_public", True):
                return None
            title = escape(f"{book.get('title', 'Book')} — New Chapters", quote=True)
            # Chapter pages advertise a chapter-windowed feed (?around=N,
            # chapters N-50..N+100) so a feed reader that discovers the feed
            # from an older chapter URL still sees everything from there on.
            href = f"/api/public/books/{book_id}/feed.rss"
            if chapter is not None:
                href += f"?around={chapter}"
            tag = (f'<link rel="alternate" type="application/rss+xml" '
                   f'title="{title}" href="{href}" />')
            html, n = _global_feed_re.subn(tag.replace("\\", "\\\\"), _index_html_text, count=1)
            if n == 0:  # index.html lost its global tag — just add ours
                html = _with_head_tags(_index_html_text, [tag])
            return _with_head_tags(html, [_canonical_tag(_canonical_url(kind, book_id, chapter))])

        def _index_for_library():
            base = sitemap_builder.resolve_base_url(config)
            if not base:
                return None
            return _with_head_tags(_index_html_text, [_canonical_tag(f"{base}/library")])

        # The public process only serves the reader SPA routes; everything
        # else gets the themed 404 page. The admin process serves index.html
        # for any unknown path (classic SPA catch-all).
        _PUBLIC_SPA_PREFIXES = ("library", "read")

        def _is_public_spa_path(full_path: str) -> bool:
            head = full_path.split("/", 1)[0]
            return head in _PUBLIC_SPA_PREFIXES

        # SPA catch-all: any non-API path serves index.html for client-side routing
        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            # Serve real files (e.g. favicon.ico) if they exist
            file_path = os.path.join(static_dir, full_path)
            if full_path and os.path.isfile(file_path):
                return FileResponse(file_path)
            m = _book_path_re.match(full_path)
            if m:
                chapter = int(m.group("chapter")) if m.group("chapter") else None
                html = _index_for_book(m.group("kind"), int(m.group("book")), chapter)
                if html is not None:
                    return HTMLResponse(html)
            elif _library_path_re.match(full_path):
                html = _index_for_library()
                if html is not None:
                    return HTMLResponse(html)
            if public_only:
                if not full_path:
                    # Direct hit on the process root (Apache normally
                    # rewrites / to /library before proxying).
                    return RedirectResponse("/library", status_code=302)
                if not _is_public_spa_path(full_path):
                    if os.path.isfile(notfound_html):
                        return FileResponse(notfound_html, status_code=404)
                    return HTMLResponse("<h1>Not found</h1>", status_code=404)
            return FileResponse(index_html)

    return app
