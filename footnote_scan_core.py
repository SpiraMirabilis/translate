"""Shared core of the footnote-candidate scanner.

Everything both consumers need lives here: the system prompt, the model call
(with 529-overload retry), the hallucination filter that anchors every
candidate in the chapter's own source text, the entity-glossary builder, the
already-covered registry, and the first-mention dedupe helpers.

Consumers:
  * footnote_scan.py — the bulk CLI (collect / review / report / export / prune)
  * modules/footnote_scan_module.py — per-chapter background scan on ingest

Candidates are stored in the main DB (footnote_candidates / footnote_scans,
via db/footnote_candidates_repo.py). This is a COLLECTOR ONLY — nothing here
ever modifies chapters or the real footnotes table; applying approved
candidates stays a separate, manual step (add_footnotes.py).
"""

import hashlib
import json
import os
import re
import threading
import time

DEFAULT_MODEL = "deepseek:deepseek-v4-pro"

# A book's custom scan prompt lives in the footnote_scan module's settings.
# Keep these in sync with modules/footnote_scan_module.py (tests assert it).
SCAN_MODULE_ID = "footnote_scan"
PROMPT_SETTING = "system_prompt"
MODE_SETTING = "scan_mode"

# When a chapter gets scanned. "ingest" is the historical behaviour: a second
# model call on its own, after the chapter is first saved. "translation" folds
# the scan into the translation call — same rules, same anchoring, same tables,
# but the candidates ride back on the translation response instead of costing a
# second pass. Per book, because it is a real trade: inline pays the translation
# model's rate and adds the scan rules to every chunk of the prompt, in exchange
# for not re-sending the chapter, the glossary and the book's notes.
#
# MODE_INLINE is the DEFAULT — a book with no scan_mode row of its own scans
# during translation. "ingest" is now the opt-out, for a book that wants the
# scan on a cheaper model than its translator, or off the translation prompt
# entirely. Changing this default changes behaviour for every such book at
# once, which is the point: the second pass was the exception, not the rule.
MODE_INGEST = "ingest"
MODE_INLINE = "translation"
SCAN_MODES = (MODE_INGEST, MODE_INLINE)

# ── the model call ────────────────────────────────────────────────────────────

# The rules themselves are prose a human revises — dropping a test, widening the
# scope to another language — so they live in a FILE next to the genre prompts
# rather than in a Python string literal here, where they were revised less
# often than they should have been. The file is the built-in prompt: what the
# bulk CLI sends, what a book inherits when it has no override of its own, and
# what the module's "Load built-in prompt" button offers as a starting copy.
# The OUTPUT paragraphs below stay code-owned — the parser and the
# hallucination filter depend on their shape, so they are not the file's to set
# any more than they are a book's.
SCAN_PROMPT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "prompts",
    "footnote_scan_prompt.txt")

_rules_cache = {"mtime": None, "text": ""}


class ScanPromptError(RuntimeError):
    """The built-in scan rules could not be read and no override was given."""


