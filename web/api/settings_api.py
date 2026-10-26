"""
Settings and provider configuration endpoints.
"""
import os
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
import settings_store
from settings_store import persist_env
from providers import get_factory

router = APIRouter(prefix="/api/settings")

_config = None


def init(config):
    global _config
    _config = config


# ------------------------------------------------------------------
# Providers
# ------------------------------------------------------------------

@router.get("/providers")
def list_providers():
    factory = get_factory()
    providers = []
    for name, cfg in factory.config["providers"].items():
        env_var = cfg.get("api_key_env", "")
        has_key = bool(os.getenv(env_var)) if env_var else False
        providers.append({
            "name": name,
            "default_model": cfg.get("default_model", ""),
            "api_key_env": env_var,
            "has_key": has_key,
            "models": cfg.get("models", []),
            "max_chars": cfg.get("max_chars", 5000),
        })
    return {"providers": providers}


class ApiKeyRequest(BaseModel):
    api_key: str


@router.post("/providers/{provider_name}/key")
def set_api_key(provider_name: str, req: ApiKeyRequest):
    factory = get_factory()
    resolved = factory._resolve_provider_name(provider_name)
    if resolved not in factory.config["providers"]:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name}")

    env_var = factory.config["providers"][resolved].get("api_key_env")
    if not env_var:
        raise HTTPException(status_code=400, detail="This provider has no API key variable.")

    os.environ[env_var] = req.api_key
    persist_env(env_var, req.api_key)
    return {"status": "ok", "env_var": env_var}


@router.post("/providers/{provider_name}/test")
def test_api_key(provider_name: str):
    """Quick test: try to instantiate the provider (checks key format / connectivity)."""
    try:
        factory = get_factory()
        resolved = factory._resolve_provider_name(provider_name)
        provider = factory.create_provider(resolved)
        cfg = factory.config["providers"][resolved]
        model = cfg.get("default_model", "")
        # Try a tiny completion
        response = provider.chat_completion(
            messages=[{"role": "user", "content": "Say 'ok' in one word."}],
            model=model,
            temperature=0,
        )
        content = provider.get_response_content(response)
        return {"status": "ok", "response": content[:100]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------
# Jev (TypeSafe System One) -- not a chat provider, so not in the
# provider cards above; see jev_client.py. jev_model "openai:decisions"
# routes the same calls to OpenAI's Decisions API on OPENAI_KEY.
# ------------------------------------------------------------------

@router.post("/typesafe-key")
def set_typesafe_key(req: ApiKeyRequest):
    import jev_client
    key = req.api_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key is empty.")
    os.environ[jev_client.KEY_ENV] = key
    persist_env(jev_client.KEY_ENV, key)
    return {"status": "ok", "env_var": jev_client.KEY_ENV}


@router.post("/typesafe-test")
def test_typesafe_key():
    """One tiny classification against the live API (whichever jev_model names)."""
    import jev_client
    try:
        ans = jev_client.choice(
            "Chapter 12: The sword flashed as Lin Feng stepped into the courtyard.",
            "Is this story narrative or an author's note?",
            {"story": "Story narrative", "note": "An author's note to readers"},
        )
    except jev_client.JevError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "response": f"{ans['choice']} (confidence {ans['confidence']:.2f})"}


# ------------------------------------------------------------------
# Scraper (scrape_worker.py) -- the bearer token for the home-server
# scraper is a secret, so it goes to .env; the worker re-reads .env on
# every call, so a new token needs no restart.
# ------------------------------------------------------------------

def _scraper_token():
    import scrape_sync
    return scrape_sync.scraper_token()


def _scraper_notify_secret():
    import scrape_sync
    return scrape_sync.notify_secret()


@router.post("/scraper-token")
def set_scraper_token(req: ApiKeyRequest):
    key = req.api_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="Token is empty.")
    os.environ["SCRAPER_TOKEN"] = key
    persist_env("SCRAPER_TOKEN", key)
    return {"status": "ok", "env_var": "SCRAPER_TOKEN"}


@router.post("/scraper-test")
def test_scraper():
    """Reach the scraper's /health and an authenticated route."""
    import scrape_sync
    from scraper_client import ScraperError
    sc = scrape_sync.make_client(timeout=8)
    try:
        health = sc._req("GET", "/health")[1]
        sc._req("GET", "/jobs?limit=1")
    except ScraperError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Scraper unreachable: {e}")
    return {"status": "ok", "response": health}


# ------------------------------------------------------------------
# General settings
# ------------------------------------------------------------------

