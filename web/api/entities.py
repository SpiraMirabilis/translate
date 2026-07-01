"""
Entity management endpoints.
"""
import json
import re
import sqlite3
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
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
    action: str  # "substitute" | "requeue" | "pronoun_repair"
    from_chapter: Optional[int] = None  # only affect chapters >= this number
    # When True (substitute only): first sweep the book for chapters whose
    # source text actually contains the entity's Chinese, and restrict the
    # replacement to that subset. Mirrors correct_entity_translation.py's
    # --safer-substitute — avoids rewriting unrelated occurrences of the old
    # English string in chapters that don't feature the entity.
    safer: bool = False
    old_gender: Optional[str] = None
    new_gender: Optional[str] = None


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
    origin_chapter: Optional[int] = Query(None),
):
    conn = _entity_manager.get_connection()
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
    if origin_chapter is not None:
        query += " AND origin_chapter = ?"
        params.append(origin_chapter)

    query += " ORDER BY category, untranslated"

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"entities": rows}


@router.get("/origin-chapters")
async def list_origin_chapters(book_id: int = Query(...)):
    """Return distinct origin_chapter values that have entities for this book."""
    conn = _entity_manager.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT origin_chapter FROM entities WHERE book_id = ? AND origin_chapter IS NOT NULL ORDER BY origin_chapter",
        (book_id,),
    )
    chapters = [row[0] for row in cursor.fetchall()]
    conn.close()
    return {"chapters": chapters}


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
    conn = _entity_manager.get_connection()
    cursor = conn.cursor()

    # Check exists and get book_id and current translation
    cursor.execute("SELECT id, book_id, translation FROM entities WHERE id = ?", (entity_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entity not found.")
    entity_book_id = row[1]
    current_translation = row[2]

    updates = {}
    if req.translation is not None:
        updates["translation"] = req.translation
        # Auto-record the previous translation as incorrect_translation when it
        # actually changes, so downstream tools (substitution audits, redo
        # scripts) can find what was edited. The standalone scripts do this too.
        # Caller may override by sending an explicit incorrect_translation.
        if req.incorrect_translation is None and current_translation and req.translation != current_translation:
            updates["incorrect_translation"] = current_translation
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


class DecaseRequest(BaseModel):
    translation: str
    book_id: int


@router.post("/decase")
async def decase_entity(req: DecaseRequest):
    """
    Lowercase all mid-sentence occurrences of a capitalised translation
    in the translated content of every chapter in the given book.
    Preserves capitalisation at sentence starts and after quotation marks / 【.
    """
    word = req.translation
    if not word or word[0].islower():
        return {"status": "ok", "chapters_changed": 0, "substitutions": 0}

    lowered = word[0].lower() + word[1:]

    # Collect translations of other entities (book-specific + global) that
    # *contain* this word — occurrences of the word inside these compound
    # phrases must keep their capitalisation. The entity being deleted is
    # still in the DB at this point, so it is excluded via `t != word`.
    protected = set()
    all_entities = _entity_manager.get_all_entities_for_review(book_id=req.book_id)
    for ents in all_entities.values():
        for data in ents.values():
            t = data.get("translation", "")
            if t and t != word and word in t:
                protected.add(t)

    conn = _entity_manager.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, translated_content FROM chapters WHERE book_id = ?",
        (req.book_id,),
    )
    rows = cursor.fetchall()

    total_subs = 0
    chapters_changed = 0
    pattern = re.compile(re.escape(word) + r'\b' if word[-1].isalpha() else re.escape(word))

    for ch_id, raw_content in rows:
        try:
            lines = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError):
            continue

        changed = False
        new_lines = []
        for line in lines:
            # Character spans occupied by protected compound phrases in this
            # line — word matches fully inside one of these are left alone.
            protected_spans = []
            for phrase in protected:
                i = line.find(phrase)
                while i != -1:
                    protected_spans.append((i, i + len(phrase)))
                    i = line.find(phrase, i + 1)

            def replacer(m):
                pos = m.start()
                for s, e in protected_spans:
                    if s <= pos and m.end() <= e:
                        return m.group(0)
                if line[:pos].strip() == '':
                    return m.group(0)
                if pos > 0 and line[pos - 1] in '"\u201c\u2018\'\u3010':
                    return m.group(0)
                i = pos - 1
                while i >= 0 and line[i] == ' ':
                    i -= 1
                if i >= 0 and line[i] in '.!?':
                    return m.group(0)
                return lowered + m.group(0)[len(word):]

            new_line = pattern.sub(replacer, line)
            if new_line != line:
                changed = True
                total_subs += 1
            new_lines.append(new_line)

        if changed:
            chapters_changed += 1
            cursor.execute(
                "UPDATE chapters SET translated_content = ? WHERE id = ?",
                (json.dumps(new_lines, ensure_ascii=False), ch_id),
            )

    conn.commit()
    conn.close()
    return {"status": "ok", "chapters_changed": chapters_changed, "substitutions": total_subs}


