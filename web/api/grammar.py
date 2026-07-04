"""
Grammar/spell check (local LanguageTool server) + LLM polish pass.

Endpoints power the write editor's inline squiggles:
  GET  /api/grammar/status      — feature discovery (enabled? LT reachable?)
  POST /api/grammar/check       — proxy to LanguageTool /v2/check with
                                  entity-based known-term suppression
  POST /api/grammar/polish      — start an LLM line-editing pass as a
                                  background job; returns {job_id} immediately
  GET  /api/grammar/polish/jobs/{id}    — poll job status; when done, carries
                                  per-suggestion {find, replace, reason,
                                  status} rows persisted in polish_jobs /
                                  polish_suggestions
  GET  /api/grammar/polish/latest       — newest job for a chapter (editor
                                  re-attach on mount)
  PUT  /api/grammar/polish/suggestions/{id} — record accepted/dismissed
  POST /api/grammar/polish/jobs/{id}/dismiss-open — bulk-dismiss remainder
  POST /api/grammar/dictionary  — add a word to the 'dictionary' entity
                                  category (book-scoped, or global when
                                  book_id is null)

Handlers are sync `def` (anyio threadpool) — the provider SDK and the local
LT call are blocking; precedent: settings_api.test_api_key. The polish LLM
call itself runs in its own daemon thread so the POST returns immediately;
that keeps every request comfortably inside the Apache reverse proxy's
`Timeout 300` (no ProxyTimeout is set) no matter how slow the model is.
"""
import bisect
import json
import re
import threading
import unicodedata
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/grammar")

_entity_manager = None
_config = None

# Mirrors maxTextLength in deploy/languagetool-server.properties.
MAX_CHECK_CHARS = 150_000
# A single LLM call must stay well-formed; chunking is a documented follow-up.
MAX_POLISH_CHARS = 60_000
MAX_SUGGESTIONS = 50

LT_TIMEOUT = httpx.Timeout(connect=5, read=120, write=30, pool=10)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Categories whose matches read as stylistic advice rather than errors.
_STYLE_CATEGORIES = {"STYLE", "REDUNDANCY", "PLAIN_ENGLISH", "WIKIPEDIA"}
_STYLE_ISSUE_TYPES = {"style", "register", "locale-violation"}


def init(entity_manager, config):
    global _entity_manager, _config
    _entity_manager = entity_manager
    _config = config
    # Any 'running' polish job at startup is an orphan of a previous process.
    _entity_manager.fail_stale_polish_jobs()


# ------------------------------------------------------------------
# Known-term suppression (entity system as custom dictionary)
# ------------------------------------------------------------------

def _norm(word: str) -> str:
    w = unicodedata.normalize("NFC", word).strip()
    for suffix in ("’s", "'s"):
        if w.lower().endswith(suffix):
            w = w[: -len(suffix)]
            break
    return w.casefold()


def _known_terms(book_id: int) -> set:
    """Every entity translation (book + global) is a known-correct spelling.

    The set holds full translations plus their individual words (len >= 2),
    and for 'dictionary' rows the untranslated too (== translation by
    construction). Dictionary rows' English `untranslated` never matches
    Chinese source scans, so translation glossaries are unaffected by them.
    """
    terms = set()
    with _entity_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT translation, untranslated, category FROM entities "
            "WHERE book_id = ? OR book_id IS NULL",
            (book_id,),
        )
        for row in cursor.fetchall():
            for value in (row["translation"],
                          row["untranslated"] if row["category"] == "dictionary" else None):
                if not value:
                    continue
                terms.add(_norm(value))
                for word in re.split(r"[\s\u2010-\u2015-]+", value):
                    if len(word) >= 2:
                        terms.add(_norm(word))
    terms.discard("")
    return terms


def _is_spelling_rule(rule: dict) -> bool:
    category_id = ((rule.get("category") or {}).get("id") or "").upper()
    rule_id = (rule.get("id") or "").upper()
    return category_id == "TYPOS" or rule_id.startswith("MORFOLOGIK_RULE")


