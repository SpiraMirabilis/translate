"""
FastAPI application for the web GUI.

Run with:
    python web/app.py
or:
    uvicorn web.app:app --reload --app-dir ..
"""
import sys
import os
import re
from html import escape

# Make the project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, HTMLResponse

from config import TranslationConfig
from logger import Logger
from database import DatabaseManager
from translation_engine import TranslationEngine

from web.services.job_manager import job_manager
from web.services.web_interface import WebInterface
from web.api import translation, books, entities, queue_api, settings_api, dictionary_api, activity_log_api, api_calls, wordpress_api, health, public, recommendations_public, recommendations_admin, reader_stats_api, comments_public, comments_admin
from web.auth import configure_auth, AuthMiddleware, router as auth_router

# ------------------------------------------------------------------
# Application setup
# ------------------------------------------------------------------

def create_app() -> FastAPI:
    config = TranslationConfig()
    logger = Logger(config)
    entity_manager = DatabaseManager(config, logger)
    translator = TranslationEngine(config, logger, entity_manager)
    web_interface = WebInterface(translator, entity_manager, logger, job_manager)
    job_manager.db_manager = entity_manager

    # Wire up API modules
    translation.init(web_interface, job_manager)
    books.init(entity_manager, translator, logger)
    entities.init(entity_manager, translator)
    queue_api.init(entity_manager, job_manager, web_interface)
    settings_api.init(config)
    settings_api.init_db(entity_manager)
    dictionary_api.init(entity_manager, translator)
    activity_log_api.init(entity_manager)
    api_calls.init(entity_manager)
    wordpress_api.init(config, entity_manager, job_manager)
    health.init(entity_manager)
    public.init(entity_manager, config)
    recommendations_public.init(entity_manager)
    recommendations_admin.init(entity_manager)
    reader_stats_api.init(entity_manager)
    comments_public.init(entity_manager, config)
    comments_admin.init(entity_manager)

    app = FastAPI(title=f"{config.site_name} Translation GUI", version="1.0.0")

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
            if path.startswith("/assets/"):
                # Vite-built assets have content hashes — cache for 1 year
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif path.endswith((".ico", ".png", ".jpg", ".svg", ".webp", ".woff2", ".woff")):
                # Other static files — cache for 1 day
                response.headers["Cache-Control"] = "public, max-age=86400"
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

    # Auth — must be added after CORS so CORS headers are still set on 401s
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

    # API routes
    app.include_router(translation.router)
    app.include_router(books.router)
    app.include_router(entities.router)
    app.include_router(queue_api.router)
    app.include_router(settings_api.router)
    app.include_router(dictionary_api.router)
    app.include_router(activity_log_api.router)
    app.include_router(api_calls.router)
    app.include_router(wordpress_api.router)
    app.include_router(health.router)
    app.include_router(public.router)
    app.include_router(recommendations_public.router)
    app.include_router(recommendations_admin.router)
    app.include_router(reader_stats_api.router)
    app.include_router(comments_public.router)
    app.include_router(comments_admin.router)

    # Serve built frontend (production)
    static_dir = os.path.join(os.path.dirname(__file__), "frontend", "dist")
    if os.path.isdir(static_dir):
        from fastapi.responses import FileResponse

        index_html = os.path.join(static_dir, "index.html")

        # Serve actual static assets (JS, CSS, images, etc.)
        app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="static-assets")

        # Per-book RSS autodiscovery: feed readers fetch the page URL server-side
        # and don't run JS, so the React-injected <link> is invisible to them.
        # For the book-detail route we splice a per-book <link rel="alternate">
        # into the served index.html so non-JS crawlers discover the book's feed.
        _book_path_re = re.compile(r"^library/book/(\d+)/?$")
        with open(index_html, "r", encoding="utf-8") as fh:
            _index_html_text = fh.read()

        def _index_with_book_feed(book_id: int):
            book = entity_manager.get_book(book_id=book_id)
            if not book or not book.get("is_public", True):
                return None
            title = escape(f"{book.get('title', 'Book')} — New Chapters", quote=True)
            tag = (f'<link rel="alternate" type="application/rss+xml" '
                   f'title="{title}" href="/api/public/books/{book_id}/feed.rss" />')
            return _index_html_text.replace("</head>", tag + "</head>", 1)

        # SPA catch-all: any non-API path serves index.html for client-side routing
        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            # Serve real files (e.g. favicon.ico) if they exist
            file_path = os.path.join(static_dir, full_path)
            if full_path and os.path.isfile(file_path):
                return FileResponse(file_path)
            m = _book_path_re.match(full_path)
            if m:
                html = _index_with_book_feed(int(m.group(1)))
                if html is not None:
                    return HTMLResponse(html)
            return FileResponse(index_html)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        app_dir=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
