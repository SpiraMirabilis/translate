#!/usr/bin/env python3
"""Wrap LitRPG System notifications / messages / status screens / skill cards in
Markdown tables so they render as boxed UI elements in the reader and EPUB.

The chapter content is a list of paragraph "lines" ("" separates paragraphs).
A Markdown table must have its rows on consecutive lines, so each detected block
is emitted as a SINGLE list element (rows joined by "\\n"); the renderer
(chapterMarkdown.js / output_formatter._render_markdown) joins runs with "\\n",
so the table renders correctly while keeping illustrations (⟦IMG⟧) in place.

Detection: a block STARTS at a strong marker — "Attention!", "Skill Card",
"True Name:", "Status: Player", "Rank increased", "Choose/Select a …
development …" — and extends through the contiguous system lines that follow
(type-specific; a skill card ends at its first "N/M SP" Saturation value).
Format is HYBRID: status screens & skill cards become 2-column label|value
tables (bold section sub-headers); plain notifications are 1-column.

EQUIPMENT blocks (System artifacts/items) have no fixed start marker — they
open with an arbitrary item name ("Marauder's Bag", "Active Armor GR-715.").
They are detected by LOOKAHEAD instead: a short non-system line whose next two
non-blank lines are "Rank [SABCDEF]" and "Type: …". The item name becomes the
table header; "Description:"/"Features:"/"Properties:" become bold section
rows, numbered features ("1. Officer's Aura:") pair with their following
bullet as label|value, and multi-bullet descriptions render one bullet per row.

SOURCE MODE (--source): transforms the RUSSIAN source instead — both
chapters.untranslated_content and pending queue.content rows — so blocks are
already boxed when (re)translation runs (add a prompt line for the book telling
the model to preserve Markdown tables). The Russian markers are far more
consistent than translation output («Внимание!» only — no «Предупреждение!»;
«Ранг повышен.»; «Выберите направление развития:»; «Карта навыка – X»;
«Истинное имя:»/«Статус: Игрок»; name + «Ранг X» + «Тип: …» for equipment).
Russian-specific safeguards:
  * rank letters mix Cyrillic lookalikes with Latin → [SABCDEFАВСЕ];
  * dialogue lines also start with "- " → bullets are accepted only directly
    after a label/numbered/bullet line, never after plain text;
  * card start needs the dash form AND a structural next line (excludes
    narrative "Карта навыка, …" and appendix inventory "… 1 шт." lines);
  * card extend is two-phase: greedy to a confirmed "N/M ОС" terminator,
    else structural lines only (some cards run straight into narrative).

IDEMPOTENT: generated tables begin with "|", which never matches a strong
marker, so re-running only wraps newly-translated/unwrapped blocks. Safe to run
again after the queue translates or after adding more books.

Usage:
    python3 wrap_system_tables.py --book-id 39 --skip 28,64,101,130 [--apply]
    python3 wrap_system_tables.py --book-id 39 --source --skip 28,64,101,130 [--apply]

--skip is for stat-sheet appendix chapters (e.g. "Характеристики"/"Stats and
skills") whose dump structure differs from in-narrative system blocks.
"""
import argparse
import json
import re
import sys

import db_backend

