"""
Book and chapter management endpoints.
"""
import datetime
import io
import os
import re
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from PIL import Image
from database import DEFAULT_CATEGORIES
from web.api.deps import get_book_or_404
from web.services import media_urls


def _content_disposition(filename: str) -> str:
    """Build an RFC 5987-compliant Content-Disposition header that tolerates non-ASCII filenames."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"

THUMB_MAX_SIZE = (80, 112)  # 2x the display size (w-8 h-11 = 32x44) for retina

router = APIRouter(prefix="/api/books")

_entity_manager = None
_translator = None
_logger = None


def init(entity_manager, translator, logger):
    global _entity_manager, _translator, _logger
    _entity_manager = entity_manager
    _translator = translator
    _logger = logger


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------

class BookCreate(BaseModel):
    title: str
    author: Optional[str] = None
    language: Optional[str] = "en"
    source_language: Optional[str] = "zh"
    description: Optional[str] = None
    genre: Optional[str] = None
    source_url: Optional[str] = None
    notes: Optional[str] = None
    is_original: Optional[bool] = False  # original work written in the web editor


class BookUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None
    total_source_chapters: Optional[int] = None
    status: Optional[str] = None
    comments_enabled: Optional[bool] = None
    source_url: Optional[str] = None
    notes: Optional[str] = None
    trad_to_simp: Optional[int] = None  # tri-state: null=inherit global, 0=off, 1=on
    tags: Optional[List[str]] = None
    modules: Optional[Dict[str, bool]] = None  # per-book override map {module_id: on}; {}/null = pure auto
    is_original: Optional[bool] = None


class ModuleSettingsUpdate(BaseModel):
    settings: Dict[str, Any]  # {setting_key: value}; authoritatively replaces stored settings


class PromptUpdate(BaseModel):
    template: str


class ChapterContentUpdate(BaseModel):
    content: List[str]
    title: Optional[str] = None
    # Write-editor extensions (absent = legacy behavior, used by ChapterEditor):
    autosave: Optional[bool] = False   # background save; auto revision at most every 10 min
    snapshot: Optional[bool] = False   # explicit save; always records a 'manual' revision
    expected_translation_date: Optional[str] = None  # optimistic lock: 409 on mismatch


class ChapterCreate(BaseModel):
    title: Optional[str] = None
    chapter_number: Optional[int] = None  # default: max existing + 1


class ChapterRenumber(BaseModel):
    new_chapter_number: int


class ChapterProofreadUpdate(BaseModel):
    is_proofread: bool  # True sets timestamp, False clears it


class ChapterPublishUpdate(BaseModel):
    # ISO timestamp — now = publish immediately, future = scheduled;
    # null/omitted = unpublish (back to draft). No separate boolean.
    published_at: Optional[str] = None


class BatchPublishAction(BaseModel):
    chapters: List[int]
    published_at: Optional[str] = None   # start time; omitted = now
    interval_hours: Optional[float] = 0  # drip-release stagger per chapter (ascending)
    unpublish: Optional[bool] = False    # true = set all selected back to draft


class BatchChapterAction(BaseModel):
    chapters: List[int]


class BatchRequeueAction(BaseModel):
    chapters: List[int]
    retranslation_reason: Optional[str] = None


class BatchProofreadAction(BaseModel):
    chapters: List[int]
    is_proofread: bool  # True sets timestamp, False clears it


class CategoriesUpdate(BaseModel):
    categories: List[str]
    # Optional per-category attribute map, e.g. {"characters": ["gender"]}.
    # Categories absent from the map have no attributes. When omitted entirely,
    # gender defaults are applied (characters stays gender-tracked).
    attributes: Optional[Dict[str, List[str]]] = None


class BookSearchRequest(BaseModel):
    query: str
    scope: Optional[str] = "both"
    is_regex: Optional[bool] = False


class BookReplaceRequest(BaseModel):
    query: str
    replacement: str
    chapter_numbers: Optional[List[int]] = None
    is_regex: Optional[bool] = False


# ------------------------------------------------------------------
# Books CRUD
# ------------------------------------------------------------------

@router.get("")
def list_books():
    books = _entity_manager.list_books()
    for b in (books or []):
        _attach_cover_urls(b)
    return {"books": books or []}


@router.post("")
def create_book(req: BookCreate):
    source_lang = req.source_language or "zh"

    # If a genre is specified, use its source_language as default. Original
    # works have no source text, so genre presets don't apply.
    genre_obj = None
    if req.genre and req.genre != "custom" and not req.is_original:
        from genres import get_genre
        genre_obj = get_genre(_entity_manager.config.script_dir, req.genre)
        if genre_obj and genre_obj.get("source_language") and not req.source_language:
            source_lang = genre_obj["source_language"]

    book_id = _entity_manager.create_book(
        title=req.title,
        author=req.author,
        language=req.language or "en",
        source_language="en" if req.is_original else source_lang,
        description=req.description,
        is_original=bool(req.is_original),
    )
    if not book_id:
        raise HTTPException(status_code=500, detail="Failed to create book.")

    # Apply genre preset: prompt template and categories (derived from prompt)
    if genre_obj:
        from genres import read_genre_prompt, extract_categories_meta_from_prompt
        prompt = read_genre_prompt(_entity_manager.config.script_dir, genre_obj)
        if prompt:
            _entity_manager.set_book_prompt_template(book_id, prompt)
            cats = extract_categories_meta_from_prompt(prompt)
            if cats:
                _entity_manager.set_book_categories(book_id, cats)

    # Apply optional metadata that create_book() doesn't accept directly
    extras = {k: v for k, v in (("source_url", req.source_url), ("notes", req.notes)) if v}
    if extras:
        _entity_manager.update_book(book_id, **extras)

    return {"id": book_id, "title": req.title}


# ------------------------------------------------------------------
# Prompt template (literal paths MUST come before /{book_id} routes)
# ------------------------------------------------------------------

@router.get("/tags")
def list_all_tags():
    """Return the deduped, sorted union of tags across all books (for autocomplete)."""
    return {"tags": _entity_manager.get_all_tags()}


@router.get("/genres")
def list_genres():
    """Return available genre presets."""
    from genres import load_genres
    genres = load_genres(_entity_manager.config.script_dir)
    if not genres:
        # Hardcoded fallback
        genres = [
            {"id": "chinese_xianxia", "name": "Chinese Xianxia", "source_language": "zh", "description": "Chinese cultivation/xianxia web novels"},
            {"id": "chinese_general", "name": "Chinese General", "source_language": "zh", "description": "General Chinese web novels"},
            {"id": "japanese_light_novel", "name": "Japanese Light Novel", "source_language": "ja", "description": "Japanese light novels and web novels"},
            {"id": "korean_web_novel", "name": "Korean Web Novel", "source_language": "ko", "description": "Korean web novels"},
            {"id": "custom", "name": "Custom", "source_language": None, "description": "Manual configuration"},
        ]
    return {"genres": genres}


@router.get("/modules")
def list_modules(book_id: Optional[int] = None):
    """Return the registered per-book modules (for the book edit UI).

    When ``book_id`` is supplied, each module also reports ``auto_enabled`` —
    whether its automatic trigger currently fires for that book, independent of
    any explicit On/Off override — so the UI can show whether 'Auto' resolves to
    enabled or off for this specific book.
    """
    from modules import all_modules
    book = _entity_manager.get_book(book_id=book_id) if book_id is not None else None
    ctx = None
    stored_settings = {}
    if book is not None:
        ctx = {"config": _entity_manager.config,
               "model": getattr(_entity_manager.config, "translation_model", None)}
        stored_settings = _entity_manager.get_module_settings(book_id)  # {module_id: {key: value}}
    out = []
    for m in all_modules():
        item = {"id": m.id, "name": m.name, "description": m.description,
                "auto_url_patterns": list(m.auto_url_patterns),
                "default_enabled": bool(m.default_enabled),
                "has_auto": bool(m.has_auto),
                "auto_hint": m.auto_hint,
                "has_settings": bool(m.has_settings),
                "settings_schema": list(m.settings_schema)}
        if book is not None:
            try:
                item["auto_enabled"] = bool(m.auto_enabled(book, ctx))
            except Exception:  # noqa: BLE001 - never let one module break the list
                item["auto_enabled"] = None
            # Resolved current settings (schema defaults merged with stored values).
            if m.has_settings:
                item["settings"] = m.resolve_settings(stored_settings.get(m.id))
        out.append(item)
    resp = {"modules": out}
    if book_id is not None:
        from modules import module_task_runner
        resp["task"] = module_task_runner.status(book_id)
    return resp


@router.get("/{book_id}/module-task")
def get_module_task(book_id: int):
    """Status of the book's background module backfill (polled by the UI).

    Returns ``{"running": info|null, "last": info|null}`` — ``running`` is the
    pending/in-flight task, ``last`` the most recent finished one (with its
    ``error`` if it failed).
    """
    from modules import module_task_runner
    return module_task_runner.status(book_id)


@router.get("/default-prompt")
def get_default_prompt():
    """Return the default system prompt template with {{ENTITIES_JSON}} and {{CHAPTER_NUMBER}} placeholders."""
    import json

    entities_json = {cat: {} for cat in DEFAULT_CATEGORIES}
    default = _translator.generate_system_prompt([], entities_json, do_count=False)
    # Replace the empty entities JSON with the placeholder
    default = default.replace(
        json.dumps(entities_json, ensure_ascii=False, indent=4),
        "{{ENTITIES_JSON}}"
    )
    # Restore the entity categories placeholder
    default = default.replace(
        ", ".join(DEFAULT_CATEGORIES),
        "{{ENTITY_CATEGORIES}}"
    )
    # Restore the chapter number placeholder (generate_system_prompt strips it when chapter_number is None)
    if "{{CHAPTER_NUMBER}}" not in default:
        default = default.replace(
            "You are a Chinese-to-English literary translator.",
            "You are a Chinese-to-English literary translator.\n\nYou are translating chapter {{CHAPTER_NUMBER}}.",
        )
    return {"template": default}


# ------------------------------------------------------------------
# Book CRUD (parameterized /{book_id} routes)
# ------------------------------------------------------------------

@router.get("/{book_id}")
def get_book(book_id: int):
    book = get_book_or_404(book_id)
    return _attach_cover_urls(book)


@router.put("/{book_id}")
def update_book(book_id: int, req: BookUpdate):
    book = get_book_or_404(book_id)
    dump = req.model_dump()
    # Allow total_source_chapters=null to clear the value
    nullable_fields = {'total_source_chapters', 'trad_to_simp', 'modules'}
    kwargs = {k: v for k, v in dump.items() if v is not None or k in nullable_fields}
    if 'status' in kwargs and kwargs['status'] not in ('ongoing', 'ongoing-trial', 'hiatus', 'completed', 'dropped'):
        raise HTTPException(status_code=400, detail="Invalid status. Must be one of: ongoing, ongoing-trial, hiatus, completed, dropped")
    if 'tags' in kwargs:
        kwargs['tags'] = _normalize_tags(kwargs['tags'])
    if 'modules' in kwargs:
        kwargs['modules'] = _normalize_modules(kwargs['modules'])
    from modules import ModuleTaskBusyError, module_task_runner
    try:
        success = _entity_manager.update_book(book_id, **kwargs)
    except ModuleTaskBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update book.")
    # If the update changed the enabled-module set, the backfill now runs as a
    # background task — hand its info to the caller so the UI can track it.
    return {"status": "ok", "module_task": module_task_runner.active(book_id)}


def _normalize_tags(tags):
    """Strip + lowercase + dedupe a tag list. Validates length and count."""
    if not tags:
        return []
    seen = set()
    cleaned = []
    for t in tags:
        if not isinstance(t, str):
            raise HTTPException(status_code=400, detail="Tags must be strings.")
        s = t.strip().lower()
        if not s:
            continue
        if len(s) > 50:
            raise HTTPException(status_code=400, detail=f"Tag too long (max 50 chars): {s[:50]}...")
        if s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    if len(cleaned) > 30:
        raise HTTPException(status_code=400, detail="Too many tags (max 30 per book).")
    return cleaned


def _normalize_modules(modules):
    """Keep only registered module ids, coerce values to bool. Empty -> None (pure auto)."""
    if not modules:
        return None
    from modules import REGISTRY
    cleaned = {k: bool(v) for k, v in modules.items() if k in REGISTRY}
    return cleaned or None


def _normalize_module_settings(module, settings):
    """Keep only keys declared in the module's ``settings_schema`` (drop unknowns)."""
    if not settings:
        return {}
    allowed = {f["key"] for f in module.settings_schema}
    return {k: v for k, v in settings.items() if k in allowed}


