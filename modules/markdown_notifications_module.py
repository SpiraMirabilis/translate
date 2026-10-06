"""markdown_notifications module — render 【…】 system/notification blocks as tables.

Many web novels emit RPG-style "system" notifications wrapped in full-width
brackets, one per paragraph, e.g.::

    【Mission Reward: Immaculate Demon Heart】

This module rewrites each such line into a single-column Markdown table cell so
the Reader / EPUB / HTML output draws it as a boxed callout. A *run* of adjacent
notifications (separated only by blank lines) is merged into one multi-row
table — the first bracket becomes the table header, the rest become body rows::

    | Novice Demon Lord Mission has been issued. |
    | --- |
    | Mission: Seize Another's Fortuitous Opportunity (Incomplete) |
    | …please rob others of their fortuitous opportunities… |
    | Mission Progress: 0/10 |
    | Mission Reward: Immaculate Demon Heart |

ASCII-bracket lines (``[ … ]`` alone on their line) are treated identically to
full-width 【…】 — any line whose sole content is one bracketed span counts as a
notification. Reversal always re-emits full-width 【…】 regardless of which
bracket style the source used.

Several notifications may also share one line — raws often pack a boot sequence
two-to-a-paragraph, e.g. ``[System status: abnormal startup] [Detecting runtime
environment...]``. A line that is *nothing but* a whitespace-separated run of
2+ balanced bracket spans yields one cell per span, not one cell holding the
brackets. (Greedy single-span matching would otherwise swallow the whole line,
leaving the interior ``] [`` inside the cell text.) ``transform_source_lines``
does the matching thing on the untranslated side — it splits such a line into
one blank-separated paragraph per notification, so the model translates them
as separate paragraphs and the rows line up. Any bare text outside the spans
means the line is prose and is left alone.

A notification may also span several paragraphs: a line that *starts* with an
opening bracket pairs with a later line that *ends* with the matching closer,
and every non-blank line in between becomes its own table row::

    【Mission Issued: …teach her swordsmanship.

    Mission duration is fifty years.

    Upon mission settlement, …the richer the rewards.】

Matching is bracket-*balance* aware: balanced interior pairs on a line — e.g.
``…35% of your 【Spirit】 is converted into 【Agility】 and 【Strength】.`` — are
net-zero and do not disqualify the opener or a cell; only the unmatched outer
wrapper drives the block. The scan is bounded (``_MAX_MULTILINE_SPAN`` lines)
and aborts if the running depth ever goes negative (a stray closer) or closes
mid-line, so a stray unmatched bracket cannot swallow a chapter. Reversal of
such a block is intentionally
lossy: each row comes back as its own 【…】 paragraph, not one multi-paragraph
bracket.

A speaker label left *outside* its notification is folded in, on both sides::

    Molly: 【Because you were tested twice…】   →   【Molly: Because you were tested twice…】
    茉莉：【因為你考了兩次……】                  →   【茉莉：因為你考了兩次……】

so the name rides inside the box instead of dangling in front of it. The colon
and the spacing after it are preserved, so English ``: `` and Chinese ``：`` each
keep their own convention. The label must match one of the book's person-like
entities exactly (any entity carrying a gender, globals included) — shape alone
would also fold the tail of a narrative clause, and ``Molly popped up again,
angling for attention: 【…】`` must keep its narration outside the box. With no
db in context the gate is undeterminable and nothing is folded.

The per-book setting **"Fold any label before the colon"** replaces that entity
gate with a purely structural one: *any* non-empty text before a ``:`` / ``：``
that is followed by nothing but one bracketed notification is folded in::

    Bullet comments: 【Okay, got it, so it's grandma spoiling him.】
        → 【Bullet comments: Okay, got it, so it's grandma spoiling him.】

which is what books whose notifications are introduced by a *thing* rather than
a person need (``Bullet comments:``, ``System:``, ``弹幕：``) — none of which is
an entity, so the default gate never folds them. It is off by default because it
is strictly looser: with it on, a narrative clause ending in a colon folds into
the box too. The label still has to carry no bracket of its own, and the
remainder still has to be exactly one balanced notification, so a folded line is
not re-folded and prose mentioning a bracketed term is untouched.

The per-book setting **"Fold the description after the colon"** does the mirror
image of the speaker fold — it pulls a description *trailing* a bracketed label
into the box::

    【Food Appraisal】: An excellent chef must master…
        → 【Food Appraisal: An excellent chef must master…】
    【財富商城】：財富值達到1000開啟。 → 【財富商城：財富值達到1000開啟。】

No entity gate is needed on this side: the line has to *begin* with a balanced
bracket group, which narration ending in a colon cannot. A tail that is itself
bracketed (``【A】: 【B】`` — two notifications) and a numeric ASCII label
(``[1]: …`` — a footnote marker or a Markdown link reference definition) are
left alone, and an *empty* tail belongs to the panel rule below.

The per-book setting **"Absorb stat entries under a 【Heading】:"** handles the
status panels of game-system novels, which bracket their headings but print
their entries bare::

    【Player: Zhou Yan】

    【Professional Skills】:

    Knife Work (Intermediate): 8604/10000 (Your knife work is up to the demands…)

    Heat Control (Beginner): 668/1000 (Kid, you need more practice)

    【Dishes Mastered】:

    Twice-Cooked Pork (Beginner): 55/1000 (Freshly paved road, nice and flat…)

    ……

    【Main Quest: Become the God of Cookery!】

Without it the panel breaks into a one-row table per bracketed line with loose
prose between them. With it, a ``【Heading】:`` line claims the entries beneath
it — a stat entry (``name (level): value``, the level being the first
parenthesized group before a colon), an elision (``……``), or an unbracketed
sub-heading such as ``Special Skills:`` when a stat entry follows it — and the
run-grouping then merges the whole panel, quests included, into one box. The
section ends at the first line matching none of those, so the prose after the
panel stays outside, and a ``【Heading】:`` that absorbs no stat entry is left to
the other matchers. Reversal puts a panel back the way it came, except that an
unbracketed sub-heading returns bracketed — the table stores the two forms
identically.

Forward conversion is idempotent: it consumes the bracket markers it matches, so
a second pass finds nothing to do. Disabling the module reverses the operation —
single-column tables (as produced here) are turned back into double-spaced
【…】 paragraphs. Some normalizations are one-way, though, and survive a disable:
notifications that shared a line come back as two paragraphs rather than one, a
folded speaker label or trailing description stays inside its brackets, and a
panel's unbracketed sub-heading comes back bracketed.

Auto-off: never enabled by Source URL or default; turn it on per book via the
Modules dialog.

Ordering note: this module is registered AFTER ``chapter_spacing`` so that the
double-spacer runs first. ``chapter_spacing`` inserts a blank line between every
adjacent paragraph, which would split a table's contiguous rows; running this
transform last means the table is assembled after spacing has settled.
"""
import json
import re

