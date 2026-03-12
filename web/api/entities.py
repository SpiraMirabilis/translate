"""
Entity management endpoints.
"""
import sqlite3
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter(prefix="/api/entities")

_entity_manager = None
_translator = None


def init(entity_manager, translator):
    global _entity_manager, _translator
    _entity_manager = entity_manager
    _translator = translator


from database import DEFAULT_CATEGORIES
CATEGORIES = DEFAULT_CATEGORIES


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------

class EntityCreate(BaseModel):
    category: str
    untranslated: str
    translation: str
    book_id: Optional[int] = None
    gender: Optional[str] = None
    incorrect_translation: Optional[str] = None
    note: Optional[str] = None


class EntityUpdate(BaseModel):
    translation: Optional[str] = None
    category: Optional[str] = None
    gender: Optional[str] = None
    incorrect_translation: Optional[str] = None
    note: Optional[str] = None


class DuplicateResolveRequest(BaseModel):
    untranslated: str
    action: str  # "keep_one" | "rename" | "allow"
    keep_category: Optional[str] = None          # for keep_one
    renames: Optional[dict] = None               # for rename: {category: new_translation}
    book_id: Optional[int] = None                # scope resolution to a specific book


class BatchRequest(BaseModel):
    ids: List[int]
    action: str  # "delete" | "move_category" | "change_book"
    category: Optional[str] = None       # for move_category
    book_id: Optional[int] = None        # for change_book (None = global)


class ContextRadius(BaseModel):
    radius: int = 100


class AdviceRequest(BaseModel):
    untranslated: str
    translation: str
    category: str
    book_id: Optional[int] = None


class PropagateRequest(BaseModel):
    entity_id: int
    old_translation: str
    new_translation: str
    action: str  # "substitute" | "requeue"


# ------------------------------------------------------------------
# Entity listing
# ------------------------------------------------------------------