@router.put("/{book_id}/modules/{module_id}/settings")
def set_module_settings(book_id: int, module_id: str, req: ModuleSettingsUpdate):
    """Authoritatively replace a book's stored settings for one module.

    Unknown keys (not in the module's ``settings_schema``) are dropped. Returns
    the resolved settings (schema defaults merged with the saved values).
    """
    book = get_book_or_404(book_id)
    from modules import REGISTRY
    module = REGISTRY.get(module_id)
    if module is None:
        raise HTTPException(status_code=404, detail="Unknown module.")
    if not module.has_settings:
        raise HTTPException(status_code=400, detail="Module has no settings.")
    cleaned = _normalize_module_settings(module, req.settings)
    from modules import (apply_module_settings_change, ModuleTaskBusyError,
                         module_task_runner)
    # Persists the settings and, for modules that opt in, re-derives any
    # settings-dependent backfill (remove→persist→add, run as a background
    # task) when the module is enabled and the resolved settings changed.
    try:
        success = apply_module_settings_change(
            _entity_manager, book, module_id, cleaned,
            _entity_manager.config, _logger)
    except ModuleTaskBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save module settings.")
    return {"status": "ok", "settings": module.resolve_settings(cleaned),
            "module_task": module_task_runner.active(book_id)}


@router.delete("/{book_id}")
def delete_book(book_id: int):
    book = get_book_or_404(book_id)
    # Guardrail: a running module backfill is rewriting this book's chapters —
    # deleting the book out from under it would race the background thread.
    from modules import module_task_runner
    active = module_task_runner.active(book_id)
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"A module task ({active.get('label')}) is running for this "
                   "book. Wait for it to finish before deleting.")
    # Clean up cover + thumbnail files (local + Spaces)
    _remove_cover_files(book, book_id)
    # Best-effort purge of this book's illustration + EPUB objects from Spaces.
    try:
        import spaces
        cfg = _entity_manager.config
        if spaces.is_enabled(cfg):
            spaces.delete_prefix(cfg, f"illustrations/{book_id}")
            spaces.delete_prefix(cfg, f"epub/{book_id}")
    except Exception:
        pass
    success = _entity_manager.delete_book(book_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete book.")
    return {"status": "ok"}


# ------------------------------------------------------------------
# Cover images
# ------------------------------------------------------------------

def _covers_dir():
    return os.path.join(_entity_manager.config.script_dir, "covers")


def _spaces_upload(rel_path):
    """Best-effort mirror of a local media file to Spaces/CDN (no-op when disabled)."""
    try:
        import spaces
        cfg = _entity_manager.config
        if spaces.is_enabled(cfg):
            spaces.upload_relpath(cfg, rel_path)
    except Exception:
        pass


def _spaces_delete(rel_path):
    """Best-effort delete of a Spaces object mirroring a local relative path."""
    try:
        import spaces
        cfg = _entity_manager.config
        if spaces.is_enabled(cfg):
            spaces.delete_relpath(cfg, rel_path)
    except Exception:
        pass


# Shared cover/illustration CDN helpers (web/services/media_urls.py); thin
# delegates keep the existing call sites unchanged.

def _cdn_url(rel_path):
    return media_urls.cdn_url(_entity_manager, rel_path)


def _attach_cover_urls(book):
    return media_urls.attach_cover_urls(_entity_manager, book)


def _attach_illustrations(book_id, ch):
    return media_urls.attach_illustrations(_entity_manager, book_id, ch)


def _cdn_redirect_or_file(rel_path, local_filepath, media_type=None):
    return media_urls.cdn_redirect_or_file(_entity_manager, rel_path, local_filepath,
                                           media_type=media_type)


def _generate_cover_derivatives(book_id, cover_rel):
    """Generate + upload the thumb and medium derivatives for a book's cover."""
    import cover_images
    cover_images.generate_all(_entity_manager.config, {"id": book_id, "cover_image": cover_rel})


def _remove_cover_files(book, book_id):
    """Remove full cover + derivative files for a book (local + Spaces)."""
    if book.get("cover_image"):
        full_path = os.path.join(_entity_manager.config.script_dir, book["cover_image"])
        if os.path.exists(full_path):
            os.remove(full_path)
        _spaces_delete(book["cover_image"])
    for kind in ("thumb", "medium"):
        rel = f"covers/{book_id}_{kind}.webp"
        local = os.path.join(_entity_manager.config.script_dir, rel)
        if os.path.exists(local):
            os.remove(local)
        _spaces_delete(rel)


@router.post("/{book_id}/cover")
async def upload_cover(book_id: int, file: UploadFile = File(...)):
    book = get_book_or_404(book_id)

    ct = file.content_type or ""
    if not ct.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image.")

    ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
    ext = ext_map.get(ct, ".jpg")

    # Remove old cover + thumbnail if exists
    _remove_cover_files(book, book_id)

    covers = _covers_dir()
    os.makedirs(covers, exist_ok=True)
    filename = f"{book_id}{ext}"
    filepath = os.path.join(covers, filename)

    data = await file.read()
    with open(filepath, "wb") as f:
        f.write(data)

    rel_path = f"covers/{filename}"
    _spaces_upload(rel_path)                       # full cover
    _generate_cover_derivatives(book_id, rel_path)  # thumb + medium (uploaded too)

    _entity_manager.update_book(book_id, cover_image=rel_path)
    return {"status": "ok", "cover_image": rel_path}


@router.get("/{book_id}/cover")
def get_cover(book_id: int):
    book = get_book_or_404(book_id)
    if not book.get("cover_image"):
        raise HTTPException(status_code=404, detail="No cover image.")
    filepath = os.path.join(_entity_manager.config.script_dir, book["cover_image"])
    return _cdn_redirect_or_file(book["cover_image"], filepath)


def _serve_cover_derivative(book_id, kind):
    """Serve a cover derivative (thumb|medium), generating it on the fly if
    missing, with CDN redirect + full-cover fallback."""
    import cover_images
    book = get_book_or_404(book_id)
    if not book.get("cover_image"):
        raise HTTPException(status_code=404, detail="No cover image.")
    path = cover_images.ensure_derivative(_entity_manager.config, book, kind)
    if not path:
        # Generation failed — fall back to the full cover.
        return _cdn_redirect_or_file(
            book["cover_image"],
            os.path.join(_entity_manager.config.script_dir, book["cover_image"]),
        )
    return _cdn_redirect_or_file(cover_images.derivative_relpath(book_id, kind), path, media_type="image/webp")


@router.get("/{book_id}/cover/thumb")
def get_cover_thumb(book_id: int):
    """Serve the small webp thumbnail (admin list rows / Reader TOC)."""
    return _serve_cover_derivative(book_id, "thumb")


@router.get("/{book_id}/cover/medium")
def get_cover_medium(book_id: int):
    """Serve the medium webp derivative (Library grid / detail hero)."""
    return _serve_cover_derivative(book_id, "medium")


@router.delete("/{book_id}/cover")
def delete_cover(book_id: int):
    book = get_book_or_404(book_id)
    _remove_cover_files(book, book_id)
    _entity_manager.update_book(book_id, cover_image="")
    return {"status": "ok"}


@router.get("/{book_id}/illustration/{marker_id}")
def get_illustration(book_id: int, marker_id: str):
    """Serve an in-chapter illustration image by its opaque marker id.

    The marker id is the value embedded in chapter content as ⟦IMG:<marker_id>⟧
    (see illustrations.py) — a short lowercase-hex token.
    """
    if not re.fullmatch(r"[0-9a-f]{4,}", marker_id or ""):
        raise HTTPException(status_code=400, detail="Invalid illustration id.")
    row = _entity_manager.get_book_illustration(book_id, marker_id)
    if not row or not row.get("filename"):
        raise HTTPException(status_code=404, detail="Illustration not found.")
    filepath = os.path.join(_entity_manager.config.script_dir, row["filename"])
    return _cdn_redirect_or_file(row["filename"], filepath)


# ------------------------------------------------------------------
# Book-specific prompt templates
# ------------------------------------------------------------------

@router.get("/{book_id}/prompt")
def get_prompt(book_id: int):
    book = get_book_or_404(book_id)
    template = _entity_manager.get_book_prompt_template(book_id)
    return {"template": template or ""}


@router.put("/{book_id}/prompt")
def set_prompt(book_id: int, req: PromptUpdate):
    book = get_book_or_404(book_id)
    success = _entity_manager.set_book_prompt_template(book_id, req.template)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save prompt template.")
    return {"status": "ok"}


@router.delete("/{book_id}/prompt")
def reset_prompt(book_id: int):
    book = get_book_or_404(book_id)
    _entity_manager.set_book_prompt_template(book_id, None)
    return {"status": "ok"}


# ------------------------------------------------------------------
# Per-book entity categories
# ------------------------------------------------------------------

@router.get("/{book_id}/categories")
def get_categories(book_id: int):
    book = get_book_or_404(book_id)
    meta = _entity_manager.get_book_categories_meta(book_id)
    is_default = book.get("categories") is None
    return {
        "categories": [c["name"] for c in meta],
        "attributes": {c["name"]: c["attributes"] for c in meta},
        "is_default": is_default,
    }


@router.put("/{book_id}/categories")
def set_categories(book_id: int, req: CategoriesUpdate):
    book = get_book_or_404(book_id)
    # Validate + attach per-category attributes. When `attributes` is omitted
    # (legacy clients), pass bare names so normalize_categories applies gender
    # defaults instead of silently stripping gender from characters.
    has_attrs = req.attributes is not None
    attr_map = {k.strip().lower(): v for k, v in (req.attributes or {}).items()}
    seen = set()
    cleaned = []
    for cat in req.categories:
        c = cat.strip().lower()
        if not c:
            raise HTTPException(status_code=400, detail="Category names must be non-empty.")
        if c in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate category: {c}")
        seen.add(c)
        cleaned.append({"name": c, "attributes": attr_map.get(c, [])} if has_attrs else c)
    if not cleaned:
        raise HTTPException(status_code=400, detail="At least one category is required.")
    success = _entity_manager.set_book_categories(book_id, cleaned)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save categories.")
    meta = _entity_manager.get_book_categories_meta(book_id)
    return {
        "status": "ok",
        "categories": [c["name"] for c in meta],
        "attributes": {c["name"]: c["attributes"] for c in meta},
    }


@router.delete("/{book_id}/categories")
def reset_categories(book_id: int):
    book = get_book_or_404(book_id)
    _entity_manager.set_book_categories(book_id, None)
    return {"status": "ok"}


@router.get("/{book_id}/categories/entity-counts")
def category_entity_counts(book_id: int):
    """Return the count of entities per category for a book (includes global)."""
    return {"counts": _entity_manager.count_entities_by_category(book_id)}


# ------------------------------------------------------------------
# Search & Replace
# ------------------------------------------------------------------

@router.post("/{book_id}/search")
def search_book(book_id: int, req: BookSearchRequest):
    book = get_book_or_404(book_id)
    if not req.query:
        return {"results": [], "total_matches": 0}
    results = _entity_manager.search_book_chapters(
        book_id, req.query, scope=req.scope or "both", is_regex=req.is_regex or False
    )
    total = sum(r["match_count"] for r in results)
    return {"results": results, "total_matches": total}


@router.post("/{book_id}/replace")
def replace_in_book(book_id: int, req: BookReplaceRequest):
    book = get_book_or_404(book_id)
    if not req.query:
        return {"status": "ok", "affected_chapters": 0, "total_replacements": 0}
    result = _entity_manager.replace_in_chapters(
        book_id, req.query, req.replacement,
        chapter_numbers=req.chapter_numbers, is_regex=req.is_regex or False
    )
    return {"status": "ok", **result}


@router.post("/{book_id}/undo-replace")
def undo_replace(book_id: int):
    if not _entity_manager.has_replace_undo(book_id):
        raise HTTPException(status_code=404, detail="Nothing to undo.")
    result = _entity_manager.undo_replace(book_id)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to undo.")
    return {"status": "ok", **result}


# ------------------------------------------------------------------
# Chapters
# ------------------------------------------------------------------

@router.get("/{book_id}/chapters")
def list_chapters(book_id: int):
    book = get_book_or_404(book_id)
    chapters = _entity_manager.list_chapters(book_id)
    return {"chapters": chapters or []}


_BATCH_CHAPTER_MAX = 10


@router.get("/{book_id}/chapters/batch")
def get_chapters_batch(book_id: int, nums: str):
    book = get_book_or_404(book_id)
    try:
        wanted = [int(n) for n in nums.split(",") if n.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="nums must be comma-separated integers")
    if not wanted:
        return {"chapters": []}
    if len(wanted) > _BATCH_CHAPTER_MAX:
        raise HTTPException(status_code=400, detail=f"At most {_BATCH_CHAPTER_MAX} chapters per batch")
    out = []
    for n in wanted:
        ch = _entity_manager.get_chapter(book_id=book_id, chapter_number=n)
        if ch:
            out.append(_attach_illustrations(book_id, ch))
    return {"chapters": out}


@router.get("/{book_id}/chapters/{chapter_number}")
def get_chapter(book_id: int, chapter_number: int):
    chapter = _entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found.")
    return _attach_illustrations(book_id, chapter)


@router.post("/{book_id}/chapters")
def create_chapter(book_id: int, req: ChapterCreate):
    """Create an empty chapter (write editor / original works).

    Chapters are otherwise only born from the translation queue; original
    works need a direct creation path.
    """
    get_book_or_404(book_id)
    if req.chapter_number is not None:
        if req.chapter_number < 1:
            raise HTTPException(status_code=400, detail="Chapter number must be a positive integer.")
        number = req.chapter_number
    else:
        existing = _entity_manager.list_chapters(book_id)
        number = max((ch["chapter"] for ch in existing), default=0) + 1
    if _entity_manager.get_chapter(book_id=book_id, chapter_number=number):
        raise HTTPException(status_code=409, detail=f"Chapter {number} already exists.")
    chapter_id = _entity_manager.save_chapter(
        book_id=book_id,
        chapter_number=number,
        title=req.title or f"Chapter {number}",
        untranslated_content=[],
        translated_content=[],
        translation_model="original",
    )
    if not chapter_id:
        raise HTTPException(status_code=500, detail="Failed to create chapter.")
    return {"status": "ok", "chapter_number": number, "id": chapter_id}


@router.put("/{book_id}/chapters/{chapter_number}")
def update_chapter_translation(book_id: int, chapter_number: int, req: ChapterContentUpdate):
    chapter = _entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found.")

    # Optimistic lock (write editor): reject if the chapter changed on the
    # server since the client loaded it, so a stale autosave can't clobber
    # e.g. a revision restore done in another tab.
    if req.expected_translation_date is not None and \
            req.expected_translation_date != chapter.get("translation_date"):
        raise HTTPException(status_code=409, detail={
            "message": "Chapter changed on server",
            "translation_date": chapter.get("translation_date"),
        })

    title = req.title if req.title is not None else chapter.get("title", f"Chapter {chapter_number}")
    chapter_id = _entity_manager.save_chapter(
        book_id=book_id,
        chapter_number=chapter_number,
        title=title,
        untranslated_content=chapter.get("untranslated", []),
        translated_content=req.content,
        summary=chapter.get("summary"),
        translation_model=chapter.get("model"),
    )
    if not chapter_id:
        raise HTTPException(status_code=500, detail="Failed to update chapter.")

    # Revision snapshots (write editor): explicit saves always record one;
    # autosaves leave a coalesced breadcrumb trail (at most one per 10 min).
    if req.snapshot:
        _entity_manager.add_chapter_revision(book_id, chapter_number, title, req.content, kind='manual')
    elif req.autosave:
        last = _entity_manager.latest_revision_time(book_id, chapter_number)
        cutoff = (datetime.datetime.now() - datetime.timedelta(minutes=10)).isoformat()
        if not last or last < cutoff:
            _entity_manager.add_chapter_revision(book_id, chapter_number, title, req.content, kind='auto')

    saved = _entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
    return {"status": "ok", "translation_date": saved.get("translation_date") if saved else None}


def _normalize_publish_time(value: Optional[str]) -> Optional[str]:
    """Parse/normalize a publish timestamp to naive server-local ISO format
    (the same convention as translation_date, so string comparison works)."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid published_at timestamp.")
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.isoformat()


@router.put("/{book_id}/chapters/{chapter_number}/publish")
def set_chapter_publish(book_id: int, chapter_number: int, req: ChapterPublishUpdate):
    """Set a chapter's publish time (now/scheduled) or clear it (draft)."""
    value = _normalize_publish_time(req.published_at)
    try:
        stored = _entity_manager.set_chapter_published(book_id, chapter_number, value)
    except LookupError:
        raise HTTPException(status_code=404, detail="Chapter not found.")
    return {"status": "ok", "published_at": stored}


@router.post("/{book_id}/chapters/batch-publish")
def batch_publish(book_id: int, req: BatchPublishAction):
    """Publish/schedule/unpublish many chapters at once.

    With interval_hours set, chapters (ascending) get staggered publish times
    start + i*interval — a drip release."""
    get_book_or_404(book_id)
    if not req.chapters:
        raise HTTPException(status_code=400, detail="No chapters given.")
    nums = sorted(set(req.chapters))
    if req.unpublish:
        schedule = [(n, None) for n in nums]
    else:
        start_s = _normalize_publish_time(req.published_at) or datetime.datetime.now().isoformat()
        start = datetime.datetime.fromisoformat(start_s)
        step = datetime.timedelta(hours=req.interval_hours or 0)
        schedule = [(n, (start + i * step).isoformat()) for i, n in enumerate(nums)]
    updated = _entity_manager.set_chapters_published(book_id, schedule)
    return {
        "status": "ok",
        "updated": updated,
        "schedule": [{"chapter": n, "published_at": t} for n, t in schedule],
    }


@router.put("/{book_id}/chapters/{chapter_number}/proofread")
def set_proofread(book_id: int, chapter_number: int, req: ChapterProofreadUpdate):
    try:
        now = _entity_manager.set_chapter_proofread(book_id, chapter_number, req.is_proofread)
    except LookupError:
        raise HTTPException(status_code=404, detail="Chapter not found.")
    return {"status": "ok", "is_proofread": now}


@router.delete("/{book_id}/chapters/{chapter_number}")
def delete_chapter(book_id: int, chapter_number: int):
    success = _entity_manager.delete_chapter(book_id=book_id, chapter_number=chapter_number)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete chapter.")
    return {"status": "ok"}


@router.post("/{book_id}/chapters/{chapter_number}/renumber")
def renumber_chapter(book_id: int, chapter_number: int, req: ChapterRenumber):
    ok, reason = _entity_manager.renumber_chapter(book_id, chapter_number, req.new_chapter_number)
    if not ok:
        if reason == "invalid":
            raise HTTPException(status_code=400, detail="Chapter number must be a positive integer.")
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="Chapter not found.")
        if reason == "target_exists":
            raise HTTPException(
                status_code=409,
                detail=f"Chapter {req.new_chapter_number} already exists for this book.",
            )
        raise HTTPException(status_code=500, detail=f"Failed to renumber chapter: {reason}")
    return {"status": "ok", "chapter_number": req.new_chapter_number}