def _classify(rule: dict) -> str:
    if _is_spelling_rule(rule) or rule.get("issueType") == "misspelling":
        return "typo"
    category_id = ((rule.get("category") or {}).get("id") or "").upper()
    if category_id in _STYLE_CATEGORIES or rule.get("issueType") in _STYLE_ISSUE_TYPES:
        return "style"
    return "grammar"


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------

@router.get("/status")
def grammar_status():
    enabled = bool(getattr(_config, "grammar_check_enabled", False))
    up = False
    if enabled:
        try:
            with httpx.Client(timeout=2.0) as client:
                up = client.get(f"{_config.languagetool_url}/v2/languages").status_code == 200
        except httpx.HTTPError:
            up = False
    return {"enabled": enabled, "languagetool_up": up}


# ------------------------------------------------------------------
# Check
# ------------------------------------------------------------------

class CheckRequest(BaseModel):
    blocks: list
    book_id: Optional[int] = None
    language: Optional[str] = None


@router.post("/check")
def grammar_check(req: CheckRequest):
    if not getattr(_config, "grammar_check_enabled", False):
        raise HTTPException(status_code=503, detail="Grammar checking is disabled in Settings.")
    blocks = [b if isinstance(b, str) else "" for b in (req.blocks or [])]
    if not any(b.strip() for b in blocks):
        return {"language": req.language or _config.grammar_language, "matches": [], "filtered": 0}

    joined = "\n\n".join(blocks)
    if len(joined) > MAX_CHECK_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Text too large for grammar check (max {MAX_CHECK_CHARS:,} characters).")

    # Cumulative start offset of each block within `joined`.
    starts = []
    pos = 0
    for b in blocks:
        starts.append(pos)
        pos += len(b) + 2  # '\n\n'

    language = req.language or _config.grammar_language
    try:
        with httpx.Client(timeout=LT_TIMEOUT) as client:
            resp = client.post(
                f"{_config.languagetool_url}/v2/check",
                data={"language": language, "text": joined},
            )
    except httpx.HTTPError:
        raise HTTPException(
            status_code=503,
            detail=f"LanguageTool server unreachable at {_config.languagetool_url} — "
                   f"is the languagetool systemd service running?")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"LanguageTool error: {resp.text[:200]}")

    known = _known_terms(req.book_id) if req.book_id else set()
    matches = []
    filtered = 0
    for m in resp.json().get("matches", []):
        offset, length = m.get("offset", 0), m.get("length", 0)
        rule = m.get("rule") or {}

        block_idx = bisect.bisect_right(starts, offset) - 1
        in_block = offset - starts[block_idx]
        # Defensive: drop matches starting inside a separator or spanning blocks
        # (LT segments sentences at paragraph breaks, so this shouldn't happen).
        if in_block >= len(blocks[block_idx]) or in_block + length > len(blocks[block_idx]):
            continue

        if known and _is_spelling_rule(rule):
            token = joined[offset:offset + length]
            if _norm(token) in known:
                filtered += 1
                continue

        matches.append({
            "block": block_idx,
            "offset": in_block,
            "length": length,
            "message": m.get("message", ""),
            "shortMessage": m.get("shortMessage", ""),
            "replacements": [r.get("value", "") for r in (m.get("replacements") or [])[:5]],
            "ruleId": rule.get("id", ""),
            "categoryId": (rule.get("category") or {}).get("id", ""),
            "categoryName": (rule.get("category") or {}).get("name", ""),
            "type": _classify(rule),
        })
    return {"language": language, "matches": matches, "filtered": filtered}


# ------------------------------------------------------------------
# LLM polish pass
# ------------------------------------------------------------------