@router.get("")
async def list_entities(
    book_id: Optional[int] = Query(None),
    global_only: bool = Query(False),
    include_global: bool = Query(False),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    conn = sqlite3.connect(_entity_manager.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT id, category, untranslated, translation, last_chapter, gender, incorrect_translation, book_id, origin_chapter, note FROM entities WHERE 1=1"
    params = []

    if global_only:
        query += " AND book_id IS NULL"
    elif book_id is not None and include_global:
        query += " AND (book_id = ? OR book_id IS NULL)"
        params.append(book_id)
    elif book_id is not None:
        query += " AND book_id = ?"
        params.append(book_id)
    if category:
        query += " AND category = ?"
        params.append(category)
    if search:
        query += " AND (untranslated LIKE ? OR translation LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    query += " ORDER BY category, untranslated"

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"entities": rows}


# ------------------------------------------------------------------
# CRUD
# ------------------------------------------------------------------

@router.post("")
async def create_entity(req: EntityCreate):
    valid_cats = _entity_manager.get_book_categories(req.book_id) if req.book_id else CATEGORIES
    if req.category not in valid_cats:
        raise HTTPException(status_code=400, detail=f"Invalid category: {req.category}")
    result = _entity_manager.add_entity(
        req.category,
        req.untranslated,
        req.translation,
        book_id=req.book_id,
        gender=req.gender,
        incorrect_translation=req.incorrect_translation,
        note=req.note,
    )
    if not result:
        raise HTTPException(status_code=409, detail="Entity already exists or could not be created.")
    return {"status": "ok"}


@router.put("/{entity_id}")
async def update_entity(entity_id: int, req: EntityUpdate):
    conn = sqlite3.connect(_entity_manager.db_path)
    cursor = conn.cursor()

    # Check exists and get book_id for category validation
    cursor.execute("SELECT id, book_id FROM entities WHERE id = ?", (entity_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entity not found.")
    entity_book_id = row[1]

    updates = {}
    if req.translation is not None:
        updates["translation"] = req.translation
    if req.category is not None:
        valid_cats = _entity_manager.get_book_categories(entity_book_id) if entity_book_id else CATEGORIES
        if req.category not in valid_cats:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Invalid category: {req.category}")
        updates["category"] = req.category
    if req.gender is not None:
        updates["gender"] = req.gender
    if req.incorrect_translation is not None:
        updates["incorrect_translation"] = req.incorrect_translation
    if req.note is not None:
        updates["note"] = req.note

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [entity_id]
        cursor.execute(f"UPDATE entities SET {set_clause} WHERE id = ?", values)
        conn.commit()

    conn.close()
    return {"status": "ok"}


@router.delete("/{entity_id}")
async def delete_entity(entity_id: int):
    conn = sqlite3.connect(_entity_manager.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM entities WHERE id = ?", (entity_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Entity not found.")
    cursor.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ------------------------------------------------------------------
# Batch operations
# ------------------------------------------------------------------

@router.post("/batch")
async def batch_operation(req: BatchRequest):
    if not req.ids:
        raise HTTPException(status_code=400, detail="No entity IDs provided.")

    conn = sqlite3.connect(_entity_manager.db_path)
    cursor = conn.cursor()

    # Verify all IDs exist
    placeholders = ",".join("?" for _ in req.ids)
    cursor.execute(f"SELECT id FROM entities WHERE id IN ({placeholders})", req.ids)
    found = {row[0] for row in cursor.fetchall()}
    missing = set(req.ids) - found
    if missing:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Entity IDs not found: {sorted(missing)}")

    if req.action == "delete":
        cursor.execute(f"DELETE FROM entities WHERE id IN ({placeholders})", req.ids)
        affected = cursor.rowcount

    elif req.action == "move_category":
        if not req.category:
            conn.close()
            raise HTTPException(status_code=400, detail="category is required for move_category action.")
        cursor.execute(
            f"UPDATE entities SET category = ? WHERE id IN ({placeholders})",
            [req.category] + req.ids,
        )
        affected = cursor.rowcount

    elif req.action == "change_book":
        # book_id=None means move to global
        cursor.execute(
            f"UPDATE entities SET book_id = ? WHERE id IN ({placeholders})",
            [req.book_id] + req.ids,
        )
        affected = cursor.rowcount

    else:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

    conn.commit()
    conn.close()
    _entity_manager._load_entities()
    return {"status": "ok", "affected": affected}


# ------------------------------------------------------------------
# Entity context (surrounding text from origin chapter)
# ------------------------------------------------------------------

@router.get("/{entity_id}/context")
async def get_entity_context(entity_id: int, radius: int = Query(100)):
    """Get surrounding context for an entity from its origin chapter."""
    import json

    conn = sqlite3.connect(_entity_manager.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT untranslated, book_id, origin_chapter FROM entities WHERE id = ?", (entity_id,))
    entity = cursor.fetchone()
    if not entity:
        conn.close()
        raise HTTPException(status_code=404, detail="Entity not found.")

    if not entity["book_id"] or not entity["origin_chapter"]:
        conn.close()
        return {"context": None, "message": "No origin chapter recorded for this entity."}

    cursor.execute(
        "SELECT untranslated_content FROM chapters WHERE book_id = ? AND chapter_number = ?",
        (entity["book_id"], entity["origin_chapter"]),
    )
    chapter = cursor.fetchone()
    conn.close()

    if not chapter:
        return {"context": None, "message": "Origin chapter not found in database."}

    try:
        content = json.loads(chapter["untranslated_content"])
        full_text = "\n".join(content) if isinstance(content, list) else str(content)
    except (json.JSONDecodeError, TypeError):
        full_text = chapter["untranslated_content"] or ""

    untranslated = entity["untranslated"]
    idx = full_text.find(untranslated)
    if idx == -1:
        return {"context": None, "message": "Entity text not found in origin chapter."}

    start = max(0, idx - radius)
    end = min(len(full_text), idx + len(untranslated) + radius)
    snippet = full_text[start:end]

    return {"context": snippet, "untranslated": untranslated}


# ------------------------------------------------------------------
# Duplicate checking
# ------------------------------------------------------------------

@router.get("/duplicates")
async def get_duplicates(book_id: Optional[int] = Query(None), scope: Optional[str] = Query(None)):
    conn = sqlite3.connect(_entity_manager.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Build WHERE clause based on filters
    # scope=global means book_id IS NULL; book_id=N means book_id = N; neither means all
    where = ""
    params = []
    if scope == "global":
        where = " WHERE book_id IS NULL"
    elif book_id is not None:
        where = " WHERE book_id = ?"
        params = [book_id]

    # Duplicates by untranslated text within the same book (same Chinese, different categories)
    cursor.execute(f"""
        SELECT untranslated, book_id, COUNT(*) as count
        FROM entities{where}
        GROUP BY untranslated, book_id
        HAVING COUNT(*) > 1
        ORDER BY book_id, count DESC
    """, params)
    dup_untranslated = []
    for row in cursor.fetchall():
        cursor.execute(
            "SELECT id, category, translation, last_chapter FROM entities WHERE untranslated = ? AND book_id IS ? ORDER BY category",
            (row["untranslated"], row["book_id"]),
        )
        instances = [dict(r) for r in cursor.fetchall()]
        dup_untranslated.append({
            "untranslated": row["untranslated"],
            "book_id": row["book_id"],
            "count": row["count"],
            "instances": instances,
        })

    # Duplicates by translation within the same book (same English, different Chinese)
    cursor.execute(f"""
        SELECT translation, book_id, COUNT(*) as count
        FROM entities{where}
        GROUP BY translation, book_id
        HAVING COUNT(*) > 1
        ORDER BY book_id, count DESC
    """, params)
    dup_translations = []
    for row in cursor.fetchall():
        cursor.execute(
            "SELECT id, category, untranslated, last_chapter FROM entities WHERE translation = ? AND book_id IS ? ORDER BY category",
            (row["translation"], row["book_id"]),
        )
        instances = [dict(r) for r in cursor.fetchall()]
        dup_translations.append({
            "translation": row["translation"],
            "book_id": row["book_id"],
            "count": row["count"],
            "instances": instances,
        })

    conn.close()
    return {
        "duplicate_untranslated": dup_untranslated,
        "duplicate_translations": dup_translations,
    }


@router.post("/resolve-duplicate")
async def resolve_duplicate(req: DuplicateResolveRequest):
    conn = sqlite3.connect(_entity_manager.db_path)
    cursor = conn.cursor()

    if req.action == "keep_one":
        if not req.keep_category:
            conn.close()
            raise HTTPException(status_code=400, detail="keep_category required for keep_one action.")
        cursor.execute(
            "DELETE FROM entities WHERE untranslated = ? AND category != ? AND book_id IS ?",
            (req.untranslated, req.keep_category, req.book_id),
        )

    elif req.action == "delete_all":
        cursor.execute(
            "DELETE FROM entities WHERE untranslated = ? AND book_id IS ?",
            (req.untranslated, req.book_id),
        )

    elif req.action == "rename":
        if not req.renames:
            conn.close()
            raise HTTPException(status_code=400, detail="renames required for rename action.")
        for category, new_translation in req.renames.items():
            cursor.execute(
                "UPDATE entities SET translation = ? WHERE untranslated = ? AND category = ? AND book_id IS ?",
                (new_translation, req.untranslated, category, req.book_id),
            )

    else:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

    conn.commit()
    conn.close()
    _entity_manager._load_entities()
    return {"status": "ok"}


# ------------------------------------------------------------------
# LLM translation advice
# ------------------------------------------------------------------

@router.post("/advice")
async def get_advice(req: AdviceRequest):
    node = {
        "untranslated": req.untranslated,
        "translation": req.translation,
        "category": req.category,
    }
    try:
        advice = _translator.get_translation_options(node, [])
        return advice
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Propagate translation changes across chapters
# ------------------------------------------------------------------

@router.post("/propagate")
async def propagate_change(req: PropagateRequest):
    """
    After an entity translation is edited, propagate the change across all
    chapters belonging to the same book.

    action="substitute": find-and-replace old_translation with new_translation
                         in every chapter's translated content.
    action="requeue":    find chapters whose *untranslated* content contains the
                         entity's Chinese text, and add them back to the queue.
    """
    import json, re
    from itertools import zip_longest

    conn = sqlite3.connect(_entity_manager.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Look up the entity to get its untranslated text and book_id
    cursor.execute("SELECT untranslated, book_id FROM entities WHERE id = ?", (req.entity_id,))
    entity_row = cursor.fetchone()
    if not entity_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entity not found.")

    untranslated = entity_row["untranslated"]
    book_id = entity_row["book_id"]

    if book_id is None:
        conn.close()
        raise HTTPException(status_code=400, detail="Cannot propagate changes for global entities (no book_id).")

    # Get all chapters for this book
    cursor.execute(
        "SELECT id, chapter_number, title, untranslated_content, translated_content FROM chapters WHERE book_id = ?",
        (book_id,),
    )
    chapters = cursor.fetchall()

    if req.action == "substitute":
        if not req.old_translation or req.old_translation == req.new_translation:
            conn.close()
            return {"status": "ok", "affected": 0}

        pattern = re.compile(re.escape(req.old_translation), re.IGNORECASE)

        def match_case(match):
            matched_text = match.group()
            old_words = matched_text.split()
            new_words = req.new_translation.split()
            transformed = []
            for old_w, new_w in zip_longest(old_words, new_words, fillvalue=""):
                if old_w.isupper():
                    transformed.append(new_w.upper())
                elif old_w.istitle():
                    transformed.append(new_w.capitalize())
                elif old_w.islower():
                    transformed.append(new_w.lower())
                else:
                    transformed.append(new_w)
            return " ".join(transformed).strip()

        affected = 0
        for ch in chapters:
            try:
                content = json.loads(ch["translated_content"])
            except (json.JSONDecodeError, TypeError):
                continue

            changed = False
            for i in range(len(content)):
                new_line = pattern.sub(match_case, content[i])
                if new_line != content[i]:
                    content[i] = new_line
                    changed = True

            if changed:
                cursor.execute(
                    "UPDATE chapters SET translated_content = ? WHERE id = ?",
                    (json.dumps(content, ensure_ascii=False), ch["id"]),
                )
                affected += 1

        conn.commit()
        conn.close()
        return {"status": "ok", "affected": affected}

    elif req.action == "requeue":
        affected = 0
        for ch in chapters:
            try:
                raw = ch["untranslated_content"]
                untranslated_content = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                untranslated_content = [raw] if raw else []

            # Check if the entity's Chinese text appears in the untranslated content
            full_text = "\n".join(untranslated_content) if isinstance(untranslated_content, list) else str(untranslated_content)
            if untranslated in full_text:
                # Add to queue (content must be list for add_to_queue)
                content_list = untranslated_content if isinstance(untranslated_content, list) else full_text.split("\n")
                _entity_manager.add_to_queue(
                    book_id=book_id,
                    content=content_list,
                    title=ch["title"] or f"Chapter {ch['chapter_number']}",
                    chapter_number=ch["chapter_number"],
                    source="retranslation",
                )
                affected += 1

        conn.close()
        return {"status": "ok", "affected": affected}

    else:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