@router.get("")
def get_settings():
    from web.auth import is_public_library
    return {
        "translation_model": _config.translation_model,
        "advice_model": _config.advice_model,
        "debug_mode": _config.debug_mode,
        "public_library": is_public_library(),
        # Read from the store: the public process reads it live per request.
        "error_reports_enabled": bool(settings_store.get("error_reports_enabled", True)),
        "site_name": _config.site_name,
        "public_site_name": _config.public_site_name,
        "comment_automod_enabled": getattr(_config, "comment_automod_enabled", False),
        "comment_automod_model": getattr(_config, "comment_automod_model", "claude:claude-haiku-4-5"),
        "comment_automod_jev_confidence": settings_store.get("comment_automod_jev_confidence", 0.9),
        "pronoun_repair_model": getattr(_config, "pronoun_repair_model", "claude:claude-haiku-4-5"),
        "email_from": getattr(_config, "email_from", ""),
        "email_backend": getattr(_config, "email_backend", "ses"),
        "site_base_url": getattr(_config, "site_base_url", ""),
        "trad_to_simp": getattr(_config, "trad_to_simp", False),
        "entity_note_updates": getattr(_config, "entity_note_updates", True),
        "footnote_inline_scan": getattr(_config, "footnote_inline_scan", True),
        "repetition_guard": getattr(_config, "repetition_guard", False),
        "json_auto_repair": getattr(_config, "json_auto_repair", True),
        "claude_code_mcp_tools": getattr(_config, "claude_code_mcp_tools", False),
        "disable_content_cache": getattr(_config, "disable_content_cache", False),
        "disable_media_cache": getattr(_config, "disable_media_cache", False),
        "overload_retry_wait_seconds": getattr(
            _config, "overload_retry_wait_seconds",
            int(os.getenv("OVERLOAD_RETRY_WAIT_SECONDS", "300")),
        ),
        "json_fix_timeout_seconds": getattr(
            _config, "json_fix_timeout_seconds",
            int(os.getenv("JSON_FIX_TIMEOUT_SECONDS", "300")),
        ),
        # Read from the store, not _config: the registry consults the store
        # live, so the UI must show the value actually in force.
        "max_concurrent_translations": settings_store.get(
            "max_concurrent_translations", 3),
        "grammar_check_enabled": getattr(_config, "grammar_check_enabled", False),
        "languagetool_url": getattr(_config, "languagetool_url", "http://127.0.0.1:8081"),
        "grammar_language": getattr(_config, "grammar_language", "en-US"),
        "polish_model": getattr(_config, "polish_model", "claude:claude-sonnet-4-6"),
        # Jev: read from the store -- chapter_triage consults it live.
        "jev_model": settings_store.get("jev_model", "jev-latest"),
        "jev_chapter_conflict": settings_store.get("jev_chapter_conflict", "auto"),
        "jev_conflict_confidence": settings_store.get("jev_conflict_confidence", 0.9),
        "jev_unit_filter": settings_store.get("jev_unit_filter", True),
        "jev_unit_confidence": settings_store.get("jev_unit_confidence", 0.9),
        "has_typesafe_key": bool(os.getenv("TYPESAFE_KEY", "").strip()),
        # Scraping: read from the store -- the scrape worker consults it live.
        "scrape_enabled": settings_store.get("scrape_enabled", True),
        "scraper_url": settings_store.get("scraper_url", "http://100.65.58.103:8787"),
        "scrape_daily_hour": settings_store.get("scrape_daily_hour", 5),
        "scrape_chunk_size": settings_store.get("scrape_chunk_size", 50),
        "scrape_default_gap": settings_store.get("scrape_default_gap", 10),
        "scrape_engine": settings_store.get("scrape_engine", ""),
        "scrape_reserved_slots": settings_store.get("scrape_reserved_slots", 1),
        "scrape_notify_url": settings_store.get("scrape_notify_url", ""),
        "scraper_vnc_url": settings_store.get("scraper_vnc_url", ""),
        "has_scraper_notify_secret": bool(_scraper_notify_secret()),
        "has_scraper_token": bool(_scraper_token()),
        "has_openai_key": bool(os.getenv("OPENAI_KEY", "").strip()),
    }


class SettingsUpdate(BaseModel):
    translation_model: Optional[str] = None
    advice_model: Optional[str] = None
    debug_mode: Optional[bool] = None
    public_library: Optional[bool] = None
    error_reports_enabled: Optional[bool] = None
    site_name: Optional[str] = None
    public_site_name: Optional[str] = None
    comment_automod_enabled: Optional[bool] = None
    comment_automod_model: Optional[str] = None
    comment_automod_jev_confidence: Optional[float] = None
    pronoun_repair_model: Optional[str] = None
    email_from: Optional[str] = None
    email_backend: Optional[str] = None
    site_base_url: Optional[str] = None
    trad_to_simp: Optional[bool] = None
    entity_note_updates: Optional[bool] = None
    footnote_inline_scan: Optional[bool] = None
    repetition_guard: Optional[bool] = None
    json_auto_repair: Optional[bool] = None
    claude_code_mcp_tools: Optional[bool] = None
    disable_content_cache: Optional[bool] = None
    disable_media_cache: Optional[bool] = None
    overload_retry_wait_seconds: Optional[int] = None
    json_fix_timeout_seconds: Optional[int] = None
    max_concurrent_translations: Optional[int] = None
    grammar_check_enabled: Optional[bool] = None
    languagetool_url: Optional[str] = None
    grammar_language: Optional[str] = None
    polish_model: Optional[str] = None
    jev_model: Optional[str] = None
    jev_chapter_conflict: Optional[str] = None
    jev_conflict_confidence: Optional[float] = None
    jev_unit_filter: Optional[bool] = None
    jev_unit_confidence: Optional[float] = None
    scrape_enabled: Optional[bool] = None
    scraper_url: Optional[str] = None
    scrape_daily_hour: Optional[int] = None
    scrape_chunk_size: Optional[int] = None
    scrape_default_gap: Optional[int] = None
    scrape_engine: Optional[str] = None
    scrape_reserved_slots: Optional[int] = None
    scrape_notify_url: Optional[str] = None
    scraper_vnc_url: Optional[str] = None