@router.delete("/{entity_id}")
async def delete_entity(entity_id: int):
    conn = _entity_manager.get_connection()
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

    conn = _entity_manager.get_connection()
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

    conn = _entity_manager.get_connection()
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
    conn = _entity_manager.get_connection()
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
    conn = _entity_manager.get_connection()
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
async def propagate_change(req: PropagateRequest, background_tasks: BackgroundTasks):
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

    conn = _entity_manager.get_connection()
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

    # Get chapters for this book (optionally from a specific chapter forward)
    if req.from_chapter is not None:
        cursor.execute(
            "SELECT id, chapter_number, title, untranslated_content, translated_content FROM chapters WHERE book_id = ? AND chapter_number >= ?",
            (book_id, req.from_chapter),
        )
    else:
        cursor.execute(
            "SELECT id, chapter_number, title, untranslated_content, translated_content FROM chapters WHERE book_id = ?",
            (book_id,),
        )
    chapters = cursor.fetchall()

    if req.action == "substitute":
        if not req.old_translation or req.old_translation == req.new_translation:
            conn.close()
            return {"status": "ok", "affected": 0}

        # Safer mode: restrict to chapters whose source (untranslated) text
        # contains the entity's Chinese — the same subset
        # correct_entity_translation.py --safer-substitute targets. The source
        # is JSON-encoded with ensure_ascii=False (or, for legacy rows, plain
        # text), so the Chinese substring appears literally either way.
        candidates = None
        if req.safer:
            chapters = [
                ch for ch in chapters
                if ch["untranslated_content"] and untranslated in ch["untranslated_content"]
            ]
            candidates = len(chapters)

        pattern = re.compile(re.escape(req.old_translation), re.IGNORECASE)
        old_words = req.old_translation.split()
        new_words = req.new_translation.split()

        def match_case(match):
            # Preserve *positional* casing — the capitalization a word picks up
            # from where it sits (sentence-start capital, all-caps headings) —
            # by comparing each matched word against the canonical old word and
            # re-applying only that shift to the new word. The old translation's
            # *own* casing is NOT preserved: a pure case correction (e.g.
            # "azure sword" → "Azure Sword") is applied as written. Comparing
            # the new word against the matched chapter text instead would make
            # such a correction reproduce the old casing and silently no-op.
            chapter_words = match.group().split()
            transformed = []
            for idx, (old_w, new_w) in enumerate(
                zip_longest(old_words, new_words, fillvalue="")
            ):
                if not new_w:
                    continue
                if not old_w:
                    # New translation has more words than the old one; the extra
                    # words have no positional reference, so use them verbatim.
                    transformed.append(new_w)
                    continue
                chapter_w = chapter_words[idx] if idx < len(chapter_words) else old_w
                # The guards ensure a branch fires only for a genuine case
                # *shift*; when the old word is already in that form the new
                # word is used verbatim (preserving internal caps, "HeavenNet").
                if chapter_w == old_w.upper() and not old_w.isupper():
                    transformed.append(new_w.upper())
                elif chapter_w == old_w[0].upper() + old_w[1:] and not old_w[0].isupper():
                    transformed.append(new_w[0].upper() + new_w[1:])
                elif chapter_w == old_w[0].lower() + old_w[1:] and not old_w[0].islower():
                    transformed.append(new_w[0].lower() + new_w[1:])
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
        result = {"status": "ok", "affected": affected}
        if candidates is not None:
            result["candidates"] = candidates
        return result

    elif req.action == "requeue":
        # Build an auto-generated retranslation reason from whatever actually
        # changed on the entity (translation and/or gender), so the model knows
        # exactly what was corrected.
        old_t = (req.old_translation or "").strip()
        new_t = (req.new_translation or "").strip()
        old_g = (req.old_gender or "").strip().lower() or None
        new_g = (req.new_gender or "").strip().lower() or None
        translation_changed = bool(new_t) and old_t != new_t
        gender_changed = old_g != new_g

        clauses = []
        if translation_changed and old_t:
            clauses.append(
                f"The entity \"{untranslated}\" was previously translated as "
                f"\"{old_t}\" but has been corrected to \"{new_t}\"."
            )
        elif translation_changed and not old_t:
            clauses.append(
                f"The entity \"{untranslated}\" now has a canonical translation "
                f"(\"{new_t}\")."
            )
        if gender_changed:
            # Humanize None -> "unspecified" for readability
            def _g(v):
                return v if v else "unspecified"
            clauses.append(
                f"The character entity \"{untranslated}\" gender has changed "
                f"from {_g(old_g)} to {_g(new_g)}. Please use the appropriate "
                f"pronouns and gendered language consistently."
            )

        if clauses:
            auto_reason = " ".join(clauses)
            if translation_changed and not gender_changed:
                auto_reason += " Please use the corrected translation consistently throughout this chapter."
        else:
            auto_reason = (
                f"The entity \"{untranslated}\" was edited. Please re-check its "
                f"translation and any related terminology throughout this chapter."
            )

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
                    retranslation_reason=auto_reason,
                )
                affected += 1

        conn.close()
        return {"status": "ok", "affected": affected}

    elif req.action == "pronoun_repair":
        # Surgical pronoun fix: scan chapters mentioning the entity, send each
        # paragraph context window to a small classifier model, splice corrections
        # back into translated_content. Runs as a background task so the POST
        # returns immediately; results are written to the activity log.
        new_g = (req.new_gender or "").strip().lower()
        if new_g not in ("male", "female", "neutral"):
            conn.close()
            raise HTTPException(
                status_code=400,
                detail=f"pronoun_repair requires new_gender to be male/female/neutral; got {req.new_gender!r}",
            )
        translation = (req.new_translation or req.old_translation or "").strip()
        # Quick chapter count for the response
        candidates = _entity_manager.find_chapters_using_entity(untranslated, book_id=book_id)
        n_candidates = len(candidates)
        conn.close()

        background_tasks.add_task(
            _run_pronoun_repair,
            req.entity_id,
            new_g,
            translation,
            book_id,
        )
        return {"status": "started", "affected": n_candidates}

    else:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")