from .activity import log_module_activity
from .base import TranslationModule
from .entity_names import load_person_names

# A whole-line notification: 【 … 】 or [ … ] alone on its line (surrounding
# whitespace ok). Both bracket styles are treated identically.
# Greedy inner (.*) so nested brackets — e.g. 【Li Yu: 【Video】】 — match through the
# LAST closer, not the first. A char class like [^】]* stops at the first inner 】.
_NOTIF_RE = re.compile(r"^\s*(?:【(.*)】|\[(.*)\])\s*$")
# The two notification bracket styles, as (opener, closer) pairs.
_BRACKETS = (("【", "】"), ("[", "]"))
# A Markdown table row: | … |
_ROW_RE = re.compile(r"^\s*\|(.*)\|\s*$")
# Longest multi-line notification we will scan for a closer before giving up.
# Bounds the damage a stray unmatched opener can do. Counted in raw lines, so
# with chapter_spacing's double-spacing this is ~20 paragraphs — long "system
# evaluation" blocks (e.g. book 72 ch42) run right up against it.
_MAX_MULTILINE_SPAN = 50
# A table separator cell: optional colons around one-or-more dashes (e.g. ---).
_SEP_RE = re.compile(r"^:?-+:?$")
# A speaker label introducing a bracketed notification: `Molly: 【…】`, `茉莉：【…】`.
# The label is one to four short space-separated words (translated names like
# "Kurosaki Ichigo" span several) carrying no bracket, quote, colon or sentence
# punctuation — that shape plus the entity gate in _fold_speaker_line is what
# keeps a narrative clause ending in a colon out. Groups: label, colon, the gap
# after it (preserved verbatim), and the bracketed remainder.
_NAME_TOKEN = r"[^\s:：【】\[\]|\"“”，。！？、,.!?]{1,20}"
_SPEAKER_RE = re.compile(
    r"^\s*(" + _NAME_TOKEN + r"(?: " + _NAME_TOKEN + r"){0,3})([:：])(\s*)([【\[].*)$")
