"""
FastAPI application for the web GUI.

Run with:
    python web/app.py
or:
    uvicorn web.app:app --reload --app-dir ..
"""
import sys
import os

# Make the project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import TranslationConfig
from logger import Logger
from database import DatabaseManager
from translation_engine import TranslationEngine

from web.services.job_manager import job_manager
from web.services.web_interface import WebInterface
from web.api import translation, books, entities, queue_api, settings_api, dictionary_api, activity_log_api
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

    app = FastAPI(title="T9 Translation GUI", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth — must be added after CORS so CORS headers are still set on 401s
    configure_auth()
    app.add_middleware(AuthMiddleware)

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

    # Serve built frontend (production)
    static_dir = os.path.join(os.path.dirname(__file__), "frontend", "dist")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

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
    )
