"""Auto-fix for reader error reports: an LLM proposes, a human applies.

The admin's Error Reports page has an Auto-fix button. It sends the report, the
whole chapter (source and translation, line-numbered), the chapter's glossary
and the book's footnote conventions to `error_report_fix_model`, which answers
with a verdict and a list of surgical edits, new footnotes and gender
corrections. Nothing is written at that point: the proposal is validated here,
stored on the report row (`error_reports.autofix_result`) and previewed; the
admin applies all of it or a subset.

Why the shape is what it is:

- **Edits are find/replace inside one numbered line**, never whole-chapter
  rewrites, so a model can't quietly change anything it wasn't asked to and
  every change can be shown as a diff. `replace` may contain a newline (split a
  collapsed stat block); `find` = the whole line with an empty `replace` deletes
  the line (source ads).
- **The model works on clean prose** (`footnotes.strip_footnotes`): footnote
  markers and the definition block are a rendering of the `footnotes` table,
  re-applied by anchor on every `save_chapter`. A chapter whose rendered
  footnotes are *not* in the table (pre-migration inline footnotes) is edited
  with its markers in place instead, its definition block kept verbatim, and
  gets no new footnotes — stripping would delete those footnotes for good.
- **An edit that would orphan an existing footnote is rejected**: rewriting the
  anchored term would silently drop its note on the next render.
- **Footnotes go through `add_footnote` + `rerender_chapter_footnotes`**, the
  same pair `add_footnotes.apply_footnote_plan` uses, so numbering and the
  definition block come out exactly as for any other footnote.
- **Gender only, among entity changes.** A term rename is a book-wide job
  (`--substitute`), so the model may only *advise* it (`other_action`).
- **The proposal is pinned to the chapter's `translation_date`**; applying it to
  a chapter that changed since is refused (409 upstream) — re-run instead.
"""
from __future__ import annotations

import datetime
import difflib
import json
import logging
import re
from typing import Optional

from footnotes import (MARKER_RE, content_to_list, find_occurrence,
                       marker_position, occurrence_at, prose_end_index,
                       render_footnotes, split_prose_and_defs, strip_footnotes)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claudecode:claude-opus-5-5"
VERDICTS = ("fix", "no_change", "unclear", "needs_other_tool")
VALID_GENDERS = ("male", "female", "neutral")
MAX_EDITS = 60
MAX_FOOTNOTES = 5
MAX_FOOTNOTE_BODY = 1000
MAX_BOOK_ANCHORS = 400        # "already footnoted in this book" list in the prompt
STYLE_EXAMPLES = 3
NOTE_CHARS = 240              # glossary note truncation in the prompt

# Inline sentinels (⟦IMG:id⟧, ⟦U⟧…, ⟦COLOR:…⟧, ⟦FN⟧, table markers). An edit
# may move text around them but never add, drop or alter one.
SENTINEL_RE = re.compile(r"⟦[^⟧]*⟧")
_TOKEN_RE = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)


class AutofixError(Exception):
    """A run or an apply that can't proceed; the message is user-facing."""


class ChapterChanged(AutofixError):
    """The chapter was saved after the proposal was made."""


RESEARCH_MAX_TURNS = 10
RESEARCH_HINT = (
    "\n\nRESEARCH: read-only tools for this book are attached (see RESEARCH TOOLS). "
    "Use them when the report hinges on something established before this chapter: "
    "a character's gender or identity, how a term or phrase was rendered earlier, "
    "whether a referent was already footnoted, or what an earlier scene said. Check, "
    "then answer; don't research what the chapter and glossary above already settle."
)


def research_enabled() -> bool:
    try:
        import settings_store
        return bool(settings_store.get("error_report_fix_research", True))
    except Exception:
        return True


def research_kwargs(provider, ctx: dict, config=None) -> dict:
    """``{"mcp_tools": {...}}`` for a provider that can take them (claudecode,
    codex), else ``{}`` — OpenAI-compatible providers would forward an unknown
    kwarg to the API. Same shape TranslationEngine._mcp_tools_kwargs builds; the
    read-only server bounds reads at this chapter. Unreachable server → the
    provider drops the tools and the call goes out without them."""
    if not getattr(provider, "supports_mcp_tools", False) or not research_enabled():
        return {}
    from research_tools import scope_number
    try:
        scope_number(ctx["book_id"])
        scope_number(ctx["chapter_number"])
    except ValueError:
        return {}
    return {"mcp_tools": {
        "url": getattr(config, "claude_code_mcp_url", None) or "http://127.0.0.1:8766/mcp",
        "book_id": ctx["book_id"],
        "book_title": ctx["book"].get("title"),
        "chapter_number": ctx["chapter_number"],
        "max_turns": RESEARCH_MAX_TURNS,
        "task": "fixing a reader-reported error in",
    }}