def stock_scan_rules(strict=False):
    """The built-in scan RULES, read from :data:`SCAN_PROMPT_FILE`.

    Re-read whenever the file's mtime changes, so editing the prompt reaches
    the running processes without a restart (the trick settings_store plays on
    settings.json). The last text that read cleanly is kept if the file later
    goes missing or is briefly truncated mid-save. ``strict`` raises when there
    is nothing to send: a scan with no rules would come back confident junk,
    which is worse than a scan that fails loudly.
    """
    try:
        mtime = os.path.getmtime(SCAN_PROMPT_FILE)
        if mtime != _rules_cache["mtime"]:
            with open(SCAN_PROMPT_FILE, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                _rules_cache["mtime"], _rules_cache["text"] = mtime, text
    except OSError:
        pass
    if strict and not _rules_cache["text"]:
        raise ScanPromptError(
            f"Footnote scan prompt file is missing or empty: {SCAN_PROMPT_FILE}")
    return _rules_cache["text"]


# Import-time snapshot, kept because callers (and tests) refer to the stock
# rules as a constant. Anything that builds a prompt goes through
# ``scan_rules`` / ``stock_scan_rules`` instead, so a file edited after startup
# is picked up. Never raises at import: a broken install must not take the
# module registry — and with it the whole web app — down with it.
SCAN_RULES = stock_scan_rules()

# Where the candidates go. Split off SCAN_RULES and appended from code, for the
# same reason the translation contract lives in prompt_contract: a book may
# replace the rules wholesale (see below), and the output shape is the one part
# of this prompt that the parser and the hallucination filter depend on — it
# must not be something a book can edit away by accident. It is appended LAST,
# so it also supersedes an output paragraph left over in a prompt that was
# customised before the split.
OUTPUT_STANDALONE = """\
OUTPUT: respond with ONLY a JSON array; each element is
  {"term_zh": "...", "term_en": "...", "body": "...", "sentence": "..."}
where "sentence" is the source sentence containing the referent, copied
verbatim. No prose, no code fences, no explanations."""

# The same channel riding the main translation response instead of a model call
# of its own (scan_mode "translation"). The rules above are written for a
# standalone scan, so this restates the two things that differ there: where the
# candidates go, and what "the entity glossary sent with the chapter" means when
# the glossary is already in the translator's prompt.
OUTPUT_INLINE = """\
OUTPUT: you are producing these candidates as part of your translation response,
not as a reply of your own. Your JSON must ALWAYS carry a top-level
"footnote_candidates" array, a sibling of "entities" — an empty array when this
chapter genuinely holds nothing, never a missing key. Each element is
  {"term_zh": "...", "term_en": "...", "body": "...", "sentence": "..."}
where "sentence" is the source sentence containing the referent, copied
verbatim. Never reply with a bare array.
This is a real pass over the chapter, not an afterthought: apply the COVERAGE
rule above as written, working through the whole passage rather than noting
whatever happened to catch your eye while translating. Translating the chapter
does not discharge it. The translation itself must be exactly what it would
have been without this task.
Where the rules above refer to an entity glossary sent with the chapter, they
mean the PRE-TRANSLATED ENTITIES block earlier in this prompt."""

# The standalone prompt, unchanged in assembled form: the bulk CLI's
# --print-prompt, the module's "Load built-in prompt" button and the on-ingest
# scan all still see exactly what they saw before the split.
SYSTEM_PROMPT = SCAN_RULES + "\n\n" + OUTPUT_STANDALONE

# A book may replace SYSTEM_PROMPT outright (footnote_scan module setting).
# The prompt above is written for a Chinese web novel and hard-codes its scope
# ("DO NOT FLAG non-Chinese referents"), which some books need to change — a
# novel set in the Japanese game industry wants Japanese referents too, and
# no amount of appending reliably undoes a rule stated that flatly. Whatever a
# book substitutes must still keep the ANCHORING rule and the JSON output
# shape: the hallucination filter and parse_model_response depend on them.


# A prompt customised before the rules/output split still carries an output
# paragraph of its own. It is stripped here so the code-owned one is the only
# instruction the model sees. Both known forms are covered — the stock wording
# and the Markdown "# OUTPUT" heading one book rewrote it into. The section is
# by convention the last thing in the prompt, hence the anchor to the end; the
# "JSON array" guard keeps an OUTPUT section that means something else intact.
_CUSTOM_OUTPUT_RE = re.compile(
    r"\n+(?:#+[ \t]*)?OUTPUT\b[^\n]*\n.*\Z", re.S | re.I)


def scan_rules(custom=None):
    """The scan RULES to send: a book's custom prompt, else the stock rules.

    Empty/whitespace-only ``custom`` means "no override", and the built-in
    rules are re-read from the prompt file (see ``stock_scan_rules``) rather
    than taken from the import-time snapshot. Whatever comes back carries no
    output instructions — those are appended by ``resolve_system_prompt`` and
    are not a book's to change."""
    text = (custom or "").strip()
    if not text:
        return stock_scan_rules(strict=True)
    stripped = _CUSTOM_OUTPUT_RE.sub("", text)
    if stripped.strip() and "json array" in text[len(stripped):].lower():
        return stripped.rstrip()
    return text


def resolve_system_prompt(custom=None, inline=False):
    """The complete scan system prompt: rules (custom or stock) plus the
    code-owned output paragraph for the mode being run.

    ``inline=True`` targets the footnote_candidates channel on the main
    translation response; the default targets a standalone scan call."""
    return scan_rules(custom) + "\n\n" + (OUTPUT_INLINE if inline
                                            else OUTPUT_STANDALONE)


def book_scan_prompt(db, book_id):
    """A book's stored custom scan prompt, or "" when it has none.

    Read straight from the module settings row so the CLI honors the same
    per-book setting the on-ingest module does, without importing the module
    registry. Never raises — a missing table or a malformed row means "no
    override", not a failed scan."""
    try:
        stored = db.get_module_settings(book_id, SCAN_MODULE_ID) or {}
    except Exception:  # noqa: BLE001 - settings must never break a scan
        return ""
    return str(stored.get(PROMPT_SETTING) or "").strip()


def strip_code_fence(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("\n")
        raw = "\n".join(parts[1:-1]) if len(parts) > 2 else raw
        if raw.startswith("json"):
            raw = raw[4:].strip()
    return raw


def parse_model_response(raw):
    """Parse the model's reply into a clean list of candidate dicts.

    Raises json.JSONDecodeError / ValueError on garbage; silently drops
    malformed items (non-dicts, empty body) from an otherwise-valid array."""
    data = json.loads(strip_code_fence(raw))
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        body = str(item.get("body") or "").strip()
        if not body:
            continue
        out.append({
            "term_zh": str(item.get("term_zh") or "").strip(),
            "term_en": str(item.get("term_en") or "").strip(),
            "body": body,
            "sentence": str(item.get("sentence") or "").strip(),
        })
    return out


# ── hallucination filter ──────────────────────────────────────────────────────
#
# Cheap models invent referents outright — a plausible-sounding 典故 that is
# simply not on the page. A candidate we cannot ANCHOR in the chapter's own
# source is dropped before it is printed or stored, so a fabrication never
# reaches the reviewer at all.
#
# Anchoring is deliberately not a literal substring test on term_zh, because the
# best candidates in this genre are precisely the ones that don't survive one:
#   薅羊毛              is on the page as 顺手薅一薅公司的羊毛   (words wedged in)
#   诛九族              is on the page as 朕定要诛尔等九族
#   放下屠刀，立地成佛   is on the page as 拿起屠刀，誓不成佛     (the parody — the
#                       whole point of the footnote)
# Measured over ~4.5k collected candidates, requiring term_zh verbatim would
# have deleted ~60 referents of that kind to catch 2 true fabrications. So the
# model's quoted source sentence counts as an anchor too: it is copied verbatim
# from the chapter, and a model that quotes the real page is reading it, not
# inventing. What's left with no anchor at all is what we drop.

_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
_NON_CJK_RE = re.compile(r"[^㐀-䶿一-鿿]+")
# The model joins co-referents in one term ("伯乐 / 千里马", "阴鱼／阳鱼").
_TERM_SPLIT_RE = re.compile(r"[/／、,，]|\.{3}|…")

# Only idiom-length terms may earn the one-character-swap pass; shorter ones
# would fuzzy-match almost anything.
MIN_SWAP_LEN = 4


def _fold(s):
    """Fold for substring matching: drop punctuation, whitespace and case. The
    model re-punctuates freely (、 for ，, ellipses, quote marks), and a term or
    sentence can straddle a line break in the source."""
    return _NON_WORD_RE.sub("", s or "").casefold()


def _cjk_only(s):
    return _NON_CJK_RE.sub("", s or "")


def _one_swap_away(term, text):
    """Does text contain a window equal to term but for a single character?

    The twisted-idiom case the prompt hunts for: the page says 先涨后奏 and the
    model reports the idiom it puns on, 先斩后奏. The referent IS in the
    chapter — one character over."""
    n = len(term)
    if n < MIN_SWAP_LEN:
        return False
    return any(sum(a != b for a, b in zip(term, text[i:i + n])) == 1
               for i in range(len(text) - n + 1))


def candidate_in_source(cand, folded_text, cjk_text):
    """Can this candidate be anchored in the chapter? (folded_text and cjk_text
    are the chapter source run through _fold / _cjk_only.) Any one of:

      - term_zh occurs in the source, or is one character away from something
        that does (parodied idiom);
      - every part of a multi-part term ("伯乐 / 千里马") occurs;
      - the source sentence the model was told to copy occurs verbatim, which
        is what rescues an idiom the page states discontinuously.

    None of the above and the referent is nowhere in the chapter: a fabrication.
    """
    term = cand.get("term_zh") or ""
    zh = _cjk_only(term)
    if zh:
        if zh in cjk_text or _one_swap_away(zh, cjk_text):
            return True
        parts = [_cjk_only(p) for p in _TERM_SPLIT_RE.split(term)]
        parts = [p for p in parts if p]
        if len(parts) > 1 and all(p in cjk_text for p in parts):
            return True
    elif _fold(term):                          # digits/latin, e.g. 996
        return _fold(term) in folded_text
    sentence = _fold(cand.get("sentence"))
    return bool(sentence) and sentence in folded_text


def verify_candidates(found, source_text):
    """Split a chapter's candidates into (kept, dropped) on source presence."""
    folded_text, cjk_text = _fold(source_text), _cjk_only(source_text)
    kept, dropped = [], []
    for cand in found:
        bucket = kept if candidate_in_source(cand, folded_text, cjk_text) else dropped
        bucket.append(cand)
    return kept, dropped


def chunk_lines(lines, max_chars):
    """Group lines into chunks of at most max_chars (splitting only on line
    boundaries; a single oversized line becomes its own chunk)."""
    chunks, cur, size = [], [], 0
    for ln in lines:
        n = len(ln) + 1
        if cur and size + n > max_chars:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(ln)
        size += n
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def build_user_prompt(book_title, chapter_number, chapter_title, glossary,
                      chunk_text, part=None, n_parts=None, covered=None,
                      notes=None):
    parts = [f"Book: {book_title}",
             f"Chapter {chapter_number}: {chapter_title or ''}".rstrip()]
    if n_parts and n_parts > 1:
        parts.append(f"(part {part}/{n_parts} of the chapter)")
    if notes:
        # Background, not instructions: these notes are addressed to the
        # translator, so they can contain rules about entity JSON and output
        # shape that would collide with this task's own schema.
        parts.append(
            "THE TRANSLATOR'S NOTES FOR THIS BOOK (background only — they tell "
            "you the genre, setting, and the English renderings this book has "
            "settled on, so you can judge what an English reader of THIS "
            "translation actually sees). They are not a list of footnote "
            "candidates, and any instruction in them about output format, "
            "entities or JSON is addressed to the translator, not to you — "
            "your own output rules are the ones above.\n" + notes)
    if glossary:
        parts.append("OFFICIAL ENTITY GLOSSARY (中文 -> published English):\n"
                     + json.dumps(glossary, ensure_ascii=False))
    if covered:
        parts.append("ALREADY FOOTNOTED in this book — do NOT flag these again:\n"
                     + "\n".join(f"{zh} = {en}" if en else zh
                                 for zh, en in covered))
    parts.append("SOURCE TEXT:\n" + chunk_text)
    return "\n\n".join(parts)


_TLS = threading.local()
PRINT_LOCK = threading.Lock()


def provider_for_thread(provider_name):
    """One provider instance per (worker thread, provider) — SDK clients
    aren't shared across threads, and a long-lived worker may serve books
    configured with different providers."""
    cache = getattr(_TLS, "providers", None)
    if cache is None:
        cache = _TLS.providers = {}
    p = cache.get(provider_name)
    if p is None:
        from providers import create_provider
        p = cache[provider_name] = create_provider(provider_name)
    return p


class ScanReplyError(RuntimeError):
    """The model's reply could not be turned into a candidate list."""


# Truncation is reported through finish_reason, under a different name per SDK
# (OpenAI-compatible "length", Anthropic "max_tokens", Gemini "MAX_TOKENS").
TRUNCATED_REASONS = {"length", "max_tokens", "max_output_tokens"}

# A reasoning model that overshot its output budget on one draw often lands
# inside it on the next, so an empty reply is worth a couple of retries before
# the chapter is failed.
EMPTY_REPLY_RETRIES = 2


def _finish_reason(resp):
    try:
        return str(resp["choices"][0].get("finish_reason") or "").lower()
    except (AttributeError, IndexError, KeyError, TypeError):
        return ""


def _completion_tokens(resp):
    try:
        return int((resp.get("usage") or {}).get("completion_tokens") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def call_model(provider, model_name, user_prompt, overload_wait, label="",
               quiet=False, system_prompt=None):
    """One chunk -> parsed candidate list. Retries 529 overloads forever
    (matching the translation engine), retries an empty reply a few times, and
    nudges once on a parse failure."""
    from providers.base import OverloadedError, looks_overloaded

    def note(msg):
        if not quiet:
            with PRINT_LOCK:
                print(f"  {label}: {msg}", flush=True)

    messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}]
    nudged = False
    empty_tries = 0
    while True:
        try:
            resp = provider.chat_completion(model=model_name, messages=messages,
                                            temperature=0.0)
            raw = provider.get_response_content(resp) or ""
        except OverloadedError:
            note(f"overloaded, waiting {overload_wait}s...")
            time.sleep(overload_wait)
            continue
        except Exception as e:
            if looks_overloaded(str(e), strict=False):
                note(f"overloaded, waiting {overload_wait}s...")
                time.sleep(overload_wait)
                continue
            raise
        if looks_overloaded(raw):
            time.sleep(overload_wait)
            continue
        if not raw.strip():
            # An empty reply is NOT malformed JSON — there is no reply at all.
            # A reasoning model can spend its entire output budget on hidden
            # reasoning tokens and stop before writing a visible character
            # (finish_reason "length"), so say that, instead of letting
            # json.loads("") report a mystifying "line 1 column 1 (char 0)".
            reason = _finish_reason(resp) or "unknown"
            spent = _completion_tokens(resp)
            empty_tries += 1
            if empty_tries <= EMPTY_REPLY_RETRIES:
                note(f"empty reply (finish_reason={reason}), retrying")
                continue
            hint = ""
            if reason in TRUNCATED_REASONS:
                hint = (" — the whole output budget went to reasoning; raise "
                        "max_output_tokens for this provider in "
                        "providers/models.json")
            raise ScanReplyError(
                f"model returned an empty reply {empty_tries}x "
                f"(finish_reason={reason}, {spent} completion tokens){hint}")
        try:
            return parse_model_response(raw)
        except (json.JSONDecodeError, ValueError) as e:
            if nudged:
                # Quote the reply: a bare parser message says nothing about
                # what the model actually sent.
                raise ScanReplyError(
                    f"unparseable reply (finish_reason="
                    f"{_finish_reason(resp) or 'unknown'}): {e} — reply began: "
                    f"{raw.strip()[:200]!r}") from e
            nudged = True
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "Return ONLY the JSON array — "
                                            "no prose, no code fences."}]