# The same fold with no entity gate, for books whose notifications are
# introduced by a thing rather than a person (`Bullet comments: 【…】`). Any
# label goes, as long as it carries no bracket or pipe of its own and ends in a
# real character — so a line that STARTS with a notification (an already-folded
# one included) can never match, which keeps the fold idempotent. The prefix is
# greedy, so with several colons the one nearest the bracket separates.
_ANY_LABEL_RE = re.compile(
    r"^\s*([^【】\[\]|]*[^\s:：【】\[\]|])([:：])(\s*)([【\[].*)$")
# The colon + gap + description trailing a bracketed label, for the
# "fold_trailing_text" setting: `【Food Appraisal】: An excellent chef…`. The
# label side is matched by bracket balance (:func:`_leading_span`), not by this
# pattern — only the tail is a regex.
_TRAILING_RE = re.compile(r"^([:：])(\s*)(\S.*)$")
# One entry of a stat panel: `Knife Work (Intermediate): 8604/10000 (…)`.
# The name may itself carry a colon (`Special Skill: Food Appraisal (Master):
# …`), so the anchor is the FIRST parenthesized level followed by a colon —
# hence the lazy name. Brackets and pipes are excluded throughout: a line
# carrying either is a notification (or a table row), not a stat entry.
_STAT_RE = re.compile(
    r"^[^【】\[\]|]{1,80}?[（(][^()（）【】\[\]|]{1,30}[)）]\s*[:：]\s*\S.*$")
# The elision a panel uses in place of the rest of a long list: `…`, `……`,
# `...`, `......`.
_ELLIPSIS_RE = re.compile(r"^[.。．·…]{1,12}$")
# An unbracketed sub-heading inside a panel: `Special Skills:`. Only honoured
# when a stat entry follows it (see :func:`_panel_section`) — the shape alone
# is also that of a narrative clause ending in a colon.
_BARE_HEADER_RE = re.compile(r"^[^【】\[\]|:：。．!?！？]{1,40}[:：]$")
# Longest stat-panel section we will scan, in raw lines. With chapter_spacing's
# double-spacing that is ~100 entries.
_MAX_PANEL_SPAN = 200


def _notif(line):
    """Return the inner text of a whole-line 【…】 / [...] notification, else None."""
    if not isinstance(line, str):
        return None
    m = _NOTIF_RE.match(line)
    if not m:
        return None
    # Exactly one of the two alternation groups matched.
    inner = m.group(1) if m.group(1) is not None else m.group(2)
    # A purely numeric [n] is a footnote marker / danmaku count, not a
    # notification — swallowing it into a table breaks the footnote system.
    if m.group(2) is not None and inner.strip().isdigit():
        return None
    return inner


def _bracket_spans(line):
    """Return the top-level bracket spans (brackets included) of ``line``, or
    None if the line is anything other than a whitespace-separated run of
    balanced bracket groups.

    Nesting is depth-tracked per style, so ``【Li Yu: 【Video】】`` is ONE span,
    while ``[A] [B]`` is two. Any non-whitespace character outside a group
    (ordinary prose around a bracketed term) → None; so does an unbalanced
    bracket, which belongs to the multi-line block path instead.
    """
    if not isinstance(line, str):
        return None
    s = line.strip()
    if not s:
        return None
    spans = []
    i, n = 0, len(s)
    while i < n:
        if s[i].isspace():
            i += 1
            continue
        pair = next((p for p in _BRACKETS if s[i] == p[0]), None)
        if pair is None:
            return None  # bare text outside a bracket group — prose
        opener, closer = pair
        depth = 0
        j = i
        while j < n:
            if s[j] == opener:
                depth += 1
            elif s[j] == closer:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            return None  # unbalanced — not a whole-line run
        spans.append(s[i:j + 1])
        i = j + 1
    return spans or None


def _multi_notif(line):
    """Return one inner text per notification for a line packing 2+ of them,
    else None.

    ``[System status: ok] [Scanning...]`` → two cells. A purely numeric ASCII
    span anywhere in the run means footnote markers / danmaku counts, not
    notifications, so the whole line is left alone (mirrors :func:`_notif`).
    """
    spans = _bracket_spans(line)
    if spans is None or len(spans) < 2:
        return None
    inners = []
    for span in spans:
        inner = span[1:-1]
        if span[0] == "[" and inner.strip().isdigit():
            return None
        inners.append(inner)
    return inners


