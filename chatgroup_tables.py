#!/usr/bin/env python3
"""Wrap runs of chat-group messages in a book's TRANSLATED chapters into a
Markdown 2-column table (| Username | Message |) so the chat log renders as a
boxed UI element in the reader and EPUB.

Chapter content is a list of paragraph "lines" ("" separates paragraphs). A chat
message looks like:

    Be My Son: "Welcome, newcomer."

A maximal run of consecutive chat messages (blank separators between them are
folded in) becomes a SINGLE list element — the table's rows joined by "\\n" —
because a Markdown table needs its rows on consecutive lines. The renderer
(chapterMarkdown.js / output_formatter._render_markdown, `tables` extension)
joins runs with "\\n", so the table renders correctly while ⟦IMG⟧ markers and
surrounding prose stay put.

WHITELIST-DRIVEN: a line only starts/continues a chat run if its prefix before
the first colon exactly matches a username in the book's "chatgroup usernames"
entity category. This avoids tabling ordinary "Word:" lines (Status:, Ding.,
dialogue attribution) and means the script auto-adapts as you curate members —
add a username to the entity DB, re-run, and that member's lines get tabled too.

SYSTEM EVENTS: chat-group system lines that sit among the messages are folded
into the same table so they don't break a run. They render "attributed":
  * "Ding. Member 【X】 has joined the Chat Group."  -> | X | joined the Chat Group |
  * "【Group Notice: …】"                             -> | System | … |
  * "Ding. … chat group … muted/disbanded/…"        -> | System | … |
Detection is deliberately narrow (the 【】 bracket is overloaded in these books
for news, status screens, item cards and inline mentions — those are NOT caught).

IDEMPOTENT & SELF-MERGING: a generated table is parsed back into its rows when
re-encountered, so re-running is a no-op when nothing changed AND it re-merges
fragments. If you miss a username, run, add it, and re-run, that member's lines
are absorbed into the adjacent existing table instead of spawning a new one.
Safe to run again after the queue translates more chapters or after adding
members.

Usage:
    python3 chatgroup_tables.py --book-id 43 [--dry-run is default]
    python3 chatgroup_tables.py --book-id 43 --apply
    python3 chatgroup_tables.py --book-id 43 --chapters 2-50 --apply
"""
import argparse
import json
import os
import re

import db_backend
from config import TranslationConfig

# Header emitted once per contiguous chat block. Edit here to taste.
HEADER = ["| Username | Message |", "|:---|:---|"]
# Either ASCII ":" or full-width "：" may follow the username.
COLON_RE = re.compile(r"\s*[:：]\s*")

# --- chat-group system events (rendered "attributed", see module docstring) ---
DING_RE = re.compile(r"^Ding[.,]\s*")
# "Member 【X】 has joined/left/… the Chat Group" -> attribute to member X.
MEMBER_EVENT_RE = re.compile(r"^Member\s*【([^】]+)】\s+has\s+(.+?)\.?$")
# "Group Notice: <body>" -> System row.
GROUP_NOTICE_RE = re.compile(r"^Group Notice\s*[:：]\s*(.+?)\s*$")
# Other whole-group events (muted, disbanded, …) — only when the line is clearly
# a system line (started with "Ding." or wrapped in 【】) to avoid catching prose.
EVENT_VERB_RE = re.compile(
    r"\b(muted|unmuted|disbanded|dissolved|renamed|removed|kicked|banned|error)\b", re.I)
SYSTEM_LABEL = "System"


def load_usernames(cur, book_id, category):
    """Return usernames sorted longest-first so a longer handle claims before a
    shorter one that happens to be its prefix."""
    cur.execute(
        "SELECT translation FROM entities WHERE book_id=? AND category=?",
        (book_id, category),
    )
    names = []
    for row in cur.fetchall():
        t = (row["translation"] if isinstance(row, dict) else row[0]) or ""
        t = t.strip()
        if t:
            names.append(t)
    return sorted(set(names), key=len, reverse=True)


def match_chat(line, usernames):
    """If `line` is "<known username>: <message>", return (username, message);
    else None. Message keeps whatever quoting/punctuation the model emitted."""
    s = line.strip()
    if not s or s.startswith(("|", "⟦")):
        return None
    for name in usernames:
        if s.startswith(name):
            rest = s[len(name):]
            m = COLON_RE.match(rest)
            if m:
                return name, rest[m.end():].strip()
    return None


