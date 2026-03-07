"""
Chinese dictionary lookup and retranslation endpoints.
"""
import sqlite3
import re
import os
import json
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter(prefix="/api/dict")

_db_path = None
_dict_db_path = None
_entity_manager = None
_translator = None


def init(entity_manager, translator=None):
    global _db_path, _dict_db_path, _entity_manager, _translator
    _db_path = entity_manager.db_path
    _entity_manager = entity_manager
    _translator = translator
    # Store dictionary in its own small DB alongside the main one
    _dict_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'cedict.db')
    _ensure_dict_loaded()


def _ensure_dict_loaded():
    """Load CC-CEDICT into SQLite if not already done."""
    if os.path.exists(_dict_db_path):
        # Check if it has data
        conn = sqlite3.connect(_dict_db_path)
        count = conn.execute("SELECT COUNT(*) FROM cedict").fetchone()[0]
        conn.close()
        if count > 0:
            return

    cedict_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'cedict_ts.u8')
    if not os.path.exists(cedict_path):
        return

    conn = sqlite3.connect(_dict_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cedict (
            id INTEGER PRIMARY KEY,
            traditional TEXT NOT NULL,
            simplified TEXT NOT NULL,
            pinyin TEXT NOT NULL,
            definitions TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cedict_simplified ON cedict(simplified)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cedict_traditional ON cedict(traditional)")

    # Parse CC-CEDICT format: Traditional Simplified [pinyin] /def1/def2/
    pattern = re.compile(r'^(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+/(.+)/$')
    entries = []
    with open(cedict_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('#'):
                continue
            m = pattern.match(line.strip())
            if m:
                traditional, simplified, pinyin, defs = m.groups()
                entries.append((traditional, simplified, pinyin, defs))

    conn.executemany(
        "INSERT INTO cedict (traditional, simplified, pinyin, definitions) VALUES (?, ?, ?, ?)",
        entries,
    )
    conn.commit()
    conn.close()
    print(f"[dict] Loaded {len(entries)} CC-CEDICT entries into {_dict_db_path}")


@router.get("/lookup")
async def lookup(q: str = Query(..., min_length=1, max_length=50)):
    """
    Look up a Chinese string. Returns:
    - Exact matches (simplified or traditional)
    - For single characters: also returns compound words containing that character
    - Character decomposition info for single chars
    """
    if not _dict_db_path or not os.path.exists(_dict_db_path):
        raise HTTPException(status_code=503, detail="Dictionary not loaded.")

    conn = sqlite3.connect(_dict_db_path)
    conn.row_factory = sqlite3.Row

    results = {
        "query": q,
        "exact": [],
        "compounds": [],
        "characters": [],
    }

    # Exact matches
    rows = conn.execute(
        "SELECT traditional, simplified, pinyin, definitions FROM cedict "
        "WHERE simplified = ? OR traditional = ? ORDER BY length(simplified)",
        (q, q),
    ).fetchall()
    results["exact"] = [
        {
            "traditional": r["traditional"],
            "simplified": r["simplified"],
            "pinyin": r["pinyin"],
            "definitions": r["definitions"].split("/"),
        }
        for r in rows
    ]

    # For single characters, find compound words
    if len(q) == 1:
        compounds = conn.execute(
            "SELECT traditional, simplified, pinyin, definitions FROM cedict "
            "WHERE (simplified LIKE ? OR traditional LIKE ?) "
            "AND simplified != ? AND traditional != ? "
            "ORDER BY length(simplified) LIMIT 30",
            (f"%{q}%", f"%{q}%", q, q),
        ).fetchall()
        results["compounds"] = [
            {
                "traditional": r["traditional"],
                "simplified": r["simplified"],
                "pinyin": r["pinyin"],
                "definitions": r["definitions"].split("/"),
            }
            for r in compounds
        ]

        # Character info: Unicode code point, stroke-related info
        cp = ord(q)
        results["characters"] = [{
            "char": q,
            "unicode": f"U+{cp:04X}",
            "codepoint": cp,
        }]

    # For multi-char queries, also look up substrings (individual chars and bigrams)
    elif len(q) <= 6:
        # Look up each individual character
        chars = list(set(q))
        placeholders = ",".join("?" * len(chars))
        char_rows = conn.execute(
            f"SELECT traditional, simplified, pinyin, definitions FROM cedict "
            f"WHERE simplified IN ({placeholders}) OR traditional IN ({placeholders}) "
            f"ORDER BY simplified",
            chars + chars,
        ).fetchall()
        results["characters"] = [
            {
                "traditional": r["traditional"],
                "simplified": r["simplified"],
                "pinyin": r["pinyin"],
                "definitions": r["definitions"].split("/"),
            }
            for r in char_rows
        ]

    conn.close()
    return results


# ------------------------------------------------------------------
# Retranslation
# ------------------------------------------------------------------

class RetranslateRequest(BaseModel):
    text: str                          # Chinese text to retranslate
    context_before: List[str] = []     # surrounding lines for context
    context_after: List[str] = []
    model: str                         # provider:model format
    book_id: Optional[int] = None


@router.post("/retranslate")
async def retranslate(req: RetranslateRequest):
    """
    Retranslate a small selection of Chinese text using the specified model.
    Returns plain English text (not the full structured translation output).
    """
    if not _translator:
        raise HTTPException(status_code=503, detail="Translator not available.")

    try:
        provider, model_name = _translator.config.get_client(req.model)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid model: {e}")

    # Build a lightweight entity context
    entities = _entity_manager.entities.copy()
    # Filter to entities that appear in the text
    text_for_check = req.text + "\n".join(req.context_before) + "\n".join(req.context_after)
    relevant = {}
    for cat, ents in entities.items():
        for key, data in ents.items():
            if key in text_for_check:
                relevant.setdefault(cat, {})[key] = data

    entity_hint = ""
    if relevant:
        flat = []
        for cat, ents in relevant.items():
            for key, data in ents.items():
                tr = data.get('translation', data) if isinstance(data, dict) else data
                flat.append(f"  {key} -> {tr}")
        entity_hint = "\n\nKnown translations for entities in this text:\n" + "\n".join(flat)

    context_hint = ""
    if req.context_before:
        context_hint += "\n\nContext before (for reference, do NOT translate these):\n" + "\n".join(req.context_before[-3:])
    if req.context_after:
        context_hint += "\n\nContext after (for reference, do NOT translate these):\n" + "\n".join(req.context_after[:3])

    system_prompt = (
        "You are a Chinese-to-English translator. Translate the given Chinese text into natural, "
        "fluent English. Preserve the meaning and tone. Return ONLY the English translation, "
        "nothing else — no explanations, no annotations, no quotes."
        f"{entity_hint}{context_hint}"
    )

    user_text = req.text

    try:
        response = provider.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            model=model_name,
            stream=False,
        )
        translation = provider.get_response_content(response).strip()
        return {"translation": translation}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