def model_spec() -> str:
    try:
        import settings_store
        spec = (settings_store.get("error_report_fix_model", DEFAULT_MODEL) or "").strip()
    except Exception:
        spec = ""
    return spec or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Chapter context
# ---------------------------------------------------------------------------

def load_context(db, report: dict) -> dict:
    """Everything the prompt and the validators need, read once."""
    if report.get("chapter_number") is None:
        raise AutofixError("Book-wide reports can't be auto-fixed — there is no chapter to edit.")
    book_id, n = report["book_id"], report["chapter_number"]
    chapter = db.get_chapter(book_id=book_id, chapter_number=n)
    if not chapter:
        raise AutofixError(f"Chapter {n} no longer exists.")
    book = db.get_book(book_id=book_id) or {}

    lines = content_to_list(chapter.get("content"))
    all_rows = db.get_chapter_footnotes(chapter["id"], is_source=0)
    rows = [r for r in all_rows if r.get("status") != "orphaned"]
    prose_with_markers, defs = split_prose_and_defs(lines)
    # Rendered definitions with no table rows behind them: the pre-migration
    # inline footnotes. Strip them and they're gone, so keep them in place.
    untracked = len(defs) > len(rows)
    if untracked and all_rows:
        # Any save re-renders from the table (save_chapter's hook), which would
        # drop the untracked ones — there is no safe way to edit this chapter.
        raise AutofixError(
            f"Chapter {n} mixes tracked and untracked footnotes; run "
            f"backfill_footnotes.py --book-id {book_id} before auto-fixing it.")
    if untracked:
        prose = list(prose_with_markers)
        def_block = lines[len(prose_with_markers):]
    else:
        prose = strip_footnotes(lines)
        def_block = []

    # Display only (never edited): blank separator lines are noise in the prompt.
    source = [l for l in strip_footnotes(content_to_list(chapter.get("untranslated"))) if l.strip()]

    try:
        terms = db.get_chapter_terms(book_id, n) or []
    except Exception as e:  # an unindexed chapter just has no glossary panel
        logger.warning(f"autofix: no chapter terms for b{book_id} ch{n}: {e}")
        terms = []
    gendered = db.get_book_gendered_categories(book_id) or []

    book_footnotes = db.get_book_footnotes(book_id) or []

    return {
        "book": book,
        "book_id": book_id,
        "chapter_number": n,
        "chapter": chapter,
        "chapter_id": chapter["id"],
        "translation_date": chapter.get("translation_date"),
        "lines": lines,
        "prose": prose,
        "def_block": def_block,
        "untracked_footnotes": untracked,
        "chapter_footnote_rows": rows,
        "source": source,
        "terms": terms,
        "gendered_categories": gendered,
        "book_footnotes": book_footnotes,
    }


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the editor of a published English translation of a web novel. A reader has
reported an error. You receive the report, the chapter's source text (S lines), its
current English translation (T lines), the chapter's glossary, and the book's footnote
conventions. Decide whether the report is right, and if it is, propose the smallest
edits that fix it.

VERDICT — exactly one of:
- "fix": the report identifies a real problem in this chapter's English and you can fix it here.
- "no_change": the translation is correct or faithful to the source (e.g. a repeated
  passage that the source also repeats, a correct term the reader didn't know, a
  deliberate choice). Explain why, briefly, for the admin.
- "unclear": you can't tell which text the reader means or what they think is wrong.
- "needs_other_tool": the problem is real but can't be fixed by editing this chapter:
  a glossary term that should be renamed book-wide, a unit the unit converter doesn't
  know, source text missing from the raw itself. Say what is needed in "other_action".

EDIT RULES
- Fix only the reported problem, but fix EVERY instance of it in this chapter — e.g.
  every wrong-gender pronoun for that character, not only the quoted sentence.
- Use the glossary: its renderings are canonical, and its gender field is authoritative
  for pronouns. A pronoun that refers to a different character stays as it is.
- Check the source (S lines) before changing meaning. The T and S line numbers do not
  correspond 1:1; find the matching passage by content.
- Smallest possible change. Keep the prose's voice, register and punctuation style.
  Never rewrite a sentence for taste.