@router.post("/{book_id}/chapters/batch-delete")
def batch_delete_chapters(book_id: int, req: BatchChapterAction):
    deleted = 0
    for num in req.chapters:
        if _entity_manager.delete_chapter(book_id=book_id, chapter_number=num):
            deleted += 1
    return {"status": "ok", "deleted": deleted}


@router.post("/{book_id}/chapters/batch-proofread")
def batch_proofread_chapters(book_id: int, req: BatchProofreadAction):
    updated, _now = _entity_manager.set_chapters_proofread(book_id, req.chapters, req.is_proofread)
    return {"status": "ok", "updated": updated}


@router.post("/{book_id}/chapters/batch-requeue")
def batch_requeue_chapters(book_id: int, req: BatchRequeueAction):
    book = get_book_or_404(book_id)

    queued = 0
    errors = []
    reason = (req.retranslation_reason or "").strip() or None
    # Iterate in descending chapter order so that when each item is inserted
    # with priority=True (which places it at the front of the queue), the
    # lowest-numbered chapter ends up first and is translated first.
    for num in sorted(req.chapters, reverse=True):
        chapter = _entity_manager.get_chapter(book_id=book_id, chapter_number=num)
        if not chapter:
            errors.append(f"Chapter {num} not found")
            continue
        untranslated = chapter.get("untranslated", [])
        if not untranslated:
            errors.append(f"Chapter {num} has no source text")
            continue
        queue_id = _entity_manager.add_to_queue(
            book_id=book_id,
            content=untranslated,
            title=chapter.get("title", f"Chapter {num}"),
            chapter_number=num,
            source="web",
            priority=True,
            retranslation_reason=reason,
        )
        if queue_id:
            queued += 1
    return {"status": "ok", "queued": queued, "errors": errors}


