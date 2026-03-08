"""
Book and chapter management endpoints.
"""
import io
import os
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List

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


class BookUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    description: Optional[str] = None


class PromptUpdate(BaseModel):
    template: str


class ChapterContentUpdate(BaseModel):
    content: List[str]


class ChapterProofreadUpdate(BaseModel):
    is_proofread: bool


# ------------------------------------------------------------------
# Books CRUD
# ------------------------------------------------------------------

@router.get("")
async def list_books():
    books = _entity_manager.list_books()
    return {"books": books or []}


@router.post("")
async def create_book(req: BookCreate):
    book_id = _entity_manager.create_book(
        title=req.title,
        author=req.author,
        language=req.language or "en",
        source_language=req.source_language or "zh",
        description=req.description,
    )
    if not book_id:
        raise HTTPException(status_code=500, detail="Failed to create book.")
    return {"id": book_id, "title": req.title}


# ------------------------------------------------------------------
# Prompt template (literal paths MUST come before /{book_id} routes)
# ------------------------------------------------------------------

@router.get("/default-prompt")
async def get_default_prompt():
    """Return the default system prompt template with {{ENTITIES_JSON}} and {{CHAPTER_NUMBER}} placeholders."""
    import json

    entities_json = {cat: {} for cat in ['characters', 'places', 'organizations', 'abilities', 'titles', 'equipment', 'creatures']}
    default = _translator.generate_system_prompt([], entities_json, do_count=False)
    # Replace the empty entities JSON with the placeholder
    default = default.replace(
        json.dumps(entities_json, ensure_ascii=False, indent=4),
        "{{ENTITIES_JSON}}"
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
    kwargs = {k: v for k, v in req.model_dump().items() if v is not None}
    success = _entity_manager.update_book(book_id, **kwargs)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update book.")
    return {"status": "ok"}


@router.delete("/{book_id}")
async def delete_book(book_id: int):
    book = _entity_manager.get_book(book_id=book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    success = _entity_manager.delete_book(book_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete book.")
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
        title=chapter.get("title", f"Chapter {chapter_number}"),
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
    import sqlite3
    conn = sqlite3.connect(_entity_manager.db_path)
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

    if format == "epub":
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

        filename = f"{book['title'].replace(' ', '_')}.epub"
        with open(output_path, "rb") as f:
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