def _open_multiline(line):
    """Return ``(opener, closer, depth, first_cell)`` if ``line`` opens a
    multi-line notification, else None.

    A line opens a block when it starts with a bracket and its *net* bracket
    depth (openers minus closers of that style) is positive — i.e. the outer
    wrapper has no closer yet. Balanced interior pairs on the line (e.g.
    ``【Effect …35% of your 【Spirit】 into 【Agility】 and 【Strength】.``) are
    net-zero and so do not disqualify the line; only the unmatched leading
    bracket counts. ``first_cell`` strips just the leading opener, preserving
    any balanced interior brackets in the cell text.
    """
    if not isinstance(line, str):
        return None
    s = line.strip()
    for opener, closer in (("【", "】"), ("[", "]")):
        if s.startswith(opener):
            depth = s.count(opener) - s.count(closer)
            if depth > 0:
                return opener, closer, depth, s[len(opener):].strip()
    return None


def _multiline_notif(lines, i):
    """Match a notification block spanning several lines starting at ``i``.

    The opener line starts with 【 / [ with a positive net bracket depth; a
    later line brings the running depth back to 0 by ending with the matching
    】 / ]. Blank lines in between are skipped; every other line becomes a
    cell. Balanced interior pairs (on any line) are tracked by depth and left
    intact in the cell text. Returns ``(cells, last_index)`` or None. Aborts
    (None) on EOF, on exceeding ``_MAX_MULTILINE_SPAN``, on the depth going
    negative (more closers than openers — unbalanced), or on the block closing
    (depth 0) at a line that does not end with the closer (a mid-line closer,
    not a whole-line block) — so a stray unmatched bracket never swallows
    ordinary prose.
    """
    opened = _open_multiline(lines[i])
    if opened is None:
        return None
    opener, closer, depth, first = opened
    cells = [first] if first else []
    limit = min(len(lines), i + 1 + _MAX_MULTILINE_SPAN)
    for j in range(i + 1, limit):
        if not isinstance(lines[j], str):
            return None
        s = lines[j].strip()
        if not s:
            continue
        depth += s.count(opener) - s.count(closer)
        if depth < 0:
            return None  # more closers than openers — unbalanced, leave alone
        if depth == 0:
            if not s.endswith(closer):
                return None  # closer mid-line — not a whole-line block
            tail = s[:-len(closer)].strip()
            if tail:
                cells.append(tail)
            return (cells, j) if cells else None
        cells.append(s)
    return None


def _leading_span(line):
    """Return ``(span, tail)`` when ``line`` starts with a *balanced* bracket
    group, else None.

    ``span`` includes its brackets and is depth-matched, so ``【A: 【B】】: x``
    yields the whole outer group; ``tail`` is whatever follows it, stripped of
    trailing whitespace. A group that never closes on this line (the multi-line
    block case) returns None.
    """
    if not isinstance(line, str):
        return None
    s = line.strip()
    if not s:
        return None
    pair = next((p for p in _BRACKETS if s[0] == p[0]), None)
    if pair is None:
        return None
    opener, closer = pair
    depth = 0
    for j, ch in enumerate(s):
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return s[:j + 1], s[j + 1:]
    return None


def _fold_trailing_line(line):
    """Pull the description trailing a bracketed label into the notification.

    ``【Food Appraisal】: An excellent chef must…`` → ``【Food Appraisal: An
    excellent chef must…】``; ``【財富商城】：財富值達到1000開啟。`` →
    ``【財富商城：財富值達到1000開啟。】``. The colon and the gap after it are
    preserved verbatim, so English ``: `` and Chinese ``：`` each keep their own
    convention — the same rule :func:`_fold_speaker_line` follows for a label on
    the other side of the brackets.

    This is the inverse of the speaker fold, and needs no entity gate: the line
    must *begin* with a balanced bracket group, which narration ending in a
    colon cannot do. Left alone: an empty tail (``【Professional Skills】:`` — a
    stat-panel heading, see :func:`_panel_section`), a tail that is itself
    bracketed (``【A】: 【B】`` — two notifications, not a description), and a
    numeric ASCII label (``[1]: …`` is a footnote marker or a Markdown link
    reference definition). Idempotent: the result carries no post-closer tail.
    """
    got = _leading_span(line)
    if got is None:
        return line
    span, tail = got
    inner = span[1:-1]
    if span[0] == "[" and inner.strip().isdigit():
        return line
    m = _TRAILING_RE.match(tail)
    if not m:
        return line
    colon, gap, body = m.groups()
    if _bracket_spans(body) is not None:
        return line
    trail = line[len(line.rstrip()):]
    return span[0] + inner.strip() + colon + gap + body + span[-1] + trail


def _fold_trailing_lines(content):
    """Apply :func:`_fold_trailing_line` to a line array; identity on no-op."""
    if not isinstance(content, list):
        return content
    out = [_fold_trailing_line(l) for l in content]
    return out if out != content else content