- Never add, remove or alter ⟦…⟧ markers{MARKER_RULE}.
- Each edit is {"line": T-line number, "find": exact text copied from that line,
  "replace": replacement, "reason": short}. "find" must occur exactly once in that
  line — include neighbouring words to make it unique. Several edits may target one
  line if they don't overlap.
- To split one line into several (collapsed formatting), put "\\n" in "replace".
- To delete a whole line (e.g. a website ad or promo copied from the raw), set "find"
  to the entire line and "replace" to "".

FOOTNOTES{FOOTNOTE_RULES}

ENTITY GENDER
- If the glossary's own gender for a character is wrong (the source makes it clear),
  add {"entity_id": id, "field": "gender", "value": "male|female|neutral", "reason": "..."}
  to "entity_updates". Never propose any other entity change; a rename belongs in
  "other_action".

OUTPUT — ONLY a JSON object, no other text:
{"verdict": "...", "explanation": "1-3 sentences for the admin's notes, saying what was wrong and what you changed (or why nothing should change)", "edits": [], "footnotes": [], "entity_updates": [], "other_action": ""}"""

_FOOTNOTE_RULES = """
- A footnote is a resolution when the reader stumbled over a correct rendering of a
  cultural, historical or real-world referent the prose can't carry by itself (a
  historical weapon class, an idiom with a source, a pun, a period institution).
  You may reword AND footnote (e.g. "firearms" → "gunpowder weapons" + a note on 火器).
- Each footnote is {"line": T-line number where the anchor is (after your edits are
  applied, numbered as before them), "anchor": the exact English words the marker
  attaches to, as they read AFTER your edits, "source_term": the source-language term,
  "body": the note, "reason": short}.
- Body format, the house style: "<English term> (<source term> <romanisation>): <gloss>".
  Plain, factual, one to three sentences. See the examples below.
- Never footnote story events or characters, never something already footnoted in
  this book, and never more than one footnote per term.
