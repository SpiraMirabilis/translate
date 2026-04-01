"""
Book and chapter management endpoints.
"""
import io
import os
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
from PIL import Image
from database import DEFAULT_CATEGORIES

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


class BookUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None
    total_source_chapters: Optional[int] = None
    status: Optional[str] = None


class PromptUpdate(BaseModel):
    template: str


class ChapterContentUpdate(BaseModel):
    content: List[str]
    title: Optional[str] = None


class ChapterProofreadUpdate(BaseModel):
    is_proofread: bool


class CategoriesUpdate(BaseModel):
    categories: List[str]


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
async def list_books():
    books = _entity_manager.list_books()
    return {"books": books or []}


@router.post("")
async def create_book(req: BookCreate):
    source_lang = req.source_language or "zh"

    # If a genre is specified, use its source_language as default
    genre_obj = None
    if req.genre and req.genre != "custom":
        from genres import get_genre
        genre_obj = get_genre(_entity_manager.config.script_dir, req.genre)
        if genre_obj and genre_obj.get("source_language") and not req.source_language:
            source_lang = genre_obj["source_language"]

    book_id = _entity_manager.create_book(
        title=req.title,
        author=req.author,
        language=req.language or "en",
        source_language=source_lang,
        description=req.description,
    )
    if not book_id:
        raise HTTPException(status_code=500, detail="Failed to create book.")

    # Apply genre preset: prompt template and categories (derived from prompt)
    if genre_obj:
        from genres import read_genre_prompt, extract_categories_from_prompt
        prompt = read_genre_prompt(_entity_manager.config.script_dir, genre_obj)
        if prompt:
            _entity_manager.set_book_prompt_template(book_id, prompt)
            cats = extract_categories_from_prompt(prompt)
            if cats:
                _entity_manager.set_book_categories(book_id, cats)

    return {"id": book_id, "title": req.title}


# ------------------------------------------------------------------
# Prompt template (literal paths MUST come before /{book_id} routes)
# ------------------------------------------------------------------

@router.get("/genres")
async def list_genres():
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