# ---------------------------------------------------------------------------
# Pattern profile. Module globals below match the ENGLISH translated text;
# use_source_profile() rebinds them all for the RUSSIAN source.
SOURCE = False
ATTN = re.compile(r'^(Attention|Warning)!')
CARD = re.compile(r'^Skill Card\b')
CARD_SPLIT = re.compile(r'^(Skill Card)\s*[–—-]\s*(.+)$')
STATUS_START = re.compile(r'^(True Name\s*[:–-]|Status\s*:\s*Player\b)')
STATUS_CONT = 'Status'
SP_LINE = re.compile(r'^\d+\s*/\s*\d+\s*SP\.?$')
RANK_ONLY = re.compile(r'^Rank\s+([SABCDEF][+-]?)\.?$')
TYPE_LINE = re.compile(r'^Type\s*[::]\s*(.+?)\s*$')
RANK_INC = re.compile(r'^Rank (increased|raised)\b')
# Upgrade-choice prompt; phrasing varies ("Choose/Select a direction of
# development / development path/direction / path of development"), colon
# optional. Translation output — re-audit for new phrasings with:
#   a "1." line followed by a "2." line whose preceding line isn't a block start.
CHOICE = re.compile(r'^(Choose|Select) a (?=[a-z ]*development)[a-z ]{3,40}:?$', re.I)
SECTION = re.compile(r'^(Characteristics|.*-rank skills?|Active skills|Passive skills|Skills)\s*:?\s*$', re.I)
BULLET = re.compile(r'^-\s+\S')
SHORT_KV = re.compile(r"^[A-Z][\w '’\-]{0,40}:\s+\S")
DESC_WORD = 'description'
L_SYSTEM, L_STATUS, L_RANK, L_TYPE = 'System', 'Status', 'Rank', 'Type'
SYS = [re.compile(p) for p in [
    r'^(Attention|Warning)!', r'^Skill Card\b', r'^Yes\s*/\s*No\.?$', r'^\(\d+\s*/\s*\d+( XP)?\)\.?$',
    r'^Rank [SABCDEF][+-]?\.?$',
    r'^(Description|Saturation|Characteristics|Active skills|Passive skills|Skills)\s*:?\s*$',
    r'^(True Name|Status|Callsign|Age|Race|Sex|Level)\s*[:–-]',
    r'^Free\b.*:', r'^[A-Za-z][A-Za-z \'\-]*\(\d+\s*/\s*\d+\)\.?$',
    r'^[A-Z][A-Za-z]+\s+\d+\s*/\s*\d+\.?$', r'^\d+\s*/\s*\d+\s*SP\.?$',
    r'^[A-Za-z][A-Za-z \-]*-rank skills?\s*:?\s*$', r'^-\s+.+',
    r'^Compatibility level \d+\s*%\.?$']]


def use_source_profile():
    """Rebind all patterns/labels for the Russian source text."""
    global SOURCE, ATTN, CARD, CARD_SPLIT, STATUS_START, STATUS_CONT, SP_LINE, \
        RANK_ONLY, TYPE_LINE, RANK_INC, CHOICE, SECTION, BULLET, SHORT_KV, \
        DESC_WORD, L_SYSTEM, L_STATUS, L_RANK, L_TYPE, SYS
    SOURCE = True
    ATTN = re.compile(r'^Внимание!')  # «Предупреждение!» never occurs in source
    # Dash form only — narrative sentences can start with "Карта навыка, …"
    CARD = re.compile(r'^Карта навыка\s*[–—-]\s*\S')
    CARD_SPLIT = re.compile(r'^(Карта навыка)\s*[–—-]\s*(.+)$')
    STATUS_START = re.compile(r'^(Истинное имя\s*[::–-]|Статус\s*[::]\s*Игрок\b)')
    STATUS_CONT = 'Статус'
    SP_LINE = re.compile(r'^\d+\s*/\s*\d+\s*ОС\.?$')
    # Source mixes Cyrillic lookalike letters («Ранг Е» = U+0415) with Latin
    # («Ранг D») — hence the widened class.
    RANK_ONLY = re.compile(r'^Ранг\s+([SABCDEFАВСЕ][+-]?)\.?$')
    TYPE_LINE = re.compile(r'^Тип\s*[::]\s*(.+?)\s*$')
    RANK_INC = re.compile(r'^Ранг повышен')
    CHOICE = re.compile(r'^Выбери(?:те)?\s(?=[а-яё ]*развити)[а-яё ]{3,45}:?$', re.I)
    SECTION = re.compile(r'^(Характеристики|Навыки\s+\S{1,4}\s+ранга|Активные навыки|Пассивные навыки|Навыки)\s*:?\s*$', re.I)
    # Russian dialogue also starts with "- " — extends only accept a bullet
    # directly after a label/numbered/bullet line (and SYS omits bullets).
    BULLET = re.compile(r'^-\s*[^\s\-]')
    SHORT_KV = re.compile(r"^[А-ЯЁ][\w ,'’«»\-]{0,40}:\s*\S")
    DESC_WORD = 'описание'
    L_SYSTEM, L_STATUS, L_RANK, L_TYPE = 'Система', 'Статус', 'Ранг', 'Тип'
    SYS = [re.compile(p) for p in [
        r'^Внимание!', r'^Карта навыка\s*[–—-]', r'^Да\s*/\s*Нет\.?$',
        r'^\(\d+\s*/\s*\d+(\s*ОС)?\)\.?$',
        r'^Ранг\s+[SABCDEFАВСЕ][+-]?\.?$',
        r'^(Описание|Насыщение|Характеристики|Особенности|Свойства|Ограничения|Навыки)\s*:?\s*$',
        r'^(Истинное имя|Статус|Позывной|Возраст|Раса|Пол|Уровень|Тип|Цель|Награда|Информация)\s*[::–-]',
        r'^Свободны[^:.!?]{0,40}:',
        r'^[А-ЯЁ][А-Яа-яЁё \'«»\-]*\(\d+\s*/\s*\d+\)\.?$',
        r'^[А-ЯЁ][А-Яа-яЁё]+\s+\d+\s*/\s*\d+\.?$',
        r'^\d+\s*/\s*\d+\s*ОС\.?$',
        r'^Навыки\s+\S{1,4}\s+ранга\s*:?\s*$',
        r'^Уровень соответствия \d+\s*%',
        r'^При изучении шанс успеха \d+\s*%']]


