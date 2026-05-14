"""
Surgical pronoun repair for translated chapters.

When a character entity's gender is set/changed, this module scans the
translated content of chapters that mention the character, finds paragraphs
containing the character's name, and uses a small classifier model (default
claude:claude-haiku-4-5) to identify and rewrite any wrong-gender pronouns
referring to that character. Pronouns referring to other characters are
preserved.

This is the precise alternative to the "flag chapters for retranslation"
propagation action — much cheaper, faster, and only touches what's wrong.

Public entry point:
    repair_pronouns_for_entity(db_manager, entity_id, target_gender, ...)

Internals mirror the original kuang_tianqing_repair.py pipeline (scan ->
marked window -> classifier -> splice) but parameterized on character name
and target gender.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


VALID_GENDERS = ("male", "female", "neutral")

PRONOUN_SETS = {
    "male":    {"subject": "he",   "object": "him",  "poss_det": "his",   "poss_pron": "his",   "reflexive": "himself"},
    "female":  {"subject": "she",  "object": "her",  "poss_det": "her",   "poss_pron": "hers",  "reflexive": "herself"},
    "neutral": {"subject": "they", "object": "them", "poss_det": "their", "poss_pron": "theirs","reflexive": "themself"},
}


def _system_prompt(character_name: str, target_gender: str) -> str:
    p = PRONOUN_SETS[target_gender]
    pronouns = f"{p['subject']}/{p['object']}/{p['poss_det']}/{p['poss_pron']}/{p['reflexive']}"
    return f"""You are an editor verifying English-language pronoun usage in a translated novel.

The character "{character_name}" is {target_gender.upper()}. The correct pronouns for this character are: {pronouns}. In some passages a translator may have used wrong-gender pronouns when referring to them. Your job is to fix only those errors.

You will receive a passage with one paragraph wrapped in <<<TARGET>>> ... <<<END_TARGET>>> markers. The surrounding paragraphs are context for disambiguation — DO NOT return them. Only return a corrected version of the TARGET paragraph.

Determine whether any pronoun in the TARGET paragraph that refers to "{character_name}" (also called by partial-name forms when unambiguous) does NOT match the {target_gender} set. If so, replace ONLY those pronouns with the correct {target_gender} equivalent. Preserve capitalization. Do not change pronouns referring to other characters. Do not change any other words, punctuation, or whitespace. Do not add or remove text.

If a pronoun is inside quoted speech or thought and its referent is genuinely ambiguous (could be {character_name} or could be a different character), leave it alone — return changed=false rather than guess.

If no change is needed (already correct, or the TARGET paragraph contains no pronoun referring to this character), return changed=false and leave corrected_paragraph empty.

Return STRICT JSON matching this schema:
{{"changed": boolean, "corrected_paragraph": string, "reason": string}}