- At most {MAX_FOOTNOTES} footnotes."""

_FOOTNOTES_BLOCKED = """
- This chapter's existing footnotes are not yet tracked by the footnote system, so no
  new footnote can be added here. Leave "footnotes" empty; if one is the right fix,
  say so in "other_action"."""


def _fmt_term(t: dict) -> str:
    parts = [f"[id {t.get('id')}] {t.get('category')} | {t.get('untranslated')} → {t.get('translation')}"]
    if t.get("gender"):
        parts.append(f"gender: {t['gender']}")
    note = (t.get("note") or "").strip()
    if note:
        parts.append("note: " + (note[:NOTE_CHARS] + "…" if len(note) > NOTE_CHARS else note))
    return " | ".join(parts)


def build_messages(report: dict, ctx: dict) -> list:
    untracked = ctx["untracked_footnotes"]
    marker_rule = (" or [n] footnote markers (they must stay attached to the same words)"
                   if untracked else "")
    footnote_rules = (_FOOTNOTES_BLOCKED if untracked
                      else _FOOTNOTE_RULES.replace("{MAX_FOOTNOTES}", str(MAX_FOOTNOTES)))
    system = (SYSTEM_PROMPT.replace("{MARKER_RULE}", marker_rule)
              .replace("{FOOTNOTE_RULES}", footnote_rules))

    book = ctx["book"]
    out = [f"ERROR REPORT #{report.get('id')}",
           f"Type (reader's choice): {report.get('report_type')}"]
    if report.get("quote"):
        out.append(f"Reported text (the reader highlighted this):\n{report['quote'].strip()}")
    else:
        out.append("Reported text: (none given)")
    out.append(f"What's wrong (reader's words):\n{(report.get('problem') or '').strip()}")
    if (report.get("suggested_fix") or "").strip():
        out.append(f"Reader's suggested fix:\n{report['suggested_fix'].strip()}")

    out.append("")
    out.append(f"BOOK: {book.get('title') or ctx['book_id']} "
               f"(source language: {book.get('source_language') or 'zh'})")
    out.append(f"CHAPTER {ctx['chapter_number']}: {ctx['chapter'].get('title') or ''}")

    out.append("")
    gendered = ", ".join(ctx["gendered_categories"]) or "(none)"
    out.append(f"GLOSSARY — entities in this chapter (gender is tracked for: {gendered}):")
    if ctx["terms"]:
        out.extend(_fmt_term(t) for t in ctx["terms"])
    else:
        out.append("(none indexed)")

    if not untracked:
        rows = ctx["chapter_footnote_rows"]
        out.append("")
        out.append("FOOTNOTES ALREADY IN THIS CHAPTER (markers are hidden from the T lines):")
        out.extend(f'- "{r["anchor"]}": {r["body"]}' for r in rows) if rows else out.append("(none)")

        book_fn = ctx["book_footnotes"]
        anchors = []
        seen = set()
        for r in reversed(book_fn):  # newest first, deduped
            a = (r.get("anchor") or "").strip()
            if a and a.lower() not in seen:
                seen.add(a.lower())
                anchors.append(a)
            if len(anchors) >= MAX_BOOK_ANCHORS:
                break
        out.append("")
        out.append("ALREADY FOOTNOTED ELSEWHERE IN THIS BOOK (don't footnote these again):")
        out.append("; ".join(sorted(anchors, key=str.lower)) if anchors else "(none)")
        examples = [r["body"] for r in book_fn[-STYLE_EXAMPLES:] if r.get("body")]
        if examples:
            out.append("")
            out.append("FOOTNOTE STYLE EXAMPLES from this book:")
            out.extend(f"- {b}" for b in examples)

    out.append("")
    out.append("SOURCE:")
    out.extend(f"S{i}: {line}" for i, line in enumerate(ctx["source"]))
    out.append("")
    out.append("TRANSLATION:")
    out.extend(f"T{i}: {line}" for i, line in enumerate(ctx["prose"]))

    return [{"role": "system", "content": system},
            {"role": "user", "content": "\n".join(out)}]


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------

def word_diff(before: str, after: str) -> list:
    """[[op, text], ...] with op in eq|del|ins, adjacent same-op runs merged."""
    a, b = _TOKEN_RE.findall(before), _TOKEN_RE.findall(after)
    segs = []

    def push(op, text):
        if not text:
            return
        if segs and segs[-1][0] == op:
            segs[-1][1] += text
        else:
            segs.append([op, text])

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            push("eq", "".join(a[i1:i2]))
        else:
            push("del", "".join(a[i1:i2]))
            push("ins", "".join(b[j1:j2]))
    return segs


def _edit_kind(line: str, find: str, replace: str) -> str:
    if replace == "" and find == line:
        return "delete"
    if "\n" in replace:
        return "split"
    return "replace"


def apply_edits(prose: list, edits: list) -> tuple:
    """Apply validated edits. Returns (new_lines, line_map) where line_map[i] is
    the list of new indices line i became ([] = deleted)."""
    by_line = {}
    for e in edits:
        by_line.setdefault(e["line"], []).append(e)
    new_lines, line_map = [], []
    for i, line in enumerate(prose):
        group = by_line.get(i)
        if not group:
            line_map.append([len(new_lines)])
            new_lines.append(line)
            continue
        if any(e["kind"] == "delete" for e in group):
            line_map.append([])
            continue
        text = line
        for e in sorted(group, key=lambda e: e["start"], reverse=True):
            text = text[:e["start"]] + e["replace"] + text[e["start"] + len(e["find"]):]
        # A split drops empty pieces; a replace that empties the line deletes it.
        pieces = [p for p in text.split("\n") if p.strip()]
        line_map.append(list(range(len(new_lines), len(new_lines) + len(pieces))))
        new_lines.extend(pieces)
    return new_lines, line_map


def _orphans(prose: list, rows: list) -> set:
    if not rows:
        return set()
    _, orphans = render_footnotes(prose, rows)
    return {o.get("id") for o in orphans}


def validate_edits(prose: list, raw_edits, *, footnote_rows=(), untracked=False) -> tuple:
    """Check the model's edits against the prose. Returns (accepted, rejected);
    each accepted edit carries id, kind, start, before/after and a word diff."""
    accepted, rejected = [], []
    spans = {}  # line -> [(start, end)]
    if not isinstance(raw_edits, list):
        raw_edits = []

    def reject(item, why):
        rejected.append({**({k: item.get(k) for k in ("line", "find", "replace", "reason")}
                            if isinstance(item, dict) else {"raw": str(item)[:200]}),
                         "why": why})

    for k, item in enumerate(raw_edits):
        if len(accepted) >= MAX_EDITS:
            reject(item, f"more than {MAX_EDITS} edits")
            continue
        if not isinstance(item, dict):
            reject(item, "not an object")
            continue
        try:
            li = int(item.get("line"))
        except (TypeError, ValueError):
            reject(item, "no line number")
            continue
        find, replace = item.get("find"), item.get("replace")
        if not isinstance(find, str) or not isinstance(replace, str) or not find:
            reject(item, "find/replace missing")
            continue
        if not 0 <= li < len(prose):
            reject(item, f"line T{li} doesn't exist")
            continue
        line = prose[li]
        count = line.count(find)
        if count == 0:
            reject(item, f"text not found in T{li}")
            continue
        if count > 1:
            reject(item, f"text occurs {count}× in T{li} — not unique")
            continue
        if find == replace:
            reject(item, "no change")
            continue
        if SENTINEL_RE.findall(find) != SENTINEL_RE.findall(replace):
            reject(item, "touches a ⟦…⟧ marker")
            continue
        kind = _edit_kind(line, find, replace)
        if untracked and kind != "delete" and \
                sorted(MARKER_RE.findall(find)) != sorted(MARKER_RE.findall(replace)):
            reject(item, "changes a footnote marker")
            continue
        if untracked and kind == "delete" and MARKER_RE.search(line):
            reject(item, "deletes a line carrying a footnote marker")
            continue
        start = line.find(find)
        end = start + len(find)
        if any(s < end and start < e for s, e in spans.get(li, [])):
            reject(item, f"overlaps another edit in T{li}")
            continue

        edit = {
            "id": k, "line": li, "find": find, "replace": replace, "kind": kind,
            "start": start, "reason": str(item.get("reason") or "")[:200],
        }
        # An edit that rewrites an anchored term would orphan that footnote.
        if footnote_rows:
            alone, _ = apply_edits(prose, [edit])
            if _orphans(alone, footnote_rows) - _orphans(prose, footnote_rows):
                reject(item, "would orphan an existing footnote (it rewrites the anchored term)")
                continue

        new_line, _ = apply_edits([line], [{**edit, "line": 0}])
        after = "\n".join(new_line)
        edit["before"] = line
        edit["after"] = after
        edit["diff"] = word_diff(line, after) if kind != "delete" else [["del", line]]
        spans.setdefault(li, []).append((start, end))
        accepted.append(edit)
    return accepted, rejected


# ---------------------------------------------------------------------------
# Footnotes
# ---------------------------------------------------------------------------

def _locate_anchor(new_prose: list, line_map: list, orig_line, anchor: str):
    """(line_idx, marker_col) of the anchor in the post-edit prose: in the line(s)
    the model's original line became, else its first occurrence anywhere."""
    candidates = []
    if isinstance(orig_line, int) and 0 <= orig_line < len(line_map):
        candidates = line_map[orig_line]
    for li in candidates:
        idx = new_prose[li].find(anchor)
        if idx != -1:
            return li, marker_position(new_prose[li], idx, idx + len(anchor))
    return find_occurrence(new_prose, anchor, 1)