def is_sys(p):
    p = p.strip()
    return any(r.match(p) for r in SYS)


def block_type(p):
    p = p.strip()
    if CARD.match(p):
        return 'card'
    if STATUS_START.match(p):
        return 'status'
    if ATTN.match(p) or RANK_INC.match(p):
        return 'notif'
    if CHOICE.match(p):
        return 'choice'
    return None


def next_nonblank(lines, k):
    while k < len(lines):
        if lines[k].strip():
            return lines[k].strip()
        k += 1
    return None


def card_start(lines, i):
    """In source mode a card start additionally needs a structural next line —
    this excludes appendix inventory entries ("Карта навыка – Гигантизм 1 шт.",
    where the next line is another inventory item)."""
    if not CARD.match(lines[i].strip()):
        return False
    if not SOURCE:
        return True
    nxt = next_nonblank(lines, i + 1)
    return bool(nxt is not None and (RANK_ONLY.match(nxt)
                or re.match(r'^(При изучении|Насыщение)', nxt)))


def equip_start(lines, i):
    """Equipment has no strong start marker: detect an item-name line by
    lookahead — the next two non-blank lines must be "Rank X" then "Type: …"."""
    name = lines[i].strip()
    if not name or len(name) > 80 or name.startswith(('|', '⟦')):
        return False
    if block_type(name):
        return False
    follow = []
    k = i + 1
    while k < len(lines) and len(follow) < 2:
        if lines[k].strip():
            follow.append(lines[k].strip())
        k += 1
    return (len(follow) == 2 and RANK_ONLY.match(follow[0])
            and TYPE_LINE.match(follow[1]))


def is_block_start(lines, i):
    bt = block_type(lines[i])
    if bt == 'card':
        return card_start(lines, i)
    if bt:
        return True
    return equip_start(lines, i)


def esc(t):
    return t.replace('|', '\\|').strip()


def parse_kv(s):
    s = s.strip()
    m = re.match(r'^([A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё ]+?)\s+(\d+\s*/\s*\d+)\.?$', s)
    if m:
        return (m.group(1).strip(), m.group(2).replace(' ', ''))
    m = re.match(r'^(.+?)\s*\((\d+\s*/\s*\d+)\)\.?$', s)
    if m:
        return (m.group(1).strip(), m.group(2).replace(' ', ''))
    m = RANK_ONLY.match(s)
    if m:
        return (L_RANK, m.group(1))
    m = re.match(r'^(.+?):\s+(.+)$', s)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    m = re.match(r'^(.+?)\s+[–—-]\s+(.+)$', s)
    if m:
        return (m.group(1).strip(), m.group(2).strip())
    return (None, None)