def match_system(line):
    """If `line` is a chat-group system event, return (col1, col2) rendered
    "attributed" (member name for joins, "System" for notices); else None."""
    raw = line.strip()
    if not raw or raw.startswith(("|", "⟦")):
        return None
    s = raw.strip('"').strip()
    bracketed = s.startswith("【") and s.endswith("】")
    s = s.strip("【】").strip()
    dinged = bool(DING_RE.match(s))
    s = DING_RE.sub("", s).strip()

    m = MEMBER_EVENT_RE.match(s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = GROUP_NOTICE_RE.match(s)
    if m:
        return SYSTEM_LABEL, m.group(1).strip().rstrip(".")
    if (dinged or bracketed) and re.search(r"chat group", s, re.I) and EVENT_VERB_RE.search(s):
        return SYSTEM_LABEL, s.rstrip(".")
    return None


def match_row(line, usernames):
    """A table row from a chat message or a chat-group system event, else None."""
    return match_chat(line, usernames) or match_system(line)


# Split on "|" delimiters only (not escaped "\|" inside a cell).
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def parse_table(element):
    """If `element` is a table WE generated, return its [(col1, col2)] rows so it
    can be re-absorbed into a run (enables merge + true idempotency). Else None.

    Round-trips exactly: fmt_table(parse_table(x)) == x for our own output, so a
    no-op re-run leaves the chapter unchanged."""
    if not isinstance(element, str) or "\n" not in element:
        return None
    rows_lines = element.split("\n")
    if (len(rows_lines) < 3
            or rows_lines[0].strip() != HEADER[0]
            or rows_lines[1].strip() != HEADER[1]):
        return None
    rows = []
    for ln in rows_lines[2:]:
        ln = ln.strip()
        if not ln:
            continue
        if not (ln.startswith("|") and ln.endswith("|")):
            return None
        cells = CELL_SPLIT_RE.split(ln)[1:-1]
        if len(cells) != 2:
            return None
        rows.append((cells[0].replace("\\|", "|").strip(),
                     cells[1].replace("\\|", "|").strip()))
    return rows or None


def rows_from_line(line, usernames):
    """All table rows a single list-element contributes — a previously-generated
    table yields its parsed rows; a chat/system line yields one row; else None."""
    parsed = parse_table(line)
    if parsed is not None:
        return parsed
    hit = match_row(line, usernames)
    return [hit] if hit is not None else None


def esc(t):
    return t.replace("|", "\\|").strip()


def fmt_table(rows):
    body = [f"| {esc(u)} | {esc(msg)} |" for u, msg in rows]
    return "\n".join(HEADER + body)


def transform_chapter(lines, usernames, min_run):
    """Return (new_lines, n_tables, n_messages). Folds blank lines that sit
    BETWEEN two chat messages into the table; a trailing blank after the block
    is left in place so paragraph spacing survives."""
    out = []
    n = len(lines)
    n_tables = n_messages = 0
    i = 0
    while i < n:
        first = rows_from_line(lines[i], usernames)
        if first is None:
            out.append(lines[i])
            i += 1
            continue
        # Collect a maximal run, folding blank separators and absorbing any
        # existing tables in the run so fragments re-merge into one.
        rows = list(first)
        last = i
        j = i + 1
        while j < n:
            hit = rows_from_line(lines[j], usernames)
            if hit is not None:
                rows.extend(hit)
                last = j
                j += 1
            elif not lines[j].strip():
                # Look past blanks: continue only if another row source follows.
                k = j
                while k < n and not lines[k].strip():
                    k += 1
                if k < n and rows_from_line(lines[k], usernames) is not None:
                    j = k
                else:
                    break
            else:
                break
        if len(rows) >= min_run:
            out.append(fmt_table(rows))
            n_tables += 1
            n_messages += len(rows)
            i = last + 1  # leave the trailing blank (last+1) untouched
        else:
            # Below threshold — emit the original lines unchanged.
            out.extend(lines[i:last + 1])
            i = last + 1
    return out, n_tables, n_messages


def parse_range(spec):
    out = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Wrap chat-group messages in translated chapters into Markdown tables.")
    ap.add_argument("--book-id", type=int, required=True)
    ap.add_argument("--category", default="chatgroup usernames",
                    help='Entity category holding the username whitelist.')
    ap.add_argument("--chapters", default=None,
                    help='e.g. "2-50" or "2,3,7". Default: all chapters.')
    ap.add_argument("--min-run", type=int, default=1,
                    help="Minimum consecutive messages to form a table (default 1).")
    ap.add_argument("--apply", action="store_true",
                    help="Write changes (default: dry-run).")
    args = ap.parse_args()

    backend = db_backend.create_backend()
    conn = backend.get_connection()
    cur = conn.cursor()

    usernames = load_usernames(cur, args.book_id, args.category)
    if not usernames:
        print(f"No usernames in category {args.category!r} for book {args.book_id}. "
              "Nothing to do.")
        conn.close()
        return

    chap_filter = parse_range(args.chapters) if args.chapters else None

    cur.execute(
        "SELECT id, chapter_number, translated_content FROM chapters "
        "WHERE book_id=? ORDER BY chapter_number", (args.book_id,))
    chapters_changed = tot_tables = tot_messages = 0
    for row_id, num, raw in cur.fetchall():
        if not raw:
            continue
        if chap_filter is not None and num not in chap_filter:
            continue
        try:
            lines = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        new, n_tables, n_messages = transform_chapter(lines, usernames, args.min_run)
        if n_tables and new != lines:
            chapters_changed += 1
            tot_tables += n_tables
            tot_messages += n_messages
            print(f"ch{num}: {n_tables} table(s), {n_messages} message(s)")
            if args.apply:
                cur.execute(
                    "UPDATE chapters SET translated_content=? WHERE id=?",
                    (json.dumps(new, ensure_ascii=False), row_id))
    if args.apply:
        conn.commit()

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print("=" * 60)
    print(f"[{mode}] book {args.book_id}: {chapters_changed} chapter(s) changed, "
          f"{tot_tables} table(s), {tot_messages} message(s) "
          f"({len(usernames)} usernames)")

    if args.apply and chapters_changed:
        cache = os.path.join(
            TranslationConfig().script_dir, "epub_cache", f"{args.book_id}.epub")
        if os.path.exists(cache):
            os.remove(cache)
            print("Invalidated", cache)
    conn.close()


if __name__ == "__main__":
    main()