def _preview_numbers(prose: list, existing_rows: list, new_items: list) -> dict:
    """The [n] each new footnote gets once the chapter is re-rendered."""
    placements = []
    for r in existing_rows:
        pos = find_occurrence(prose, (r.get("anchor") or "").strip(), int(r.get("occurrence") or 1))
        if pos:
            placements.append((pos[0], pos[1], 0, ("old", r.get("id"))))
    for k, it in enumerate(new_items):
        placements.append((it["line_idx"], it["col"], 1, ("new", k)))
    placements.sort(key=lambda p: (p[0], p[1], p[2]))
    return {key[1]: n for n, (_, _, _, key) in enumerate(placements, start=1) if key[0] == "new"}


def validate_footnotes(new_prose: list, line_map: list, raw_footnotes, ctx: dict,
                       db=None, check_earlier=True) -> tuple:
    """Check proposed footnotes against the post-edit prose. Returns
    (accepted, rejected); accepted items carry occurrence, the would-be [n], a
    marked-up line preview and any warnings."""
    accepted, rejected = [], []
    if not isinstance(raw_footnotes, list):
        raw_footnotes = []

    def reject(item, why):
        rejected.append({**({k: item.get(k) for k in ("anchor", "body", "source_term", "line")}
                            if isinstance(item, dict) else {"raw": str(item)[:200]}),
                         "why": why})

    if ctx["untracked_footnotes"] and raw_footnotes:
        for item in raw_footnotes:
            reject(item, "this chapter's existing footnotes aren't in the footnotes table "
                         "(run backfill_footnotes.py first)")
        return [], rejected

    book_rows = ctx["book_footnotes"]
    book_bodies = {(r.get("body") or "").strip() for r in book_rows}
    chapter_rows = ctx["chapter_footnote_rows"]
    taken = {((r.get("anchor") or "").strip(), int(r.get("occurrence") or 1)) for r in chapter_rows}
    n = ctx["chapter_number"]

    for k, item in enumerate(raw_footnotes):
        if len(accepted) >= MAX_FOOTNOTES:
            reject(item, f"more than {MAX_FOOTNOTES} footnotes")
            continue
        if not isinstance(item, dict):
            reject(item, "not an object")
            continue
        anchor = (item.get("anchor") or "").strip()
        body = (item.get("body") or "").strip()
        if not anchor or not body:
            reject(item, "anchor/body missing")
            continue
        if len(body) > MAX_FOOTNOTE_BODY:
            reject(item, f"body longer than {MAX_FOOTNOTE_BODY} characters")
            continue
        if body in book_bodies:
            reject(item, "this exact note is already in the book")
            continue
        orig_line = item.get("line")
        try:
            orig_line = int(orig_line) if orig_line is not None else None
        except (TypeError, ValueError):
            orig_line = None
        pos = _locate_anchor(new_prose, line_map, orig_line, anchor)
        if pos is None:
            reject(item, "anchor not found in the chapter (after the edits)")
            continue
        li, col = pos
        occurrence = occurrence_at(new_prose, anchor, li, col)
        if (anchor, occurrence) in taken:
            reject(item, "that spot already has a footnote")
            continue
        if any(a["anchor"] == anchor and a["occurrence"] == occurrence for a in accepted):
            reject(item, "duplicate of another proposed footnote")
            continue

        warnings = []
        for r in book_rows:
            if (r.get("anchor") or "").strip().lower() == anchor.lower():
                warnings.append(f"already footnoted at ch{r.get('chapter_number')}")
                break
        if check_earlier and db is not None and n and n > 1:
            try:
                hits = db.search_book_chapters(ctx["book_id"], anchor, scope="translated",
                                               max_chapter=n - 1)
                if hits:
                    warnings.append(f"first mention is ch{hits[0].get('chapter_number')}")
            except Exception as e:
                logger.warning(f"autofix: earlier-mention check failed: {e}")

        accepted.append({
            "id": k,
            "anchor": anchor,
            "body": body,
            "source_term": (item.get("source_term") or "").strip() or None,
            "reason": str(item.get("reason") or "")[:200],
            "line": orig_line,
            "line_idx": li,
            "col": col,
            "occurrence": occurrence,
            "warnings": warnings,
        })

    numbers = _preview_numbers(new_prose, chapter_rows, accepted)
    for k, it in enumerate(accepted):
        it["number"] = numbers.get(k)
        line = new_prose[it["line_idx"]]
        it["preview"] = line[:it["col"]] + f"[{it['number']}]" + line[it["col"]:]
    return accepted, rejected