POLISH_SYSTEM_PROMPT = """You are a meticulous line editor for English prose fiction.
Propose the minimal set of edits that fix objective problems: grammar errors, tense
inconsistency, missing or duplicated words, wrong prepositions, subject-verb
disagreement, misused homophones, and awkward phrasing that impedes reading.

Rules:
- Preserve the author's voice, register, and pacing. Dialogue keeps its speaker's
  register (slang, stiffness, archaic diction) — never "correct" deliberate character voice.
- Never rename characters, places, techniques, or titles.{TERMS_RULE}
- Preserve existing punctuation style: em/en dashes, ellipses, brackets, honorifics,
  and emphasis markers stay as-is unless objectively wrong.
- Prefer the smallest possible change. Do not rewrite sentences for taste; skip purely
  subjective preferences.
- Each suggestion must be independently applicable via exact find-and-replace.

Respond with ONLY a JSON object, no other text:
{"suggestions": [{"find": "...", "replace": "...", "reason": "..."}]}

- "find" must be copied verbatim from the text (exact characters, punctuation, spacing)
  and long enough to be unique — include surrounding words if the phrase repeats.
- "replace" is the corrected text for exactly that span.
- "reason" is a short explanation, under 100 characters.
- Return at most 50 suggestions, ordered by position in the text.
- If nothing needs fixing, return {"suggestions": []}."""

TERMS_RULE = (" The following terms are canonical spellings for this work; "
              "never flag or alter them: {terms}")

# Dialect instruction keyed by grammar_language — keeps the polish model from
# "correcting" realise→realize (or vice versa) against the configured variant.
DIALECT_RULES = {
    "en-US": "The work uses American English spelling (color, realize); do not suggest British forms.",
    "en-GB": ("The work uses British English spelling (colour); both -ise and -ize verb forms are "
              "acceptable — never change one to the other."),
    "en-CA": "The work uses Canadian English spelling (colour, realize); preserve that convention.",
}
_COMMONWEALTH_RULE = ("The work uses British/Commonwealth English spelling with -ise verb forms "
                      "(colour, realise, organise); treat American spellings as errors to fix, and "
                      "never change -ise forms to -ize.")


def _dialect_rule(language: str) -> str:
    return DIALECT_RULES.get(language, _COMMONWEALTH_RULE)


class PolishRequest(BaseModel):
    text: str
    book_id: Optional[int] = None
    chapter_number: Optional[int] = None
    model: Optional[str] = None


def _canonical_terms(book_id: int, limit: int = 200) -> list:
    terms = []
    with _entity_manager._conn(dict_rows=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT translation FROM entities "
            "WHERE (book_id = ? OR book_id IS NULL) AND translation != '' "
            "ORDER BY LENGTH(translation) DESC",
            (book_id,),
        )
        for row in cursor.fetchall():
            terms.append(row["translation"])
            if len(terms) >= limit:
                break
    return terms


def _parse_suggestions(content: str, text: str) -> tuple:
    """Parse the model's JSON defensively. Returns (suggestions, truncated)."""
    if not content:
        raise ValueError("empty response")
    s = _JSON_FENCE_RE.sub("", content.strip()).strip()
    truncated = False
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        s = m.group(0)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        # Output may have been cut mid-object: walk closing braces backwards
        # and try treating each as the last complete suggestion.
        data = None
        truncated = True
        end = len(s)
        for _ in range(60):
            idx = s.rfind("}", 0, end)
            if idx == -1:
                break
            try:
                data = json.loads(s[:idx + 1] + "]}")
                break
            except json.JSONDecodeError:
                end = idx
        if data is None:
            raise ValueError("unparseable JSON")

    out = []
    for item in (data.get("suggestions") or [])[:MAX_SUGGESTIONS]:
        if not isinstance(item, dict):
            continue
        find = item.get("find")
        replace = item.get("replace")
        if not isinstance(find, str) or not isinstance(replace, str):
            continue
        if not find or find == replace:
            continue
        occurrences = text.count(find)
        if occurrences == 0:
            continue  # hallucinated span
        out.append({
            "find": find,
            "replace": replace,
            "reason": str(item.get("reason") or "")[:150],
            "occurrences": occurrences,
        })
    return out, truncated


