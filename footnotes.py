#!/usr/bin/env python3
"""Shared helpers for the chapter footnote system.

Footnotes are persisted in the `footnotes` table (see db_backend.py). The body
lives there; the inline "[n]" marker plus the trailing "[n] body" definition
block in a chapter's content is a *derived rendering* that is re-applied on every
chapter save by anchor — the term the marker hugs. This module owns the rendering
logic so the table can stay the single source of truth and footnotes survive
retranslation.

Footnote convention (matches the project's manual style — no special markup):
  - inline marker hugs the term:   "...the Calabash Brothers[3] rushing in..."
  - definition at the bottom:       [3] Calabash Brothers (葫芦兄弟): a 1980s ...
"""

import json
import re

# A definition line at the bottom of a chapter: "[n] body text".
DEF_RE = re.compile(r"^\[(\d+)\]\s?(.*)$")
# An inline marker "[n]" anywhere in prose.
MARKER_RE = re.compile(r"\[(\d+)\]")

# Private-use sentinels wrap a freshly-inserted (not-yet-numbered) marker so it
# can be told apart from real text during the renumber scan.
SENT_OPEN, SENT_CLOSE = "", ""
SENT_RE = re.compile(SENT_OPEN + r"(\d+)" + SENT_CLOSE)
# Legacy: recognise either a real "[n]" marker or a sentinel (used by
# renumber_chapter, kept for the scripts that still insert markers inline).
PLACEHOLDER_RE = re.compile(r"\[(\d+)\]|" + SENT_OPEN + r"(\d+)" + SENT_CLOSE)


def content_to_list(raw):
    """Normalize chapter content (JSON string / list / str) to a list of lines."""
    if isinstance(raw, list):
        return list(raw)
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, list) else str(loaded).split("\n")
    except (ValueError, TypeError):
        return (raw or "").split("\n")


def prose_end_index(lines):
    """Index where the trailing definition block begins (blank + '[n] body' lines
    only). Searching for a term above this point keeps markers out of any existing
    footnote body."""
    i = len(lines) - 1
    while i >= 0 and (lines[i].strip() == "" or DEF_RE.match(lines[i])):
        i -= 1
    return i + 1


def split_prose_and_defs(lines):
    """Return (prose_lines, defs) where defs maps footnote number -> body."""
    prose_end = prose_end_index(lines)
    defs = {}
    for line in lines[prose_end:]:
        m = DEF_RE.match(line)
        if m:
            defs[int(m.group(1))] = m.group(2).strip()
    return lines[:prose_end], defs


def strip_footnotes(lines):
    """Return clean prose: the trailing definition block removed AND every inline
    "[n]" marker stripped from the remaining prose lines."""
    prose = lines[:prose_end_index(lines)]
    return [MARKER_RE.sub("", line) for line in prose]


def find_occurrence(prose_lines, anchor, occurrence):
    """Return (line_idx, col_just_past_anchor) for the `occurrence`-th (1-based)
    appearance of `anchor` across `prose_lines`, or None if not found."""
    if not anchor:
        return None
    count = 0
    for li, line in enumerate(prose_lines):
        start = 0
        while True:
            idx = line.find(anchor, start)
            if idx == -1:
                break
            count += 1
            if count == occurrence:
                return (li, idx + len(anchor))
            start = idx + len(anchor)
    return None


def occurrence_at(prose_lines, anchor, line_idx, col_end):
    """How many times `anchor` appears in `prose_lines` up to and including the
    match that ENDS at (line_idx, col_end). Used to record the right occurrence
    when backfilling from an existing marker position. Returns 1 if not matched."""
    if not anchor:
        return 1
    target_start = col_end - len(anchor)
    count = 0
    for li, line in enumerate(prose_lines):
        start = 0
        while True:
            idx = line.find(anchor, start)
            if idx == -1:
                break
            count += 1
            if li == line_idx and idx == target_start:
                return count
            start = idx + len(anchor)
    return count or 1