def _run_pronoun_repair(entity_id: int, target_gender: str, translation: str, book_id: int) -> None:
    """BackgroundTasks entry point. Runs the repair, writes activity log entry."""
    import logging
    log = logging.getLogger(__name__)
    try:
        from pronoun_repair import repair_pronouns_for_entity
        result = repair_pronouns_for_entity(_entity_manager, entity_id, target_gender)
        msg = (
            f"Pronoun repair: {result['paragraphs_changed']} paragraph"
            f"{'s' if result['paragraphs_changed'] != 1 else ''} corrected across "
            f"{result['chapters_changed']} chapter"
            f"{'s' if result['chapters_changed'] != 1 else ''} for {translation} "
            f"({result['windows_examined']} windows examined)"
        )
        if result["errors"]:
            msg += f"; {len(result['errors'])} window error(s)"
        _entity_manager.add_activity_log(
            type="pronoun_repair",
            message=msg,
            book_id=book_id,
            entities=[translation] if translation else None,
        )
    except Exception as e:
        log.exception("pronoun_repair background task failed")
        try:
            _entity_manager.add_activity_log(
                type="pronoun_repair_error",
                message=f"Pronoun repair failed for {translation or f'entity #{entity_id}'}: {e}",
                book_id=book_id,
                entities=[translation] if translation else None,
            )
        except Exception:
            pass