@router.get("/default-prompt")
async def get_default_prompt():
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
async def get_book(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    return book


@router.put("/{book_id}")
async def update_book(book_id: int, req: BookUpdate):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    dump = req.model_dump() if hasattr(req, 'model_dump') else req.dict()
    # Allow total_source_chapters=null to clear the value
    nullable_fields = {'total_source_chapters'}
    kwargs = {k: v for k, v in dump.items() if v is not None or k in nullable_fields}
    if 'status' in kwargs and kwargs['status'] not in ('ongoing', 'hiatus', 'completed', 'dropped'):
        raise HTTPException(status_code=400, detail="Invalid status. Must be one of: ongoing, hiatus, completed, dropped")
    success = _entity_manager.update_book(book_id, **kwargs)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update book.")
    return {"status": "ok"}


@router.delete("/{book_id}")
async def delete_book(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    # Clean up cover + thumbnail files
    _remove_cover_files(book, book_id)
    success = _entity_manager.delete_book(book_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete book.")
    return {"status": "ok"}


# ------------------------------------------------------------------
# Cover images
# ------------------------------------------------------------------

def _covers_dir():
    return os.path.join(_entity_manager.config.script_dir, "covers")


def _generate_thumbnail(source_path, book_id):
    """Generate a small webp thumbnail for the books list page."""
    covers = _covers_dir()
    thumb_path = os.path.join(covers, f"{book_id}_thumb.webp")
    try:
        with Image.open(source_path) as img:
            img.thumbnail(THUMB_MAX_SIZE, Image.LANCZOS)
            img.save(thumb_path, "WEBP", quality=80)
    except Exception:
        # If thumbnail generation fails, it's non-critical
        if os.path.exists(thumb_path):
            os.remove(thumb_path)


def _remove_cover_files(book, book_id):
    """Remove full cover and thumbnail files for a book."""
    if book.get("cover_image"):
        full_path = os.path.join(_entity_manager.config.script_dir, book["cover_image"])
        if os.path.exists(full_path):
            os.remove(full_path)
    thumb_path = os.path.join(_covers_dir(), f"{book_id}_thumb.webp")
    if os.path.exists(thumb_path):
        os.remove(thumb_path)


@router.post("/{book_id}/cover")
async def upload_cover(book_id: int, file: UploadFile = File(...)):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

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

    _generate_thumbnail(filepath, book_id)

    rel_path = f"covers/{filename}"
    _entity_manager.update_book(book_id, cover_image=rel_path)
    return {"status": "ok", "cover_image": rel_path}


@router.get("/{book_id}/cover")
async def get_cover(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    if not book.get("cover_image"):
        raise HTTPException(status_code=404, detail="No cover image.")
    filepath = os.path.join(_entity_manager.config.script_dir, book["cover_image"])
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Cover file missing.")
    return FileResponse(filepath)


@router.get("/{book_id}/cover/thumb")
async def get_cover_thumb(book_id: int):
    """Serve a small webp thumbnail for the books list page."""
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    if not book.get("cover_image"):
        raise HTTPException(status_code=404, detail="No cover image.")
    thumb_path = os.path.join(_covers_dir(), f"{book_id}_thumb.webp")
    if not os.path.exists(thumb_path):
        # Generate on the fly if missing (e.g. for pre-existing covers)
        full_path = os.path.join(_entity_manager.config.script_dir, book["cover_image"])
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="Cover file missing.")
        _generate_thumbnail(full_path, book_id)
    if not os.path.exists(thumb_path):
        # Thumbnail generation failed, fall back to full image
        return FileResponse(os.path.join(_entity_manager.config.script_dir, book["cover_image"]))
    return FileResponse(thumb_path, media_type="image/webp")


@router.delete("/{book_id}/cover")
async def delete_cover(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    _remove_cover_files(book, book_id)
    _entity_manager.update_book(book_id, cover_image="")
    return {"status": "ok"}


# ------------------------------------------------------------------
# Book-specific prompt templates
# ------------------------------------------------------------------

@router.get("/{book_id}/prompt")
async def get_prompt(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    template = _entity_manager.get_book_prompt_template(book_id)
    return {"template": template or ""}


@router.put("/{book_id}/prompt")
async def set_prompt(book_id: int, req: PromptUpdate):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    success = _entity_manager.set_book_prompt_template(book_id, req.template)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save prompt template.")
    return {"status": "ok"}


@router.delete("/{book_id}/prompt")
async def reset_prompt(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    _entity_manager.set_book_prompt_template(book_id, None)
    return {"status": "ok"}


# ------------------------------------------------------------------
# Per-book entity categories
# ------------------------------------------------------------------

@router.get("/{book_id}/categories")
async def get_categories(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    categories = _entity_manager.get_book_categories(book_id)
    is_default = book.get("categories") is None
    return {"categories": categories, "is_default": is_default}


@router.put("/{book_id}/categories")
async def set_categories(book_id: int, req: CategoriesUpdate):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    # Validate
    seen = set()
    cleaned = []
    for cat in req.categories:
        c = cat.strip().lower()
        if not c:
            raise HTTPException(status_code=400, detail="Category names must be non-empty.")
        if c in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate category: {c}")
        seen.add(c)
        cleaned.append(c)
    if not cleaned:
        raise HTTPException(status_code=400, detail="At least one category is required.")
    success = _entity_manager.set_book_categories(book_id, cleaned)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save categories.")
    return {"status": "ok", "categories": cleaned}


@router.delete("/{book_id}/categories")
async def reset_categories(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    _entity_manager.set_book_categories(book_id, None)
    return {"status": "ok"}


@router.get("/{book_id}/categories/entity-counts")
async def category_entity_counts(book_id: int):
    """Return the count of entities per category for a book (includes global)."""
    conn = _entity_manager.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT category, COUNT(*) FROM entities WHERE book_id = ? OR book_id IS NULL GROUP BY category",
        (book_id,),
    )
    counts = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return {"counts": counts}


# ------------------------------------------------------------------
# Search & Replace
# ------------------------------------------------------------------

@router.post("/{book_id}/search")
async def search_book(book_id: int, req: BookSearchRequest):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    if not req.query:
        return {"results": [], "total_matches": 0}
    results = _entity_manager.search_book_chapters(
        book_id, req.query, scope=req.scope or "both", is_regex=req.is_regex or False
    )
    total = sum(r["match_count"] for r in results)
    return {"results": results, "total_matches": total}


@router.post("/{book_id}/replace")
async def replace_in_book(book_id: int, req: BookReplaceRequest):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    if not req.query:
        return {"status": "ok", "affected_chapters": 0, "total_replacements": 0}
    result = _entity_manager.replace_in_chapters(
        book_id, req.query, req.replacement,
        chapter_numbers=req.chapter_numbers, is_regex=req.is_regex or False
    )
    return {"status": "ok", **result}


@router.post("/{book_id}/undo-replace")
async def undo_replace(book_id: int):
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
async def list_chapters(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    chapters = _entity_manager.list_chapters(book_id)
    return {"chapters": chapters or []}


@router.get("/{book_id}/chapters/{chapter_number}")
async def get_chapter(book_id: int, chapter_number: int):
    chapter = _entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found.")
    return chapter


@router.put("/{book_id}/chapters/{chapter_number}")
async def update_chapter_translation(book_id: int, chapter_number: int, req: ChapterContentUpdate):
    chapter = _entity_manager.get_chapter(book_id=book_id, chapter_number=chapter_number)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found.")
    chapter_id = _entity_manager.save_chapter(
        book_id=book_id,
        chapter_number=chapter_number,
        title=req.title if req.title is not None else chapter.get("title", f"Chapter {chapter_number}"),
        untranslated_content=chapter.get("untranslated", []),
        translated_content=req.content,
        summary=chapter.get("summary"),
        translation_model=chapter.get("model"),
    )
    if not chapter_id:
        raise HTTPException(status_code=500, detail="Failed to update chapter.")
    return {"status": "ok"}


@router.put("/{book_id}/chapters/{chapter_number}/proofread")
async def set_proofread(book_id: int, chapter_number: int, req: ChapterProofreadUpdate):
    conn = _entity_manager.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE chapters SET is_proofread = ? WHERE book_id = ? AND chapter_number = ?",
        (1 if req.is_proofread else 0, book_id, chapter_number),
    )
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Chapter not found.")
    conn.commit()
    conn.close()
    return {"status": "ok", "is_proofread": req.is_proofread}


@router.delete("/{book_id}/chapters/{chapter_number}")
async def delete_chapter(book_id: int, chapter_number: int):
    success = _entity_manager.delete_chapter(book_id=book_id, chapter_number=chapter_number)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete chapter.")
    return {"status": "ok"}


# ------------------------------------------------------------------
# Export
# ------------------------------------------------------------------

@router.get("/{book_id}/export")
async def export_book(book_id: int, format: str = Query("text", enum=["text", "epub", "markdown", "html"])):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from output_formatter import OutputFormatter

    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    chapters = _entity_manager.list_chapters(book_id)
    if not chapters:
        raise HTTPException(status_code=404, detail="No chapters to export.")

    formatter = OutputFormatter(_translator.config, _logger)
    book_info = {
        "title": book.get("title", "Unknown"),
        "author": book.get("author") or "Translator",
        "language": book.get("language") or "en",
    }
    # Include cover image path for EPUB export
    if book.get("cover_image"):
        cover_full = os.path.join(_entity_manager.config.script_dir, book["cover_image"])
        if os.path.exists(cover_full):
            book_info["cover_image"] = cover_full

    if format == "epub":
        # Check for cached EPUB first
        cache_dir = _entity_manager._epub_cache_dir()
        cached_path = os.path.join(cache_dir, f"{book_id}.epub")
        filename = f"{book['title'].replace(' ', '_')}.epub"

        if not os.path.exists(cached_path):
            all_chapters = []
            for ch_meta in chapters:
                ch = _entity_manager.get_chapter(book_id=book_id, chapter_number=ch_meta["chapter"])
                if ch:
                    all_chapters.append({
                        "chapter": ch_meta["chapter"],
                        "title": ch.get("title", f"Chapter {ch_meta['chapter']}"),
                        "content": ch.get("content", []),
                    })

            output_path = formatter.save_book_as_epub(all_chapters, book_info)
            if not output_path or not os.path.exists(output_path):
                raise HTTPException(status_code=500, detail="Failed to generate EPUB.")

            # Cache the generated EPUB
            os.makedirs(cache_dir, exist_ok=True)
            import shutil
            shutil.copy2(output_path, cached_path)

        with open(cached_path, "rb") as f:
            epub_bytes = f.read()
        return StreamingResponse(
            io.BytesIO(epub_bytes),
            media_type="application/epub+zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # HTML — generate a proper HTML document with chapter structure
    if format == "html":
        # Build chapter list for TOC
        ch_list = []
        for ch_meta in chapters:
            ch = _entity_manager.get_chapter(book_id=book_id, chapter_number=ch_meta["chapter"])
            if not ch:
                continue
            ch_list.append({
                "number": ch_meta["chapter"],
                "title": ch.get("title", f"Chapter {ch_meta['chapter']}"),
                "content": ch.get("content", []),
            })

        html_parts = [
            '<!DOCTYPE html>',
            '<html lang="en">',
            '<head>',
            f'<meta charset="utf-8"><title>{book_info["title"]}</title>',
            '<style>',
            'body { font-family: Georgia, serif; max-width: 42em; margin: 2em auto; padding: 0 1em; line-height: 1.7; color: #222; }',
            'h1 { text-align: center; margin: 1.5em 0 0.5em; }',
            'h2 { margin: 2em 0 0.5em; border-bottom: 1px solid #ccc; padding-bottom: 0.3em; }',
            'p { text-indent: 1.5em; margin: 0.4em 0; }',
            '.title-page { text-align: center; margin: 4em 0; }',
            '.title-page .author { font-size: 1.1em; color: #555; }',
            'nav { margin: 2em 0; }',
            'nav h2 { border-bottom: none; }',
            'nav ol { padding-left: 1.5em; }',
            'nav li { margin: 0.3em 0; }',
            'nav a { color: #2563eb; text-decoration: none; }',
            'nav a:hover { text-decoration: underline; }',
            '</style>',
            '</head><body>',
            '<div class="title-page">',
            f'<h1>{book_info["title"]}</h1>',
            f'<p class="author">{book_info["author"]}</p>',
            '</div>',
            '<nav><h2>Table of Contents</h2><ol>',
        ]
        for ch_data in ch_list:
            anchor = f"chapter-{ch_data['number']}"
            html_parts.append(f'<li><a href="#{anchor}">{ch_data["title"]}</a></li>')
        html_parts.append('</ol></nav>')

        for ch_data in ch_list:
            anchor = f"chapter-{ch_data['number']}"
            html_parts.append(f'<h2 id="{anchor}">{ch_data["title"]}</h2>')
            for line in ch_data["content"]:
                stripped = line.strip()
                if not stripped:
                    continue
                safe = stripped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                html_parts.append(f'<p>{safe}</p>')

        html_parts.append('</body></html>')
        filename = f"{book['title'].replace(' ', '_')}.html"
        return StreamingResponse(
            io.BytesIO("\n".join(html_parts).encode("utf-8")),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Text / markdown — return as plain text file
    all_lines = []
    for ch_meta in chapters:
        ch = _entity_manager.get_chapter(book_id=book_id, chapter_number=ch_meta["chapter"])
        if ch:
            all_lines.extend(ch.get("content", []))
            all_lines.append("")

    ext_map = {"text": "txt", "markdown": "md"}
    ext = ext_map.get(format, "txt")
    filename = f"{book['title'].replace(' ', '_')}.{ext}"
    content_str = "\n".join(all_lines)

    return StreamingResponse(
        io.BytesIO(content_str.encode("utf-8")),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