- changed: true only if you actually rewrote something in the target paragraph.
- corrected_paragraph: the full rewritten target paragraph (verbatim except for the pronoun fixes) when changed=true, otherwise "".
- reason: one short sentence explaining your decision."""


def parse_last_json_object(text: str) -> dict:
    """
    Find every balanced top-level {...} block in `text` and return the last one
    that successfully parses as JSON. Tolerates the model's habit of writing
    initial JSON, then a "wait, let me reconsider" block, then corrected JSON.
    Raises json.JSONDecodeError if none parse.
    """
    objects = []
    depth = 0
    start = None
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start : i + 1])
                start = None
    last_err = None
    for blob in reversed(objects):
        try:
            return json.loads(blob)
        except json.JSONDecodeError as e:
            last_err = e
    if last_err:
        raise last_err
    raise json.JSONDecodeError("no JSON object found", text, 0)


def _build_marked_window(paragraphs: list, idx: int, ctx: int = 1) -> str:
    """Return a window with up to `ctx` non-empty paragraphs of context on each side,
    and the target paragraph wrapped in <<<TARGET>>>...<<<END_TARGET>>> markers."""
    def collect(direction: int) -> list:
        out = []
        i = idx + direction
        while 0 <= i < len(paragraphs) and len(out) < ctx:
            if paragraphs[i].strip():
                out.append(paragraphs[i])
            i += direction
        return out

    before = list(reversed(collect(-1)))
    after = collect(1)
    target = paragraphs[idx]
    parts = before + [f"<<<TARGET>>>\n{target}\n<<<END_TARGET>>>"] + after
    return "\n\n".join(parts)


def _model_spec() -> str:
    return os.getenv("PRONOUN_REPAIR_MODEL", "claude:claude-haiku-4-5").strip()


def _get_provider():
    """Initialize the model provider for the configured spec.

    Returns (provider_inst, model_name).

    PRONOUN_REPAIR_MODEL is expected to be in "provider:model" form. Bare
    aliases ("claude") are also accepted and map to the provider's default
    model (TranslationConfig.parse_model_spec would silently route bare
    aliases to OpenAI, so we bypass it for that case).
    """
    from config import TranslationConfig
    spec = _model_spec()
    if ":" in spec:
        provider_inst, model = TranslationConfig().get_client(spec)
        return provider_inst, model
    from providers.factory import create_provider, get_factory
    return create_provider(spec), get_factory().get_default_model(spec)


def _classify_window(provider_inst, model: str, character_name: str, target_gender: str,
                     marked_window: str, max_retries: int = 3) -> dict:
    """Single classifier call. Returns dict with 'changed', 'corrected_paragraph',
    'reason' on success, or {'error': str} on failure."""
    system = _system_prompt(character_name, target_gender)
    user_msg = f"Passage:\n\n{marked_window}"
    last_err = None
    for attempt in range(max_retries):
        try:
            response = provider_inst.chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_msg},
                ],
                model=model,
                temperature=0,
                max_tokens=2048,
            )
            content = provider_inst.get_response_content(response) or ""
            content = content.strip()
            data = parse_last_json_object(content)
            return {
                "changed": bool(data.get("changed", False)),
                "corrected_paragraph": data.get("corrected_paragraph", "") or "",
                "reason": data.get("reason", "") or "",
            }
        except json.JSONDecodeError as e:
            return {"error": f"json_decode: {e}"}
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    return {"error": f"api_error: {last_err}"}


def _paragraph_matchers(names: list[str]) -> list[re.Pattern]:
    """Compile case-sensitive word-boundary regexes for each name to scan paragraphs."""
    out = []
    for n in names:
        n = (n or "").strip()
        if not n:
            continue
        out.append(re.compile(r"\b" + re.escape(n) + r"\b"))
    return out


def repair_pronouns_for_entity(
    db_manager,
    entity_id: int,
    target_gender: str,
    *,
    chapter_numbers: Optional[list[int]] = None,
    extra_names: Optional[list[str]] = None,
    progress_cb: Optional[Callable[[int, int, int], None]] = None,
    dry_run: bool = False,
) -> dict:
    """
    Scan chapters mentioning an entity and surgically correct wrong-gender
    pronouns referring to that entity in the existing translations.

    Args:
        db_manager: A DatabaseManager instance.
        entity_id: ID of the entity (must have non-null book_id and a translation).
        target_gender: One of "male", "female", "neutral".
        chapter_numbers: Optional explicit list of chapter numbers to scan within
            the entity's book. If None, defaults to all chapters in the book that
            contain the entity (via find_chapters_using_entity).
        extra_names: Optional additional name forms to search for (e.g. partial
            names or nicknames). Defaults to None.
        progress_cb: Optional callback invoked as progress_cb(i, total, chapter_number).
        dry_run: If True, do NOT call save_chapter. Returned summary still contains
            per-chapter diffs for preview.

    Returns:
        Summary dict:
          {
            "entity_id", "book_id", "character_name", "target_gender",
            "chapters_scanned", "windows_examined",
            "paragraphs_changed", "chapters_changed",
            "errors": [{"chapter_number","paragraph_index","error"}],
            "diffs": [{"chapter_number","before","after","changed_indices"}]  # only when dry_run=True
          }
    """
    if target_gender not in VALID_GENDERS:
        raise ValueError(f"target_gender must be one of {VALID_GENDERS}; got {target_gender!r}")

    conn = db_manager.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT untranslated, translation, book_id FROM entities WHERE id = ?",
        (entity_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise ValueError(f"Entity {entity_id} not found")
    # Tolerate sqlite Row vs dict-like vs plain tuple
    if hasattr(row, "keys"):
        untranslated = row["untranslated"]
        character_name = (row["translation"] or "").strip()
        book_id = row["book_id"]
    else:
        untranslated, translation_raw, book_id = row[0], row[1], row[2]
        character_name = (translation_raw or "").strip()

    if not book_id:
        raise ValueError(f"Entity {entity_id} has no book_id (global entity); pronoun repair is per-book")
    if not character_name:
        raise ValueError(f"Entity {entity_id} has no translation; cannot scan for pronouns")

    # Gather candidate chapters
    if chapter_numbers is None:
        candidates = db_manager.find_chapters_using_entity(untranslated, book_id=book_id)
        chapter_numbers = sorted({c["chapter_number"] for c in candidates if c.get("chapter_number") is not None})
    chapter_numbers = sorted(set(chapter_numbers))

    # Provider init (early, so we fail fast if API key missing)
    try:
        provider_inst, model = _get_provider()
    except Exception as e:
        raise RuntimeError(f"pronoun_repair: provider init failed: {e}") from e

    # Build name matchers
    names = [character_name]
    if extra_names:
        names.extend(extra_names)
    matchers = _paragraph_matchers(names)

    summary = {
        "entity_id": entity_id,
        "book_id": book_id,
        "character_name": character_name,
        "target_gender": target_gender,
        "chapters_scanned": 0,
        "windows_examined": 0,
        "paragraphs_changed": 0,
        "chapters_changed": 0,
        "errors": [],
    }
    if dry_run:
        summary["diffs"] = []

    total = len(chapter_numbers)
    for i, cn in enumerate(chapter_numbers):
        if progress_cb:
            try:
                progress_cb(i, total, cn)
            except Exception:
                pass
        chapter = db_manager.get_chapter(book_id=book_id, chapter_number=cn)
        if not chapter:
            continue
        summary["chapters_scanned"] += 1

        content = chapter.get("content")
        if isinstance(content, list):
            paragraphs = [str(p) for p in content]
        elif isinstance(content, str):
            paragraphs = content.split("\n")
        else:
            continue

        original_paragraphs = list(paragraphs)
        changed_indices = []

        for idx, para in enumerate(paragraphs):
            if not any(m.search(para) for m in matchers):
                continue
            summary["windows_examined"] += 1
            marked = _build_marked_window(paragraphs, idx, ctx=1)
            result = _classify_window(provider_inst, model, character_name, target_gender, marked)
            if "error" in result:
                summary["errors"].append({
                    "chapter_number": cn,
                    "paragraph_index": idx,
                    "error": result["error"],
                })
                continue
            if not result.get("changed"):
                continue
            corrected = result.get("corrected_paragraph", "")
            if not corrected:
                continue
            paragraphs[idx] = corrected
            changed_indices.append(idx)

        if not changed_indices:
            continue

        summary["chapters_changed"] += 1
        summary["paragraphs_changed"] += len(changed_indices)

        if dry_run:
            summary["diffs"].append({
                "chapter_number": cn,
                "before": original_paragraphs,
                "after": paragraphs,
                "changed_indices": changed_indices,
            })
        else:
            db_manager.save_chapter(
                book_id=book_id,
                chapter_number=cn,
                title=chapter["title"],
                untranslated_content=chapter["untranslated"],
                translated_content=paragraphs,
                summary=chapter.get("summary"),
                translation_model=chapter.get("model"),
            )

    if progress_cb:
        try:
            progress_cb(total, total, None)
        except Exception:
            pass

    return summary