def extend(lines, i):
    """Return (type, [non-empty paragraph strings], end_index_inclusive)."""
    typ = block_type(lines[i]) or 'equip'
    n = len(lines)
    paras = [lines[i].strip()]
    k = i + 1
    last_end = i
    if typ == 'card':
        if SOURCE:
            # Two-phase: greedy up to a CONFIRMED "N/M ОС" terminator (free-form
            # description lines ride along); without one, accept structured
            # lines only — free text after a card may already be narrative.
            scan = []
            end_found = False
            kk = k
            while kk < n and len(scan) < 25:
                q = lines[kk].strip()
                if q:
                    if q.startswith(('|', '⟦')) or block_type(q):
                        break
                    scan.append((kk, q))
                    if SP_LINE.match(q):
                        end_found = True
                        break
                kk += 1
            if end_found:
                for kk, q in scan:
                    paras.append(q)
                    last_end = kk
            else:
                # Free-form description paragraphs are allowed directly after
                # "Описание:" while they speak System-voice (formal 2nd person
                # Вы/Ваш/вам…) — narrative is 1st person and breaks the run.
                vy_form = re.compile(r'\b[Вв](?:ы|ам|ас|аш\w*)\b')
                bullet_ok = False
                desc_ctx = False
                for kk, q in scan:
                    if (is_sys(q) or q.endswith(':') or SHORT_KV.match(q)
                            or (BULLET.match(q) and bullet_ok)
                            or (desc_ctx and vy_form.search(q[:60]))):
                        bullet_ok = q.endswith(':') or bool(BULLET.match(q))
                        desc_ctx = (bool(re.match(r'^Описание\s*:?\s*$', q))
                                    or (desc_ctx and bool(vy_form.search(q[:60]))))
                        paras.append(q)
                        last_end = kk
                    else:
                        break
        else:
            while k < n and len(paras) < 20:
                q = lines[k]
                if not q.strip():
                    k += 1
                    continue
                if q.strip().startswith(('|', '⟦')) or block_type(q) in ('card', 'status'):
                    break
                paras.append(q.strip())
                last_end = k
                if SP_LINE.match(q.strip()):  # Saturation value -> card ends
                    break
                k += 1
    elif typ == 'status':
        last_label = False
        while k < n:
            q = lines[k]
            if not q.strip():
                k += 1
                continue
            if q.strip().startswith(('|', '⟦')):
                break
            if block_type(q) and not q.strip().startswith(STATUS_CONT):
                break
            if is_sys(q) or last_label:
                last_label = q.strip().endswith(':')
                paras.append(q.strip())
                last_end = k
                k += 1
            else:
                break
    elif typ == 'choice':
        # Numbered options, optional nested "Limitations:" + "- " bullets,
        # usually a trailing "(N/M)" saturation line (not always — ch113).
        bullet_ok = False
        while k < n:
            q = lines[k]
            if not q.strip():
                k += 1
                continue
            qs = q.strip()
            if qs.startswith(('|', '⟦')) or block_type(qs):
                break
            if (re.match(r'^\d+\.\s+\S', qs) or qs.endswith(':')
                    or re.match(r'^\(\d+\s*/\s*\d+', qs)
                    or (BULLET.match(qs) and bullet_ok)):
                bullet_ok = qs.endswith(':') or bool(BULLET.match(qs))
                paras.append(qs)
                last_end = k
                k += 1
            else:
                break
    elif typ == 'equip':
        last_label = False
        bullet_ok = False
        while k < n and len(paras) < 40:
            q = lines[k]
            if not q.strip():
                k += 1
                continue
            qs = q.strip()
            if qs.startswith(('|', '⟦')) or block_type(qs):
                break
            ok = (is_sys(qs) or TYPE_LINE.match(qs) or qs.endswith(':')
                  or re.match(r'^\d+\.\s+\S', qs)        # numbered feature
                  or SHORT_KV.match(qs)                  # short label: value
                  or re.match(r'^\d+\s*/\s*\d+\b', qs)   # saturation value
                  or (BULLET.match(qs) and bullet_ok)
                  or last_label)
            if not ok:
                break
            last_label = qs.endswith(':')
            bullet_ok = (qs.endswith(':') or bool(re.match(r'^\d+\.\s+\S', qs))
                         or bool(BULLET.match(qs)))
            paras.append(qs)
            last_end = k
            k += 1
    else:  # notif
        bullet_ok = False
        while k < n:
            q = lines[k]
            if not q.strip():
                k += 1
                continue
            qs = q.strip()
            if qs.startswith(('|', '⟦')) or block_type(qs):
                break
            ok = is_sys(qs)
            if SOURCE and not ok:
                # mission notifications carry labels, kv fields and bullets
                ok = (qs.endswith(':') or SHORT_KV.match(qs)
                      or (BULLET.match(qs) and bullet_ok))
            if not ok:
                break
            bullet_ok = qs.endswith(':') or bool(BULLET.match(qs))
            paras.append(qs)
            last_end = k
            k += 1
    return typ, paras, last_end


