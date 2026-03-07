"""
Settings and provider configuration endpoints.
"""
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
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
async def list_providers():
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
async def set_api_key(provider_name: str, req: ApiKeyRequest):
    factory = get_factory()
    resolved = factory._resolve_provider_name(provider_name)
    if resolved not in factory.config["providers"]:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name}")

    env_var = factory.config["providers"][resolved].get("api_key_env")
    if not env_var:
        raise HTTPException(status_code=400, detail="This provider has no API key variable.")

    os.environ[env_var] = req.api_key
    return {"status": "ok", "env_var": env_var}


@router.post("/providers/{provider_name}/test")
async def test_api_key(provider_name: str):
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
async def get_settings():
    return {
        "translation_model": _config.translation_model,
        "advice_model": _config.advice_model,
        "debug_mode": _config.debug_mode,
    }


class SettingsUpdate(BaseModel):
    translation_model: Optional[str] = None
    advice_model: Optional[str] = None
    debug_mode: Optional[bool] = None


@router.put("")
async def update_settings(req: SettingsUpdate):
    if req.translation_model is not None:
        _config.translation_model = req.translation_model
    if req.advice_model is not None:
        _config.advice_model = req.advice_model
    if req.debug_mode is not None:
        _config.debug_mode = req.debug_mode
    return {"status": "ok"}


# ------------------------------------------------------------------
# Database utilities
# ------------------------------------------------------------------

@router.get("/db/export")
async def export_db():
    from fastapi.responses import StreamingResponse
    import io
    import json

    data = _config  # We need entity_manager — will be injected
    raise HTTPException(status_code=501, detail="Use /api/settings/db/export-json instead.")


_entity_manager = None


def init_db(entity_manager):
    global _entity_manager
    _entity_manager = entity_manager


@router.get("/db/export-json")
async def export_json():
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