def _panel_header(line):
    """Return a stat panel's heading text when ``line`` is ``【Heading】:`` with
    nothing after the colon, else None.

    The colon is kept in the returned cell (``Professional Skills:``) so the
    heading still reads as one inside the box, and so reversal can put the line
    back the way it was.
    """
    got = _leading_span(line)
    if got is None:
        return None
    span, tail = got
    if tail not in (":", "："):
        return None
    inner = span[1:-1].strip()
    return inner + tail if inner else None


def _panel_section(lines, i):
    """Match a stat-panel section — ``【Heading】:`` plus the unbracketed entries
    under it — starting at ``i``. Returns ``(cells, last_index)`` or None.

    A "system panel" prints its headings in brackets but its entries bare::

        【Professional Skills】:

        Knife Work (Intermediate): 8604/10000 (…)

        Heat Control (Beginner): 668/1000 (…)

    so the heading alone converts to a one-row table and the entries stay loose
    prose underneath it. This pulls them in: the heading is the first cell and
    every following entry is a row, which (via the run-grouping in
    :func:`_to_tables`) puts the whole panel — the ``【Player: …】`` lines above
    and the ``【Main Quest: …】`` lines below included — into one box.

    An entry is a stat line (:data:`_STAT_RE`), an elision (``……``), or an
    unbracketed sub-heading (``Special Skills:``) — the last only when a stat
    line follows it, since that shape is also an ordinary clause ending in a
    colon. Blank lines are skipped; the section ends at the first line that is
    none of those, so the prose after the panel is untouched. Returns None
    unless at least one stat line was absorbed, so a lone ``【Heading】:`` is
    left to the other matchers.
    """
    header = _panel_header(lines[i])
    if header is None:
        return None
    cells = [header]
    stats = 0
    last = i
    pending = None  # a sub-heading held back until a stat line justifies it
    limit = min(len(lines), i + 1 + _MAX_PANEL_SPAN)
    for j in range(i + 1, limit):
        if not isinstance(lines[j], str):
            break
        s = lines[j].strip()
        if not s:
            continue
        if _STAT_RE.match(s):
            if pending:
                cells.append(pending)
                pending = None
            cells.append(s)
            stats += 1
            last = j
        elif stats and _ELLIPSIS_RE.match(s):
            cells.append(s)
            last = j
        elif pending is None and _BARE_HEADER_RE.match(s):
            pending = s
        else:
            break
    return (cells, last) if stats else None


def _notif_at(lines, i, stat_panel=False):
    """Return ``(cells, last_index)`` for a notification starting at ``i`` —
    several on one line, one line/one cell, a multi-line block, or (with
    ``stat_panel``) a ``【Heading】:`` panel section — else None.

    The multi-span check runs FIRST: ``_notif``'s greedy pattern also matches
    ``[A] [B]``, but as a single cell whose text still carries ``] [``.
    """
    multi = _multi_notif(lines[i])
    if multi is not None:
        return multi, i
    cell = _notif(lines[i])
    if cell is not None:
        return [cell], i
    if stat_panel:
        panel = _panel_section(lines, i)
        if panel is not None:
            return panel
    return _multiline_notif(lines, i)


def _esc_cell(text):
    """Escape a notification's text for use inside a Markdown table cell."""
    return text.strip().replace("|", "\\|")


def _make_table(cells):
    """Build a single-column table: cell[0] is the header, the rest are rows."""
    rows = ["| " + _esc_cell(cells[0]) + " |", "| --- |"]
    rows += ["| " + _esc_cell(c) + " |" for c in cells[1:]]
    return rows


def _to_tables(lines, stat_panel=False):
    """Collapse runs of 【…】 / [...] notification lines into single-column tables.

    Both bracket styles count as notifications, whether single-line or a
    multi-line block (opener line starts the bracket, a later line closes it —
    each inner paragraph becomes a row). Notifications separated only by blank
    lines are merged into one table. With ``stat_panel``, a ``【Heading】:`` and
    the unbracketed stat entries under it count as one such notification
    (:func:`_panel_section`), so a system panel boxes up whole rather than
    breaking at its first heading. Idempotent: the bracket markers are
    consumed, so re-running is a no-op.
    """
    if not isinstance(lines, list):
        return lines
    n = len(lines)
    out = []
    i = 0
    changed = False
    while i < n:
        hit = _notif_at(lines, i, stat_panel)
        if hit is None:
            out.append(lines[i])
            i += 1
            continue
        # Start a group; gather following notifications, hopping over blank lines
        # only when another notification follows before any real content.
        cells, last = hit
        j = last + 1
        while j < n:
            nt = _notif_at(lines, j, stat_panel)
            if nt is not None:
                more, last = nt
                cells.extend(more)
                j = last + 1
            elif isinstance(lines[j], str) and lines[j].strip() == "":
                k = j
                while k < n and isinstance(lines[k], str) and lines[k].strip() == "":
                    k += 1
                if k < n and _notif_at(lines, k, stat_panel) is not None:
                    j = k  # blanks bridge two notifications — keep the group going
                else:
                    break  # blanks lead to content/EOF — group ends at `last`
            else:
                break
        out.extend(_make_table(cells))
        changed = True
        # Resume after the last notification; trailing blanks (if any) are
        # re-emitted normally so the table stays separated from what follows.
        i = last + 1
    return out if changed else lines