def fmt_equip(paras):
    # Name spans the left cell alone (no "| Equipment | name |" label split):
    # the bullet/feature rows make the left column wide anyway, and a label
    # split squeezes the right column instead.
    header = '| %s | |' % esc(paras[0].rstrip('.'))
    src = paras[1:]
    rows = []
    i = 0
    while i < len(src):
        s = src[i]
        m = RANK_ONLY.match(s)
        if m:
            rows.append('| %s | %s |' % (L_RANK, m.group(1)))
            i += 1
            continue
        m = TYPE_LINE.match(s)
        if m:
            rows.append('| %s | %s |' % (L_TYPE, esc(m.group(1).rstrip(',.'))))
            i += 1
            continue
        if s.endswith(':'):
            # Label ("Description:", "Features:", "1. Officer's Aura:"): pair
            # with following bullets/lines. 1 value -> label|value row;
            # several -> bold header + one bullet row each; none -> bold header.
            label = esc(s.rstrip(':'))
            vals = []
            j = i + 1
            while j < len(src):
                t = src[j]
                if t.endswith(':') or RANK_ONLY.match(t) or TYPE_LINE.match(t):
                    break
                if BULLET.match(t):
                    vals.append(re.sub(r'^-\s*', '', t).strip())
                    j += 1
                    continue
                if parse_kv(t) != (None, None):
                    break
                vals.append(t)
                j += 1
            if len(vals) == 1:
                rows.append('| %s | %s |' % (label, esc(vals[0])))
                i = j
            elif vals:
                rows.append('| **%s** | |' % label)
                rows.extend('| • %s | |' % esc(v) for v in vals)
                i = j
            else:
                rows.append('| **%s** | |' % label)
                i += 1
            continue
        if BULLET.match(s):
            rows.append('| • %s | |' % esc(re.sub(r'^-\s*', '', s)))
            i += 1
            continue
        k, v = parse_kv(s)
        if k is not None:
            rows.append('| %s | %s |' % (esc(k), esc(v)))
        else:
            rows.append('| %s | |' % esc(s))
        i += 1
    return '\n'.join([header, '|:---|:---|'] + rows)


def fmt(typ, paras):
    if typ in ('notif', 'choice'):
        return '\n'.join(['| %s |' % L_SYSTEM, '|:---|'] + ['| %s |' % esc(p) for p in paras])
    if typ == 'equip':
        return fmt_equip(paras)
    if typ == 'card':
        # Split "Skill Card – <name>" into label|value cells so the name gets
        # the full right column instead of squishing into the left one.
        m = CARD_SPLIT.match(paras[0])
        if m:
            header = '| %s | %s |' % (esc(m.group(1)), esc(m.group(2)))
        else:
            header = '| %s | |' % esc(paras[0])
        src = paras[1:]
    else:
        header = '| %s | |' % L_STATUS
        src = paras
    rows = []
    i = 0
    while i < len(src):
        s = src[i]
        if SECTION.match(s):
            rows.append('| **%s** | |' % esc(s.rstrip(':')))
            i += 1
            continue
        if s.endswith(':') and parse_kv(s) == (None, None):
            label = esc(s.rstrip(':'))
            vals = []
            j = i + 1
            while j < len(src):
                t = src[j]
                if SECTION.match(t) or (t.endswith(':') and parse_kv(t) == (None, None)):
                    break
                vals.append(re.sub(r'^-\s*', '', t))
                j += 1
                if SP_LINE.match(t.strip()) or (parse_kv(t) != (None, None) and label.lower() != DESC_WORD):
                    break
            if vals:
                rows.append('| %s | %s |' % (label, esc(' '.join(vals))))
                i = j
            else:
                rows.append('| **%s** | |' % label)
                i += 1
            continue
        k, v = parse_kv(s)
        if k is not None:
            rows.append('| %s | %s |' % (esc(k), esc(v)))
        else:
            rows.append('| %s | |' % esc(s))
        i += 1
    return '\n'.join([header, '|:---|:---|'] + rows)