# ---------------------------------------------------------------------------
# Entity updates (gender only)
# ---------------------------------------------------------------------------

def validate_entity_updates(raw, ctx: dict) -> tuple:
    accepted, rejected = [], []
    if not isinstance(raw, list):
        raw = []
    terms = {t.get("id"): t for t in ctx["terms"]}
    gendered = set(ctx["gendered_categories"])
    for k, item in enumerate(raw):
        if not isinstance(item, dict):
            rejected.append({"raw": str(item)[:200], "why": "not an object"})
            continue
        why = None
        try:
            eid = int(item.get("entity_id"))
        except (TypeError, ValueError):
            eid = None
        value = (item.get("value") or "").strip().lower()
        term = terms.get(eid)
        if (item.get("field") or "gender") != "gender":
            why = "only gender can be changed here"
        elif term is None:
            why = "entity isn't in this chapter's glossary"
        elif term.get("category") not in gendered:
            why = f"gender isn't tracked for {term.get('category')}"
        elif value not in VALID_GENDERS:
            why = f"invalid gender {value!r}"
        elif (term.get("gender") or "").lower() == value:
            why = "already that gender"
        if why:
            rejected.append({"entity_id": item.get("entity_id"), "value": item.get("value"), "why": why})
            continue
        accepted.append({
            "id": k, "entity_id": eid, "field": "gender",
            "translation": term.get("translation"), "untranslated": term.get("untranslated"),
            "old": term.get("gender"), "value": value,
            "reason": str(item.get("reason") or "")[:200],
        })
    return accepted, rejected


# ---------------------------------------------------------------------------
# Proposal (run) and apply
# ---------------------------------------------------------------------------