def _split_notif_lines(lines):
    """Give every notification its own line: ``[A] [B]`` → ``[A]``, ``""``,
    ``[B]``.

    Used on the *source* side (brackets are kept — the model still needs to see
    them) and by the one-off repair script for text already stored. Only lines
    that are nothing but a run of 2+ balanced bracket spans are touched, so
    prose mentioning two bracketed terms is safe. Trailing whitespace is copied
    onto each piece so a Markdown hard break (``"  "``) survives the split.
    Idempotent: the pieces are single-span lines, which no longer match.
    """
    if not isinstance(lines, list):
        return lines
    out = []
    changed = False
    for line in lines:
        spans = _bracket_spans(line) if _multi_notif(line) is not None else None
        if not spans:
            out.append(line)
            continue
        tail = line[len(line.rstrip()):]
        for idx, span in enumerate(spans):
            out.append(span + tail)
            if idx != len(spans) - 1:
                out.append("")
        changed = True
    return out if changed else lines


def _fold_speaker_line(line, names, any_label=False):
    """Pull a speaker label into the notification it introduces.

    ``Molly: 【Because you were tested twice…】`` → ``【Molly: Because you were
    tested twice…】`` (and ``茉莉：【…】`` → ``【茉莉：…】``). The colon and the
    spacing after it are preserved verbatim, so the English ``: `` and the
    Chinese ``：`` each keep their own convention.

    ``names`` is the speaker gate: the label must match one of the book's
    person-like entities exactly. Shape alone cannot tell a label from the tail
    of a narrative clause — ``看完之後，茉莉說：【去吃飯吧。】`` and ``Molly popped
    up again, angling for attention: 【…】`` have the same shape but must stay
    outside the box. A ``None`` gate (no db) folds nothing.

    ``any_label`` (the per-book setting) drops that gate: every label before the
    colon folds, entity or not, db or not — ``Bullet comments: 【…】`` →
    ``【Bullet comments: …】``. It is a superset of the gated path, so the two are
    alternatives, not layers.
    """
    if not isinstance(line, str):
        return line
    if any_label:
        m = _ANY_LABEL_RE.match(line.rstrip())
        if not m:
            return line
        name, colon, gap, rest = m.groups()
        name = name.strip()
        if not name:
            return line
    else:
        if not names:
            return line
        m = _SPEAKER_RE.match(line.rstrip())
        if not m:
            return line
        name, colon, gap, rest = m.groups()
        if name not in names:
            return line
    spans = _bracket_spans(rest)
    if not spans or len(spans) != 1:
        return line
    span = spans[0]
    tail = line[len(line.rstrip()):]
    return span[0] + name + colon + gap + span[1:-1] + span[-1] + tail


def _fold_speaker_lines(content, names, any_label=False):
    """Apply :func:`_fold_speaker_line` to a line array; identity on no-op."""
    if not isinstance(content, list):
        return content
    out = [_fold_speaker_line(l, names, any_label) for l in content]
    return out if out != content else content


def _cell_text(inner):
    """Return the single cell of a one-column row body, or None if multi-column.

    ``inner`` is the text between a row's outer pipes. An unescaped interior
    pipe means more than one column, which is not a table this module produced.
    """
    prev = ""
    for ch in inner:
        if ch == "|" and prev != "\\":
            return None
        prev = ch
    return inner.replace("\\|", "|").strip()


def _panel_entry(cells, k):
    """True when ``cells[k]`` is a stat-panel entry — the reversal-side mirror
    of the entry test in :func:`_panel_section`.

    A stat line, or a sub-heading immediately followed by one. An elision is
    deliberately not an entry here: :func:`_panel_section` will not start a
    section on one either, so treating it as one would revert to something that
    no longer re-converts.
    """
    if k >= len(cells):
        return False
    c = cells[k]
    if _STAT_RE.match(c):
        return True
    if _BARE_HEADER_RE.match(c):
        return k + 1 < len(cells) and bool(_STAT_RE.match(cells[k + 1]))
    return False