def transform_chapter(lines):
    """Return (new_lines, counts dict). Idempotent — table elements (start with
    '|') never match a strong marker, so already-wrapped blocks are left alone."""
    out = []
    counts = {'notif': 0, 'card': 0, 'status': 0, 'equip': 0, 'choice': 0}
    i = 0
    n = len(lines)
    while i < n:
        p = lines[i]
        if p.strip() and is_block_start(lines, i):
            typ, paras, end = extend(lines, i)
            out.append(fmt(typ, paras))
            counts[typ] += 1
            i = end + 1
        else:
            out.append(p)
            i += 1
    return out, counts


def parse_range(spec):
    out = set()
    for part in (spec or '').split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-')
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def main():
    ap = argparse.ArgumentParser(description="Wrap System notifications/status/cards in Markdown tables.")
    ap.add_argument('--book-id', type=int, required=True)
    ap.add_argument('--chapters', default=None, help='e.g. "1-64" or "29,30,31". Default: all chapters.')
    ap.add_argument('--skip', default='', help='Chapters to skip (e.g. appendices): "28,64,101,130".')
    ap.add_argument('--source', action='store_true',
                    help='Transform the RUSSIAN source (chapters.untranslated_content '
                         'and pending queue.content) instead of the English translation.')
    ap.add_argument('--apply', action='store_true', help='Write changes (default: dry-run).')
    args = ap.parse_args()

    if args.source:
        use_source_profile()
    col = 'untranslated_content' if args.source else 'translated_content'

    chap_filter = parse_range(args.chapters) if args.chapters else None
    skip = parse_range(args.skip)

    backend = db_backend.create_backend()
    conn = backend.get_connection()
    cur = conn.cursor()

    tot = {'notif': 0, 'card': 0, 'status': 0, 'equip': 0, 'choice': 0}
    changed = {'chapters': 0, 'queue': 0}

    targets = [('chapters', col, "SELECT id, chapter_number, %s FROM chapters WHERE book_id=? ORDER BY chapter_number" % col)]
    if args.source:
        targets.append(('queue', 'content', "SELECT id, chapter_number, content FROM queue WHERE book_id=? ORDER BY chapter_number"))

    for table, column, select_sql in targets:
        cur.execute(select_sql, [args.book_id])
        rows = cur.fetchall()
        for row_id, num, raw in rows:
            if chap_filter is not None and num not in chap_filter:
                continue
            if num in skip:
                continue
            try:
                lines = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            new, counts = transform_chapter(lines)
            if any(counts.values()) and new != lines:
                changed[table] += 1
                for k in tot:
                    tot[k] += counts[k]
                if args.apply:
                    cur.execute("UPDATE %s SET %s=? WHERE id=?" % (table, column),
                                (json.dumps(new, ensure_ascii=False), row_id))
    if args.apply:
        conn.commit()
    mode = 'APPLIED' if args.apply else 'DRY-RUN'
    side = 'SOURCE' if args.source else 'translation'
    print("[%s] book %d (%s): %d chapters + %d queue rows changed | tables: notif=%d card=%d status=%d equip=%d choice=%d (total %d)"
          % (mode, args.book_id, side, changed['chapters'], changed['queue'],
             tot['notif'], tot['card'], tot['status'], tot['equip'], tot['choice'], sum(tot.values())))
    if args.apply and changed['chapters'] and not args.source:
        try:
            from config import TranslationConfig
            import os
            cache = os.path.join(TranslationConfig().script_dir, 'epub_cache', f'{args.book_id}.epub')
            if os.path.exists(cache):
                os.remove(cache)
                print("Invalidated", cache)
        except Exception:
            pass


if __name__ == '__main__':
    main()
