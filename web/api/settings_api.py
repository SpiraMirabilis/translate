"""
Settings and provider configuration endpoints.
"""
import os
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
        "site_name": _config.site_name,
        "public_site_name": _config.public_site_name,
        "comment_automod_enabled": getattr(_config, "comment_automod_enabled", False),
        "comment_automod_model": getattr(_config, "comment_automod_model", "claude:claude-haiku-4-5"),
        "pronoun_repair_model": getattr(_config, "pronoun_repair_model", "claude:claude-haiku-4-5"),
        "email_from": getattr(_config, "email_from", ""),
        "email_backend": getattr(_config, "email_backend", "ses"),
        "site_base_url": getattr(_config, "site_base_url", ""),
        "trad_to_simp": getattr(_config, "trad_to_simp", False),
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
        "grammar_check_enabled": getattr(_config, "grammar_check_enabled", False),
        "languagetool_url": getattr(_config, "languagetool_url", "http://127.0.0.1:8081"),
        "grammar_language": getattr(_config, "grammar_language", "en-US"),
        "polish_model": getattr(_config, "polish_model", "claude:claude-sonnet-4-6"),
    }


class SettingsUpdate(BaseModel):
    translation_model: Optional[str] = None
    advice_model: Optional[str] = None
    debug_mode: Optional[bool] = None
    public_library: Optional[bool] = None
    site_name: Optional[str] = None
    public_site_name: Optional[str] = None
    comment_automod_enabled: Optional[bool] = None
    comment_automod_model: Optional[str] = None
    pronoun_repair_model: Optional[str] = None
    email_from: Optional[str] = None
    email_backend: Optional[str] = None
    site_base_url: Optional[str] = None
    trad_to_simp: Optional[bool] = None
    disable_content_cache: Optional[bool] = None
    disable_media_cache: Optional[bool] = None
    overload_retry_wait_seconds: Optional[int] = None
    json_fix_timeout_seconds: Optional[int] = None
    grammar_check_enabled: Optional[bool] = None
    languagetool_url: Optional[str] = None
    grammar_language: Optional[str] = None
    polish_model: Optional[str] = None


@router.put("")
def update_settings(req: SettingsUpdate):
    # Only forward keys the caller actually set, and only those known to the
    # store. settings_store.update() handles JSON persistence + os.environ sync.
    updates = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
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
        json.loads(req.content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
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