def _revert_cells(cells, stat_panel=False):
    """Render a reverted table's cells as source paragraphs.

    Normally each cell comes back as ``【cell】``. With ``stat_panel`` on, a
    heading cell that is actually followed by entries comes back as
    ``【Heading】:`` and its entries come back unbracketed, so a panel is
    restored the way it came rather than as one notification per row.

    The walk mirrors :func:`_panel_section` exactly, which is what keeps the
    reversal *stable*: a cell is only un-bracketed when re-converting would
    absorb it again. A lone ``【Warning:】`` notification, or a stat-shaped cell
    with no heading above it, therefore keeps its brackets.

    Every heading cell comes back bracketed, so a panel's *unbracketed*
    sub-heading (``Special Skills:``) gains brackets — the table stores the two
    forms identically, so they are no longer tellable apart, and the common
    case is the bracketed one. It re-converts to the same table, so the
    normalization is stable, in the same one-way way a folded speaker label is.
    """
    if not stat_panel:
        return ["【" + c + "】" for c in cells]
    out = []
    i = 0
    n = len(cells)
    while i < n:
        c = cells[i]
        if not (_BARE_HEADER_RE.match(c) and _panel_entry(cells, i + 1)):
            out.append("【" + c + "】")
            i += 1
            continue
        out.append("【" + c[:-1].strip() + "】" + c[-1])
        i += 1
        while i < n:
            e = cells[i]
            if not (_STAT_RE.match(e) or _ELLIPSIS_RE.match(e)):
                break  # another heading, or the end of the panel
            out.append(e)
            i += 1
    return out


def _from_tables(lines, stat_panel=False):
    """Reverse :func:`_to_tables`: single-column tables → double-spaced 【…】.

    Only tables carrying this module's exact fingerprint are reverted: every
    row in the `| cell |` spacing _make_table emits, and the separator row
    exactly `| --- |`. A hand-authored or chatgroup-style single-column table
    (compact `|:---|`, unpadded pipes) is left untouched — previously ANY
    single-column pipe table was reversed on disable.
    Idempotent: reverted blocks no longer match a table run.

    ``stat_panel`` mirrors the setting of the same name on the way back — see
    :func:`_revert_cell`.
    """
    if not isinstance(lines, list):
        return lines
    n = len(lines)
    out = []
    i = 0
    changed = False
    while i < n:
        if not (isinstance(lines[i], str) and _ROW_RE.match(lines[i])):
            out.append(lines[i])
            i += 1
            continue
        # Collect a contiguous run of table rows (no blank lines between them).
        run = []
        j = i
        while j < n and isinstance(lines[j], str) and _ROW_RE.match(lines[j]):
            run.append(lines[j])
            j += 1
        inners = [_ROW_RE.match(r).group(1) for r in run]
        sep_ok = len(run) >= 2 and run[1].strip() == "| --- |"
        # Fingerprint: _make_table always writes "| cell |" with the padding
        # spaces; anything else was not produced by this module.
        ours = all(r.strip().startswith("| ") and r.strip().endswith(" |")
                   for idx, r in enumerate(run) if idx != 1)
        # Reconstruct cells from every row except the separator (index 1).
        cells = [_cell_text(s) for idx, s in enumerate(inners) if idx != 1]
        if sep_ok and ours and all(c is not None for c in cells):
            reverted = _revert_cells(cells, stat_panel)
            for idx, c in enumerate(reverted):
                out.append(c)
                if idx != len(reverted) - 1:
                    out.append("")
            changed = True
        else:
            out.extend(run)  # not one of ours — leave as-is
        i = j
    return out if changed else lines


def _convert(lines, names, any_label, fold_trailing=False, stat_panel=False):
    """The full forward pass: fold labels in, then build the tables.

    Folding runs first on both sides — a folded line is a whole-line
    notification, so the table conversion then picks it up as a row.
    """
    lines = _fold_speaker_lines(lines, names, any_label)
    if fold_trailing:
        lines = _fold_trailing_lines(lines)
    return _to_tables(lines, stat_panel)