def overload_wait_seconds():
    return int(os.getenv("OVERLOAD_RETRY_WAIT_SECONDS", "300") or 300)


def source_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── book-specific notes (from the book's custom system prompt) ────────────────

_NOTES_HEADER_RE = re.compile(r"^BOOK-SPECIFIC NOTES:?[ \t]*$", re.M)


def extract_book_notes(template):
    """The BOOK-SPECIFIC NOTES section of a book's system prompt, or "".

    That trailing section is where the per-book conventions live (genre,
    setting, protagonist, established renderings), which is exactly the
    context the scanner otherwise lacks. The '//' lines are template
    boilerplate the translator strips before sending, so they go here too —
    which also disposes of the commented-out '// BOOK-SPECIFIC NOTES' banner
    above the real header.
    """
    if not template:
        return ""
    body = "\n".join(ln for ln in template.splitlines()
                     if not ln.lstrip().startswith("//"))
    starts = [m.end() for m in _NOTES_HEADER_RE.finditer(body)]
    if not starts:
        return ""
    # The section runs to the end of the prompt; if a book somehow repeats the
    # header, the last one owns the tail.
    return body[starts[-1]:].strip()


def book_notes(db, book_id):
    """Book-specific notes for a book, or "" when it has no custom prompt
    (get_book_prompt_template returns None) or the notes section is empty."""
    return extract_book_notes(db.get_book_prompt_template(book_id))


# ── entity glossary (read-only — do_count=False never touches last_chapter,
#    and save_entities() is never called) ─────────────────────────────────────

def entity_glossary(db, entities_by_cat, source_lines, chapter_number):
    """{中文: published English} for every entity literally present in the
    chapter's source, via the translation engine's own matcher."""
    glossary = {}
    for cat, ents in entities_by_cat.items():
        if not ents:
            continue
        res = db.entities_inside_text(source_lines, ents, chapter_number,
                                      do_count=False)
        for zh, data in res.get("exact", {}).items():
            tr = (data.get("translation") or "").strip()
            if tr:
                glossary[zh] = tr
    return glossary


# ── already-covered terms ─────────────────────────────────────────────────────

_CJK_RE = re.compile(r"[一-鿿]")
_CJK_PAREN_RE = re.compile(r"[（(]\s*([^）)]*[一-鿿][^）)]*?)\s*[）)]")


def pairs_from_footnote_rows(rows):
    """(中文, English) pairs from real footnotes-table rows. The Chinese form
    comes from source_term when set, from the anchor itself when it's CJK,
    else from the house-style "(中文)" parenthetical in the body."""
    pairs = []
    for f in rows:
        anchor = (f.get("anchor") or "").strip()
        zh = (f.get("source_term") or "").strip()
        if not zh and _CJK_RE.search(anchor):
            zh, anchor = anchor, ""
        if not zh:
            m = _CJK_PAREN_RE.search(f.get("body") or "")
            zh = m.group(1).strip() if m else ""
        if zh:
            pairs.append((zh, anchor))
    return pairs


class CoveredRegistry:
    """Thread-safe running list of terms that already have a footnote.

    Two tiers: the book's REAL footnotes (excluded everywhere — the book has
    them, period) and candidates from earlier chapters (excluded only for
    chapters after theirs, so re-scanning a chapter regenerates its own
    suggestions and reading-order first-mention is preserved). The main thread
    add()s each chapter's finds as it completes; chapters running concurrently
    in the same worker wave miss each other, which is accepted — the
    first-mention dedupe (and the post-run cleanup) absorb those."""

    def __init__(self, real_pairs, candidate_rows):
        self._lock = threading.Lock()
        self._real = list(real_pairs)                    # [(zh, en)]
        self._cands = [(ch, zh, en) for ch, zh, en in candidate_rows if zh]

    def add(self, chapter, found):
        with self._lock:
            for f in found:
                if f.get("term_zh"):
                    self._cands.append((chapter, f["term_zh"], f["term_en"]))

    def covered_for(self, chapter, text):
        """The (zh, en) pairs to exclude for this chapter, limited — like the
        entity glossary — to terms literally present in the chapter text."""
        with self._lock:
            snapshot = self._real + [(zh, en) for ch, zh, en in self._cands
                                     if ch < chapter]
        seen, out = set(), []
        for zh, en in snapshot:
            if zh not in seen and zh in text:
                seen.add(zh)
                out.append((zh, en))
        return out


def covered_registry_for_book(db, book_id):
    """A CoveredRegistry seeded from the book's real footnotes and every
    already-collected candidate."""
    return CoveredRegistry(
        pairs_from_footnote_rows(db.get_book_footnotes(book_id)),
        [(r["chapter_number"], r["term_zh"], r["term_en"])
         for r in db.list_footnote_candidates(book_id)])


# ── review helpers (shared by CLI report/export/review and the web API) ──────

def first_mention_key(row):
    """Dedupe key: the source term when present, else the English (folded)."""
    return (row.get("term_zh") or "").strip() or \
        (row.get("term_en") or "").strip().lower()


def dedupe_first_mention(rows):
    """(firsts, repeats) — rows must already be ordered chapter, id.
    repeats maps the first-mention row id -> list of its later duplicates."""
    firsts, repeats, seen = [], {}, {}
    for r in rows:
        k = first_mention_key(r) or f"__id{r['id']}"
        if k in seen:
            repeats.setdefault(seen[k]["id"], []).append(r)
        else:
            seen[k] = r
            firsts.append(r)
    return firsts, repeats


def footnoted_anchors(db, book_id):
    """Lower-cased anchors already in the book's REAL footnotes table."""
    return {(f.get("anchor") or "").strip().lower()
            for f in db.get_book_footnotes(book_id)} - {""}


def term_in_translation(db, book_id, term):
    """Cheap presence check of an English term in the book's translated text."""
    conn = db.backend.get_connection()
    try:
        cur = conn.cursor()
        esc = term.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        cur.execute(
            "SELECT 1 FROM chapters WHERE book_id = ? AND translated_content"
            " LIKE ? ESCAPE '!' LIMIT 1", (book_id, f"%{esc}%"))
        return cur.fetchone() is not None
    finally:
        conn.close()


def dedup_candidates(db, book_id):
    """Post-run cleanup: delete later PENDING repeats of a term already
    collected at an earlier chapter (concurrent workers in one wave can't see
    each other, so a term found in ch5 and ch7 lands twice). Hand-reviewed
    rows (accepted/rejected) are never touched. Returns rows deleted."""
    rows = db.list_footnote_candidates(book_id)
    _, repeats = dedupe_first_mention(rows)
    doomed = [d for dups in repeats.values() for d in dups
              if d["status"] == "pending"]
    if doomed:
        db.delete_footnote_candidates([d["id"] for d in doomed])
        for cn in {d["chapter_number"] for d in doomed}:
            db.update_footnote_scan_count(book_id, cn)
    return len(doomed)


# ── inline scan (the translation pass collects the candidates) ───────────────
#
# Everything the standalone scan builds a user prompt out of, the translator
# already has: the chapter source is what it is translating, the entity glossary
# is the PRE-TRANSLATED ENTITIES block, and the book's conventions are its own
# system prompt. Only one thing is missing — the list of referents this book has
# already footnoted — so that is all this injects, alongside the rules.

def inline_prompt_section(rules, covered=None):
    """The FOOTNOTE CANDIDATES block for a translation system prompt."""
    parts = ["FOOTNOTE CANDIDATES:", rules]
    if covered:
        parts.append("ALREADY FOOTNOTED in this book — do NOT flag these again:\n"
                     + "\n".join(f"{zh} = {en}" if en else zh
                                 for zh, en in covered))
    parts.append(OUTPUT_INLINE)
    return "\n\n".join(parts)


def inline_enabled(book, config=None, db=None, ctx=None):
    """Whether this book collects footnote candidates during translation.

    Three things must agree: the global kill switch, the module being enabled
    for the book, and its scan_mode. Never raises — a settings lookup that
    fails means "not inline", not a failed translation.
    """
    if not getattr(config, "footnote_inline_scan", True):
        return False
    try:
        from modules import module_config
        enabled, settings = module_config(book, SCAN_MODULE_ID, db=db, ctx=ctx)
    except Exception:  # noqa: BLE001 - never break a translation over this
        return False
    return bool(enabled) and \
        (settings.get(MODE_SETTING) or MODE_INLINE) == MODE_INLINE


def inline_scan_section(db, book, config, source_text, chapter_number=None,
                        ctx=None):
    """The prompt section for this chapter, or "" when inline scanning is off.

    Built once per chapter and handed to generate_system_prompt, which is called
    again for every chunk — the covered-terms lookup is two queries and has no
    business running per chunk.
    """
    if not inline_enabled(book, config=config, db=db, ctx=ctx):
        return ""
    book_id = book.get("id")
    try:
        rules = scan_rules(book_scan_prompt(db, book_id))
        registry = covered_registry_for_book(db, book_id)
        # An unknown chapter number means every collected candidate counts as
        # prior: better to under-propose a repeat than to re-propose one.
        covered = registry.covered_for(
            chapter_number if chapter_number else float("inf"), source_text)
        return inline_prompt_section(rules, covered)
    except Exception:  # noqa: BLE001 - a missing exclusion list is not fatal
        try:
            return inline_prompt_section(scan_rules(None))
        except ScanPromptError:
            # No rules to send at all (prompt file missing): no section, so the
            # translation prompt is simply the one it was before inline scanning.
            return ""


def prior_mention_keys(db, book_id, chapter_number):
    """Dedupe keys for every referent this book has already settled BEFORE this
    chapter: real footnotes, plus candidates collected at an earlier chapter
    whatever their status.

    A rejected earlier row counts — the decision was "not this one", and asking
    again 900 chapters later is the same wasted review. Mirrors what
    dedup_candidates does for the bulk CLI.
    """
    keys = {first_mention_key({"term_zh": zh, "term_en": en})
            for zh, en in pairs_from_footnote_rows(db.get_book_footnotes(book_id))}
    limit = chapter_number if isinstance(chapter_number, int) else float("inf")
    for row in db.list_footnote_candidates(book_id):
        if row.get("chapter_number") is not None and row["chapter_number"] < limit:
            keys.add(first_mention_key(row))
    keys.discard("")
    return keys


def persist_inline_candidates(db, book_id, chapter_number, chapter_title,
                              model, source_text, found):
    """Store candidates collected by a translation pass. Returns rows kept.

    Records the scan row too, so the bulk CLI treats the chapter as scanned and
    doesn't spend a second pass re-finding what the translator already found.
    Reviewed rows are preserved: unlike the on-ingest scan, this path runs again
    on every retranslation, and a keep/reject decision must outlive one.

    Three filters, in order: the referent must be anchorable in this chapter's
    own source; a term found twice in one chapter (two chunks, say) collapses to
    its first mention; and a term this book already settled at an EARLIER
    chapter is dropped outright. That last one is the hard half of the
    first-mention rule — the ALREADY FOOTNOTED block in the prompt only *asks*
    the model, and the standalone scanner backstops it with a dedup_candidates
    sweep at the end of a run that this path has no equivalent of.
    """
    source = (source_text or "").strip()
    if not source:
        return []
    # An empty list is a real result: the model looked and found nothing. It
    # still earns a scan row, which is what stops the bulk CLI re-scanning the
    # chapter forever. (A model that never opened the channel doesn't reach
    # here — see ui.py::_store_footnote_candidates.)
    kept, _ = verify_candidates(found or [], source)
    # Within this chapter: first mention wins.
    kept = dedupe_first_mention(
        [dict(c, id=i) for i, c in enumerate(kept)])[0]
    kept = [{k: v for k, v in c.items() if k != "id"} for c in kept]
    # Against the rest of the book: anything already settled earlier is gone.
    try:
        prior = prior_mention_keys(db, book_id, chapter_number)
    except Exception:  # noqa: BLE001 - a missing dedupe is not worth a failure
        prior = set()
    if prior:
        kept = [c for c in kept if first_mention_key(c) not in prior]
    db.record_footnote_scan(book_id, chapter_number, chapter_title, model,
                            source_hash(source), kept, preserve_reviewed=True)
    return kept


# ── single-chapter scan (the module's on-ingest path) ─────────────────────────

def scan_single_chapter(db, config, book, chapter_number, model_spec=None,
                        max_chars=20000, force=False, registry=None,
                        quiet=True, notes=None, system_prompt=None):
    """Scan ONE chapter's source and persist the results in the main DB.

    Honors the scan-row guard: an unchanged, already-scanned chapter is
    skipped (returns None). Returns (kept, dropped) after persisting.
    Read-only towards entities/chapters; writes only the candidate tables.

    ``system_prompt`` replaces the stock scan prompt; None looks the book's
    own up from its module settings, "" forces the stock prompt.
    """
    from footnotes import content_to_list

    book_id = book["id"]
    book_title = book.get("title") or f"book {book_id}"
    model_spec = model_spec or DEFAULT_MODEL

    ch = db.get_chapter(book_id=book_id, chapter_number=chapter_number)
    lines = content_to_list(ch.get("untranslated")) if ch else []
    source = "\n".join(lines)
    if not source.strip():
        return None
    content_hash = source_hash(source.strip())

    prev = db.get_footnote_scans(book_id).get(chapter_number)
    if prev is not None and not force and prev["content_hash"] == content_hash:
        return None

    entities_by_cat = db.reload_entities(book_id)
    glossary = entity_glossary(db, entities_by_cat, lines, chapter_number)
    if registry is None:
        registry = covered_registry_for_book(db, book_id)
    covered = registry.covered_for(chapter_number, source)
    if notes is None:
        notes = book_notes(db, book_id)
    if system_prompt is None:
        system_prompt = book_scan_prompt(db, book_id)
    system_prompt = resolve_system_prompt(system_prompt)

    provider_name, model_name = config.parse_model_spec(model_spec)
    provider = provider_for_thread(provider_name)
    overload_wait = overload_wait_seconds()

    chunks = chunk_lines(lines, max_chars)
    found = []
    for i, chunk in enumerate(chunks, 1):
        prompt = build_user_prompt(book_title, chapter_number,
                                   ch.get("title"), glossary, chunk,
                                   part=i, n_parts=len(chunks), covered=covered,
                                   notes=notes)
        found.extend(call_model(provider, model_name, prompt, overload_wait,
                                label=f"ch{chapter_number}", quiet=quiet,
                                system_prompt=system_prompt))
    # Verify against the WHOLE chapter, not the chunk: a referent the model saw
    # in part 2 is still legitimately "in the chapter".
    kept, dropped = verify_candidates(found, source)
    db.record_footnote_scan(book_id, chapter_number, ch.get("title"),
                            model_spec, content_hash, kept)
    registry.add(chapter_number, kept)
    return kept, dropped