# ------------------------------------------------------------------
# Per-chapter pronoun repair
# ------------------------------------------------------------------


class PronounRepairRequest(BaseModel):
    entity_id: int


@router.get("/{book_id}/chapters/{chapter_number}/gendered-entities")
def list_gendered_entities_in_chapter(book_id: int, chapter_number: int):
    """Return character entities (with defined gender) whose English name appears in
    this chapter's translated text. Used to populate the per-chapter pronoun-repair picker.
    """
    chapter = _entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found.")
    translated = chapter.get("content") or []
    haystack = "\n".join(translated) if isinstance(translated, list) else str(translated or "")

    gendered_cats = _entity_manager.get_book_gendered_categories(book_id) or ['characters']
    rows = _entity_manager.list_gendered_entities(book_id, gendered_cats)

    out = []
    for r in rows:
        trans = r["translation"]
        if not trans:
            continue
        if re.search(r"\b" + re.escape(trans) + r"\b", haystack):
            out.append({
                "entity_id": r["id"],
                "untranslated": r["untranslated"],
                "translation": trans,
                "gender": r["gender"],
            })
    out.sort(key=lambda e: e["translation"].lower())
    return {"entities": out}


@router.post("/{book_id}/chapters/{chapter_number}/pronoun-repair")
def pronoun_repair_chapter(book_id: int, chapter_number: int, req: PronounRepairRequest):
    """Run pronoun_repair scoped to a single chapter for one entity. Synchronous —
    one chapter is fast enough that we return the result inline."""
    chapter = _entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found.")

    # Verify the entity belongs to this book and has the data pronoun_repair needs
    entity = _entity_manager.get_entity_by_id(req.entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail=f"Entity {req.entity_id} not found.")
    ent_trans, ent_gender, ent_book_id = entity["translation"], entity["gender"], entity["book_id"]
    if ent_book_id != book_id:
        raise HTTPException(status_code=400, detail="Entity does not belong to this book.")
    if (ent_gender or "").lower() not in ("male", "female", "neutral"):
        raise HTTPException(status_code=400, detail=f"Entity {ent_trans!r} has no usable gender; set one first.")

    try:
        from pronoun_repair import repair_pronouns_for_entity
        summary = repair_pronouns_for_entity(
            _entity_manager,
            req.entity_id,
            ent_gender.lower(),
            chapter_numbers=[chapter_number],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pronoun repair failed: {e}")

    # Log to activity feed for parity with the book-wide flow
    try:
        msg = (
            f"Pronoun repair (chapter {chapter_number}): "
            f"{summary['paragraphs_changed']} paragraph"
            f"{'s' if summary['paragraphs_changed'] != 1 else ''} corrected for "
            f"{summary.get('character_name') or ent_trans} "
            f"({summary['windows_examined']} windows examined)"
        )
        if summary.get("errors"):
            msg += f"; {len(summary['errors'])} window error(s)"
        _entity_manager.add_activity_log(
            type="pronoun_repair",
            message=msg,
            book_id=book_id,
            chapter=chapter_number,
            entities=[summary.get("character_name") or ent_trans],
        )
    except Exception:
        pass

    return {
        "status": "ok",
        "paragraphs_changed": summary["paragraphs_changed"],
        "windows_examined": summary["windows_examined"],
        "errors": len(summary.get("errors", [])),
        "character_name": summary.get("character_name") or ent_trans,
    }


# ------------------------------------------------------------------
# Export
# ------------------------------------------------------------------

@router.get("/{book_id}/export")
def export_book(book_id: int, format: str = Query("text", enum=["text", "epub", "markdown", "html"])):
    from web.services import exporters

    book = get_book_or_404(book_id)
    try:
        result = exporters.export_book(_entity_manager, _translator.config, _logger, book, format)
    except exporters.ExportError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    headers = {"Content-Disposition": _content_disposition(result.filename)}
    if result.is_path:
        return FileResponse(result.path, media_type=result.media_type, headers=headers)
    return StreamingResponse(io.BytesIO(result.content), media_type=result.media_type, headers=headers)


@router.post("/{book_id}/invalidate-epub-cache")
def invalidate_epub_cache(book_id: int):
    """Drop the cached EPUB for a book — local disk file and Spaces blob(s) —
    so the next export regenerates it from scratch."""
    book = get_book_or_404(book_id)

    # Combined invalidation: drops the local epub_cache/{book_id}.epub file and,
    # when enabled, best-effort purges this book's EPUB blobs from Spaces/CDN.
    _entity_manager.invalidate_epub_cache(book_id)

    spaces_purged = False
    try:
        import spaces
        spaces_purged = spaces.is_enabled(_entity_manager.config)
    except Exception:
        pass

    return {"status": "ok", "spaces_purged": spaces_purged}