def build_proposal(raw: dict, ctx: dict, db=None) -> dict:
    if not isinstance(raw, dict):
        raise AutofixError("Model returned something other than a JSON object.")
    verdict = raw.get("verdict")
    if verdict not in VERDICTS:
        verdict = "unclear"
    prose = ctx["prose"]
    edits, rejected_edits = validate_edits(
        prose, raw.get("edits"), footnote_rows=ctx["chapter_footnote_rows"],
        untracked=ctx["untracked_footnotes"])
    new_prose, line_map = apply_edits(prose, edits)
    footnotes, rejected_fn = validate_footnotes(new_prose, line_map, raw.get("footnotes"), ctx, db=db)
    entity_updates, rejected_ent = validate_entity_updates(raw.get("entity_updates"), ctx)
    return {
        "verdict": verdict,
        "explanation": str(raw.get("explanation") or "").strip(),
        "other_action": str(raw.get("other_action") or "").strip(),
        "edits": edits,
        "footnotes": footnotes,
        "entity_updates": entity_updates,
        "rejected": {"edits": rejected_edits, "footnotes": rejected_fn,
                     "entity_updates": rejected_ent},
        "untracked_footnotes": ctx["untracked_footnotes"],
        "translation_date": ctx["translation_date"],
        "chapter_number": ctx["chapter_number"],
    }


def _parse(content: str) -> dict:
    from pronoun_repair import parse_last_json_object
    if not (content or "").strip():
        raise AutofixError("Model returned an empty reply.")
    try:
        return parse_last_json_object(content)
    except json.JSONDecodeError:
        raise AutofixError("Model returned invalid JSON.")