@router.put("")
def update_settings(req: SettingsUpdate):
    # Only forward keys the caller actually set, and only those known to the
    # store. settings_store.update() handles JSON persistence + os.environ sync.
    updates = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
    if "jev_chapter_conflict" in updates and updates["jev_chapter_conflict"] not in ("off", "suggest", "auto"):
        raise HTTPException(status_code=400, detail="jev_chapter_conflict must be off, suggest or auto")
    for key in ("jev_conflict_confidence", "comment_automod_jev_confidence", "jev_unit_confidence"):
        if key in updates and not 0 < updates[key] <= 1:
            raise HTTPException(status_code=400, detail=f"{key} must be in (0, 1]")
    if "scrape_daily_hour" in updates and not 0 <= updates["scrape_daily_hour"] <= 23:
        raise HTTPException(status_code=400, detail="scrape_daily_hour must be 0-23")
    if "scrape_chunk_size" in updates and not 1 <= updates["scrape_chunk_size"] <= 500:
        raise HTTPException(status_code=400, detail="scrape_chunk_size must be 1-500")
    for key in ("scrape_notify_url", "scraper_vnc_url"):
        if key in updates:
            updates[key] = updates[key].strip()
            if updates[key] and not re.match(r"^https?://", updates[key]):
                raise HTTPException(status_code=400, detail=f"{key} must start with http:// or https://")
    if "scrape_engine" in updates and updates["scrape_engine"] not in ("", "patchright", "extension"):
        raise HTTPException(status_code=400, detail="scrape_engine must be blank, patchright or extension")
    if "scrape_reserved_slots" in updates and updates["scrape_reserved_slots"] < 0:
        raise HTTPException(status_code=400, detail="scrape_reserved_slots must be >= 0")
    if "scrape_default_gap" in updates and updates["scrape_default_gap"] < 0:
        raise HTTPException(status_code=400, detail="scrape_default_gap must be >= 0")
    if updates:
        settings_store.update(updates)
    # Mirror onto the in-memory _config object so callers reading _config.X see
    # the new values without a restart.
    for key, val in updates.items():
        setattr(_config, key, val)
    # web.auth caches public_library in a module-level variable; refresh it.
    if "public_library" in updates:
        import web.auth as auth_mod
        auth_mod._public_library = updates["public_library"]
    return {"status": "ok"}


# ------------------------------------------------------------------
# Database utilities
# ------------------------------------------------------------------

_entity_manager = None

# ------------------------------------------------------------------
# Units config
# ------------------------------------------------------------------

_UNITS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "units.json")
)


@router.get("/units")
def get_units():
    import json
    if not os.path.exists(_UNITS_PATH):
        return {"content": "{}"}
    with open(_UNITS_PATH, "r", encoding="utf-8") as f:
        return {"content": f.read()}


class UnitsUpdate(BaseModel):
    content: str


@router.put("/units")
def update_units(req: UnitsUpdate):
    import json
    # Validate JSON before saving
    try:
        parsed = json.loads(req.content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    # The converter re-reads units.json on change, so a structurally bad table
    # must be refused here rather than discovered mid-translation.
    from unit_converter import parse_units
    try:
        parse_units(parsed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Atomic write: truncate-then-write would leave a corrupt units.json if
    # the process died mid-write.
    tmp = _UNITS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(req.content)
    os.replace(tmp, _UNITS_PATH)
    return {"status": "ok"}


def init_db(entity_manager):
    global _entity_manager
    _entity_manager = entity_manager


@router.get("/db/export-json")
def export_json():
    from fastapi.responses import StreamingResponse
    import io, json, tempfile, os

    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    try:
        success = _entity_manager.export_to_json(tmp.name)
        if not success:
            raise HTTPException(status_code=500, detail="Export failed.")
        with open(tmp.name, "rb") as f:
            data = f.read()
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="entities.json"'},
        )
    finally:
        os.unlink(tmp.name)