def _polish_worker(job_id: int, text: str, system_prompt: str, provider, model_name: str):
    """Provider call + parse, off-request. All outcomes land on the job row."""
    try:
        response = provider.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            model=model_name,
            temperature=0,
            response_format={"type": "json_object"},
            # Polish is mechanical find/replace extraction — cap reasoning so
            # thinking-by-default models (sonnet-5) don't burn the whole output
            # budget on large chapters and return empty. Ignored by models that
            # don't reason. See providers.base.chat_completion.
            thinking_effort="low",
        )
        content = provider.get_response_content(response) or ""
    except Exception as e:
        _entity_manager.fail_polish_job(job_id, f"Polish call failed: {str(e)[:200]}")
        return

    try:
        suggestions, truncated = _parse_suggestions(content, text)
    except ValueError:
        _entity_manager.fail_polish_job(job_id, "Model returned invalid JSON.")
        return

    finish = ""
    try:
        finish = (response.get("choices") or [{}])[0].get("finish_reason") or ""
    except (AttributeError, TypeError):
        pass
    _entity_manager.finish_polish_job(job_id, suggestions,
                                      truncated=truncated or finish == "length")


@router.post("/polish")
def grammar_polish(req: PolishRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text to polish.")
    if len(text) > MAX_POLISH_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Text too long for a single polish pass "
                   f"(max {MAX_POLISH_CHARS:,} characters) — select a smaller range.")

    spec = req.model or getattr(_config, "polish_model", "claude:claude-sonnet-4-6")
    try:
        provider, model_name = _config.get_client(spec)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Polish model unavailable: {str(e)[:120]}")

    terms_rule = ""
    if req.book_id:
        terms = _canonical_terms(req.book_id)
        if terms:
            terms_rule = TERMS_RULE.format(terms=", ".join(terms))
    system_prompt = POLISH_SYSTEM_PROMPT.replace("{TERMS_RULE}", terms_rule)
    system_prompt += "\n\n" + _dialect_rule(getattr(_config, "grammar_language", "en-US"))

    job_id = _entity_manager.create_polish_job(
        req.book_id, req.chapter_number, spec, len(text))
    if job_id is None:
        raise HTTPException(status_code=500, detail="Failed to create polish job.")

    threading.Thread(
        target=_polish_worker,
        args=(job_id, text, system_prompt, provider, model_name),
        name=f"polish-job-{job_id}",
        daemon=True,
    ).start()
    return {"job_id": job_id, "status": "running", "model": spec}


@router.get("/polish/latest")
def polish_latest(book_id: int, chapter_number: int):
    """Newest job for a chapter (or JSON null) — the editor's re-attach probe."""
    return _entity_manager.latest_polish_job(book_id, chapter_number)


@router.get("/polish/jobs/{job_id}")
def polish_job(job_id: int):
    job = _entity_manager.get_polish_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Polish job not found.")
    return job


class SuggestionResolve(BaseModel):
    status: str  # 'accepted' | 'dismissed' | 'open' (undo)


@router.put("/polish/suggestions/{suggestion_id}")
def resolve_suggestion(suggestion_id: int, req: SuggestionResolve):
    if req.status not in ("open", "accepted", "dismissed"):
        raise HTTPException(status_code=400, detail="Invalid suggestion status.")
    if not _entity_manager.resolve_polish_suggestion(suggestion_id, req.status):
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    return {"status": "ok"}


@router.post("/polish/jobs/{job_id}/dismiss-open")
def dismiss_open(job_id: int):
    return {"dismissed": _entity_manager.dismiss_open_polish_suggestions(job_id)}


# ------------------------------------------------------------------
# Custom dictionary (entity category 'dictionary')
# ------------------------------------------------------------------

class DictionaryRequest(BaseModel):
    word: str
    book_id: Optional[int] = None  # null = global


@router.post("/dictionary")
def add_dictionary_word(req: DictionaryRequest):
    word = unicodedata.normalize("NFC", (req.word or "").strip())
    if not word:
        raise HTTPException(status_code=400, detail="Empty word.")
    if len(word) > 100:
        raise HTTPException(status_code=400, detail="Word too long.")
    if re.search(r"\s", word):
        raise HTTPException(status_code=400, detail="Dictionary entries must be a single word.")

    # NOTE: 'dictionary' is deliberately not in books.categories — the Entities
    # UI renders out-of-list categories as extra sections, and these rows'
    # English `untranslated` never matches Chinese source scans.
    ok = _entity_manager.add_entity(
        "dictionary", word, word,
        book_id=req.book_id,
        note="Added from grammar checker",
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to store dictionary word.")
    return {"status": "ok"}