class MarkdownNotificationsModule(TranslationModule):
    id = "markdown_notifications"
    name = "Markdown Notifications"
    description = ("Render 【…】 system/notification blocks as boxed Markdown "
                   "tables, merging a run of adjacent notifications into one "
                   "table, and give each notification its own row when a source "
                   "line packs several. Enabling it converts all existing "
                   "chapters; disabling it reverts them back to 【…】 paragraphs.")
    # Auto-off: no default, no URL patterns — manual per book only.
    # The label fold is part of the backfill, so re-derive it when the setting
    # changes (remove → persist → add).
    rebuild_on_settings_change = True

    settings_schema = [
        {"key": "fold_any_label", "type": "bool", "default": False,
         "label": "Fold any label before the colon into the notification",
         "help": ("On: `Bullet comments: 【…】` becomes `【Bullet comments: …】` "
                  "whatever the label is. Off (default): only a label matching "
                  "one of the book's characters is folded in, so a narrative "
                  "clause ending in a colon stays outside the box.")},
        {"key": "fold_trailing_text", "type": "bool", "default": False,
         "label": "Fold the description after the colon into the notification",
         "help": ("On: `【Food Appraisal】: An excellent chef must…` becomes "
                  "`【Food Appraisal: An excellent chef must…】`, so the label "
                  "and its description share one box. Off (default): the "
                  "bracketed label boxes up alone and the description is left "
                  "as prose beside it.")},
        {"key": "stat_panel", "type": "bool", "default": False,
         "label": "Absorb stat entries under a 【Heading】: into the panel",
         "help": ("On: a `【Professional Skills】:` heading pulls in the "
                  "unbracketed `Knife Work (Intermediate): 8604/10000 (…)` "
                  "entries below it as rows, so a system status panel boxes up "
                  "whole instead of breaking at its first heading. Off "
                  "(default): only the bracketed lines are boxed.")},
    ]

    def _settings(self, ctx):
        return self.resolve_settings((ctx.get("module_settings") or {}).get(self.id))

    def _fold_args(self, ctx):
        """``(names, any_label)`` for :func:`_fold_speaker_lines` in this ctx."""
        book = ctx.get("book")
        book_id = book.get("id") if (book and hasattr(book, "get")) else None
        any_label = bool(self._settings(ctx).get("fold_any_label"))
        names = None if any_label else load_person_names(ctx.get("db"), book_id)
        return names, any_label

    def _convert_args(self, ctx):
        """``(names, any_label, fold_trailing, stat_panel)`` for this ctx."""
        s = self._settings(ctx)
        return self._fold_args(ctx) + (bool(s.get("fold_trailing_text")),
                                       bool(s.get("stat_panel")))

    def transform_source_lines(self, content, ctx):
        """Normalize notification paragraphs before translation.

        Brackets stay — the model still needs to see them. A speaker label
        outside its notification is folded in, and a line packing several
        notifications is split one per paragraph, so the model translates them
        in the shape the table conversion expects.
        """
        content = _fold_speaker_lines(content, *self._fold_args(ctx))
        return _split_notif_lines(content)

    def transform_translated_lines(self, content, ctx):
        return _convert(content, *self._convert_args(ctx))

    def event_add_to_book(self, ctx):
        """Backfill: convert notifications in every existing chapter.

        Folding runs first here too, so a chapter translated before the module
        (or before one of the fold settings) gets the same treatment a new one
        does.
        """
        args = self._convert_args(ctx)
        self._rewrite_all(ctx, lambda lines: _convert(lines, *args), "converted")

    def event_removed_from_book(self, ctx):
        """Reverse: turn this module's tables back into 【…】 paragraphs."""
        stat_panel = bool(self._settings(ctx).get("stat_panel"))
        self._rewrite_all(
            ctx, lambda lines: _from_tables(lines, stat_panel), "reverted")

    def _rewrite_all(self, ctx, fn, verb):
        db = ctx.get("db")
        book = ctx.get("book")
        logger = ctx.get("logger")
        if db is None or not book:
            return
        book_id = book.get("id") if hasattr(book, "get") else None
        if not book_id:
            return

        conn = db.backend.get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, translated_content FROM chapters WHERE book_id = ?",
            (book_id,))
        rows = cur.fetchall()
        changed = 0
        for cid, raw in rows:
            if not raw:
                continue
            try:
                lines = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(lines, list):
                continue
            new = fn(lines)
            if new != lines:
                cur.execute(
                    "UPDATE chapters SET translated_content = ? WHERE id = ?",
                    (json.dumps(new, ensure_ascii=False), cid))
                changed += 1
        conn.commit()
        conn.close()
        if changed:
            try:
                db.invalidate_epub_cache(book_id)
            except Exception:
                pass
        if logger:
            logger.info(
                f"markdown_notifications: {verb} {changed} chapter(s) for book {book_id}")
        log_module_activity(
            db, "info", f"{self.name}: {verb} {changed} chapter(s)", book_id)