def propose(db, config, report: dict, *, spec: Optional[str] = None) -> dict:
    """Run the model and return a validated proposal (nothing is written)."""
    ctx = load_context(db, report)
    spec = spec or model_spec()
    try:
        provider, model_name = config.get_client(spec)
    except Exception as e:
        raise AutofixError(f"Model {spec} unavailable: {str(e)[:150]}")
    messages = build_messages(report, ctx)
    research = research_kwargs(provider, ctx, config)
    if research:
        messages[-1]["content"] += RESEARCH_HINT
    try:
        response = provider.chat_completion(
            messages=messages,
            model=model_name,
            response_format={"type": "json_object"},
            **research,
        )
        content = provider.get_response_content(response) or ""
    except AutofixError:
        raise
    except Exception as e:
        raise AutofixError(f"Model call failed: {str(e)[:200]}")
    proposal = build_proposal(_parse(content), ctx, db=db)
    proposal["model"] = spec
    proposal["research"] = bool(research)
    proposal["created_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return proposal


def run_autofix(db, config, report_id: int, *, spec: Optional[str] = None) -> None:
    """Thread target: propose and store. Every outcome lands on the report row."""
    spec = spec or model_spec()
    try:
        report = db.get_error_report(report_id)
        if not report:
            return
        proposal = propose(db, config, report, spec=spec)
        db.set_error_report_autofix(report_id, "done", proposal)
    except AutofixError as e:
        db.set_error_report_autofix(report_id, "failed", {"error": str(e), "model": spec})
    except Exception as e:
        logger.exception(f"autofix for report {report_id} crashed")
        db.set_error_report_autofix(report_id, "failed",
                                    {"error": f"Auto-fix crashed: {str(e)[:200]}", "model": spec})


def apply_proposal(db, report: dict, *, edit_ids=None, footnotes=None,
                   entity_update_ids=None, notes: Optional[str] = None) -> dict:
    """Apply the stored proposal (or the chosen part of it). Call under
    deps.chapter_save_lock. `footnotes` is [{id, anchor?, body?}] — the admin
    may have edited anchor/body; None = every proposed footnote as proposed.
    Raises ChapterChanged when the chapter was saved after the proposal."""
    try:
        proposal = json.loads(report.get("autofix_result") or "")
    except (TypeError, ValueError):
        proposal = None
    if not isinstance(proposal, dict) or report.get("autofix_status") != "done" \
            or "edits" not in proposal:
        raise AutofixError("No auto-fix proposal to apply — run Auto-fix first.")
    if proposal.get("applied"):
        raise AutofixError("This proposal was already applied.")

    ctx = load_context(db, report)
    if ctx["translation_date"] != proposal.get("translation_date"):
        raise ChapterChanged("The chapter changed since this proposal was made — re-run Auto-fix.")

    # Edits: re-validate the chosen subset against the live prose.
    chosen = [e for e in proposal["edits"] if edit_ids is None or e["id"] in set(edit_ids)]
    edits, rejected = validate_edits(
        ctx["prose"], [{k: e[k] for k in ("line", "find", "replace", "reason")} for e in chosen],
        footnote_rows=ctx["chapter_footnote_rows"], untracked=ctx["untracked_footnotes"])
    if rejected:
        raise AutofixError("Edit no longer applies: " + rejected[0]["why"])
    new_prose, line_map = apply_edits(ctx["prose"], edits)

    # Footnotes: proposed ones, possibly edited, re-validated on the new prose.
    by_id = {f["id"]: f for f in proposal.get("footnotes") or []}
    if footnotes is None:
        wanted = list(by_id.values())
    else:
        wanted = []
        for sel in footnotes:
            base = by_id.get(sel.get("id"))
            if base is None:
                continue
            wanted.append({**base,
                           "anchor": (sel.get("anchor") or base["anchor"]).strip(),
                           "body": (sel.get("body") or base["body"]).strip()})
    fn_ok, fn_skipped = validate_footnotes(
        new_prose, line_map,
        [{k: f.get(k) for k in ("line", "anchor", "body", "source_term", "reason")} for f in wanted],
        ctx, db=db, check_earlier=False)

    ents = [u for u in proposal.get("entity_updates") or []
            if entity_update_ids is not None and u["id"] in set(entity_update_ids)]

    if not edits and not fn_ok and not ents:
        raise AutofixError("Nothing selected to apply.")

    book_id, n, chapter = ctx["book_id"], ctx["chapter_number"], ctx["chapter"]
    title = chapter.get("title") or f"Chapter {n}"

    if edits:
        # Pre-fix snapshot: the chapter's History panel can put it back.
        db.add_chapter_revision(book_id, n, title, ctx["lines"], kind="manual")
        content = new_prose + ctx["def_block"] if ctx["untracked_footnotes"] else new_prose
        if not db.save_chapter(
                book_id=book_id, chapter_number=n, title=title,
                untranslated_content=chapter.get("untranslated", []),
                translated_content=content,
                summary=chapter.get("summary"),
                translation_model=chapter.get("model")):
            raise AutofixError("Saving the chapter failed.")
    elif fn_ok:
        db.add_chapter_revision(book_id, n, title, ctx["lines"], kind="manual")

    placed = []
    for f in fn_ok:
        fid = db.add_footnote(book_id, ctx["chapter_id"], f["anchor"], f["body"],
                              source_term=f.get("source_term"), occurrence=f["occurrence"])
        if fid is None:
            fn_skipped.append({"anchor": f["anchor"], "why": "database write failed"})
        else:
            placed.append({"footnote_id": fid, "anchor": f["anchor"], "body": f["body"]})
    if placed and not db.rerender_chapter_footnotes(ctx["chapter_id"]):
        raise AutofixError("Footnotes were stored but the chapter didn't re-render.")

    gender_done = []
    for u in ents:
        rev = db.set_entity_gender(u["entity_id"], u["value"], author="human",
                                   chapter_number=n,
                                   reason=f"error report #{report['id']}: {u.get('reason') or ''}"[:500])
        if rev is not None:
            gender_done.append({"entity_id": u["entity_id"], "translation": u.get("translation"),
                                "value": u["value"]})

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    note_text = notes if notes is not None else proposal.get("explanation") or ""
    db.update_error_report(report["id"], {
        "status": "resolved", "reviewed_at": now,
        "admin_notes": (note_text.strip() or "Auto-fixed."),
    })
    proposal["applied"] = {
        "at": now,
        "edits": [e["id"] for e in edits],
        "footnotes": placed,
        "footnotes_skipped": fn_skipped,
        "entity_updates": gender_done,
    }
    db.set_error_report_autofix(report["id"], "done", proposal)

    parts = []
    if edits:
        parts.append(f"{len(edits)} edit{'s' if len(edits) != 1 else ''}")
    if placed:
        parts.append(f"{len(placed)} footnote{'s' if len(placed) != 1 else ''}")
    if gender_done:
        parts.append(", ".join(f"{g['translation']} → {g['value']}" for g in gender_done))
    try:
        db.add_activity_log(
            type="error_report_autofix",
            message=f"Error report #{report['id']} auto-fixed: " + "; ".join(parts),
            book_id=book_id, chapter=n,
        )
    except Exception:
        pass
    return proposal["applied"]