def render_footnotes(lines, rows):
    """Re-render a chapter's footnotes from the table rows.

    The table is authoritative: all existing markers + the definition block are
    discarded, then each row is re-placed by finding the `occurrence`-th match of
    its `anchor` in the prose, markers are renumbered 1..N in reading order, and
    the definition block is rebuilt.

    Args:
        lines: chapter content (list of lines, JSON string, or str).
        rows:  list of dicts with at least 'anchor', 'body'; optional
               'occurrence' (default 1), 'sort_order', 'id'.

    Returns:
        (new_lines, orphan_rows) where orphan_rows are the rows whose anchor was
        not found (their markers are absent from the output).
    """
    prose = strip_footnotes(content_to_list(lines))

    placements = []  # (line_idx, col, row)
    orphans = []
    for row in rows:
        anchor = (row.get("anchor") or "").strip()
        occ = int(row.get("occurrence") or 1)
        pos = find_occurrence(prose, anchor, occ)
        if pos is None:
            orphans.append(row)
            continue
        placements.append((pos[0], pos[1], row))

    if not placements:
        # Nothing to place — return the clean prose (markers stripped).
        out = list(prose)
        while out and not out[-1].strip():
            out.pop()
        return out, orphans

    # Reading order; sort_order then id break ties at the same position.
    def sortkey(p):
        li, col, row = p
        so = row.get("sort_order")
        return (li, col, so if so is not None else 0, row.get("id") or 0)
    placements.sort(key=sortkey)

    # Insert sentinels right-to-left per line so earlier offsets stay valid.
    work = list(prose)
    byline = {}
    for k, (li, col, _row) in enumerate(placements):
        byline.setdefault(li, []).append((col, k))
    for li, pts in byline.items():
        line = work[li]
        for col, k in sorted(pts, reverse=True):
            line = line[:col] + f"{SENT_OPEN}{k}{SENT_CLOSE}" + line[col:]
        work[li] = line

    # Walk sentinels in reading order, replace with [n].
    ordered = []  # placement index k, in reading order
    out_lines = []
    for line in work:
        out, last = [], 0
        for m in SENT_RE.finditer(line):
            n = len(ordered) + 1
            out.append(line[last:m.start()])
            out.append(f"[{n}]")
            last = m.end()
            ordered.append(int(m.group(1)))
        out.append(line[last:])
        out_lines.append("".join(out))

    # Rebuild the definition block in reading order.
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    result = out_lines + [""]
    for n, k in enumerate(ordered, start=1):
        body = placements[k][2].get("body") or ""
        result.append(f"[{n}] {body}")
    return result, orphans


# ---------------------------------------------------------------------------
# Legacy inline renumberer — kept for callers that still insert "[n]" markers
# directly into content (and preserve pre-existing markers). New code should use
# render_footnotes against the table instead.
# ---------------------------------------------------------------------------
def renumber_chapter(lines, items):
    """Insert new footnotes into a chapter, then renumber ALL footnotes by
    reading-order position so markers and the definition block stay in document
    order.

    items: list of (term, body, line_idx, insert_pos).
    Returns (new_lines, final_for_item, changed_existing).
    """
    lines = list(lines)
    prose_end = prose_end_index(lines)

    existing_defs = {}
    for idx in range(prose_end, len(lines)):
        m = DEF_RE.match(lines[idx])
        if m:
            existing_defs[int(m.group(1))] = m.group(2)

    prose = lines[:prose_end]

    new_bodies = {}
    byline = {}
    for k, (term, body, li, pos) in enumerate(items):
        new_bodies[k] = body
        byline.setdefault(li, []).append((pos, k))
    for li, pts in byline.items():
        line = prose[li]
        for pos, k in sorted(pts, reverse=True):
            line = line[:pos] + f"{SENT_OPEN}{k}{SENT_CLOSE}" + line[pos:]
        prose[li] = line

    ordered = []
    changed_existing = False
    new_prose = []
    for line in prose:
        out, last = [], 0
        for m in PLACEHOLDER_RE.finditer(line):
            n = len(ordered) + 1
            out.append(line[last:m.start()])
            out.append(f"[{n}]")
            last = m.end()
            if m.group(1) is not None:
                old = int(m.group(1))
                ordered.append(("old", old))
                if old != n:
                    changed_existing = True
            else:
                ordered.append(("new", int(m.group(2))))
        out.append(line[last:])
        new_prose.append("".join(out))

    while new_prose and not new_prose[-1].strip():
        new_prose.pop()
    result = new_prose + [""]
    final_for_item = {}
    n = 0
    for kind, value in ordered:
        n += 1
        if kind == "old":
            body = existing_defs.get(value, "")
        else:
            body = new_bodies[value]
            final_for_item[value] = n
        result.append(f"[{n}] {body}")

    consumed = {v for kind, v in ordered if kind == "old"}
    for num in sorted(existing_defs):
        if num not in consumed:
            n += 1
            result.append(f"[{n}] {existing_defs[num]}")

    return result, final_for_item, changed_existing
