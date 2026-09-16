#!/usr/bin/env python3
"""Compare our entity renderings against an independent control translation.

The control is a *different* human/AI translation of the same novel, held as JSON.
This does NOT read whole chapters: it asks one mechanical question per entity —
does our English rendering also appear in the control's text for the chapters
where that entity actually occurs?

    python3 control_check.py -b 98 --control mirrorlegacy.json --chapters 1-30

Which entities are "in" a chapter comes from the `chapter_entities` index
(migration 19), i.e. entities whose untranslated form is literally present in
that chapter's SOURCE — not `origin_chapter`, which only records extraction time.

⚠️ The load-bearing filter is that an entity is only flagged when OUR chapter
contains our rendering but the control chapter does not. If our own prose never
uses the rendering, the control's silence says nothing and the row is noise.
Absence alone is not disagreement.

Flagged rows carry a guess at the control's counterpart, found by fuzzy-matching
our rendering against capitalised tokens in the control chapter, with pinyin as a
fallback for the case where one side translated what the other transliterated.

⚠️ A divergence is NOT a verdict. The control is a second opinion from a different
translator, not ground truth, and it can simply be wrong. In book 98 it renders 長蟲
("long worm", the rural euphemism) as "worm" 23 times in ch26 — where the source
gives the creature vertical pupils, a forked tongue, scales, and twice says 蛇身/蛇頭
outright. It read the characters, not the referent. Treat every flag as "go look",
never as "we are wrong".

Exit status is 0 always; this is a reporting tool.
"""

import argparse
import difflib
import json
import os
import re
import sys
import warnings
from collections import defaultdict

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from config import TranslationConfig
from db import DatabaseManager
from logger import Logger

TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,2}")
CJK_RE = re.compile(r"[一-鿿]")


def parse_range(spec):
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def load_control(path, wanted):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    for ch in data.get("chapters") or []:
        try:
            n = int(str(ch.get("number")).strip())
        except (TypeError, ValueError):
            continue
        if wanted and n not in wanted:
            continue
        out[n] = ch.get("text") or ""
    return out, data


PUNCT_MAP = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
                           "\u2013": "-", "\u2014": "-", "\u00a0": " "})


def norm(text):
    """Fold the punctuation the two translations spell differently.

    Without this, "Jing'er" and "Jing\u2019er" read as a divergence — a curly
    apostrophe is not a translation decision.
    """
    return text.translate(PUNCT_MAP).lower()


def contains(hay_lower, needle):
    """Case-insensitive substring, tolerating a trailing plural on the needle."""
    n = norm(needle).strip()
    if not n:
        return False
    if n in hay_lower:
        return True
    if n.endswith("s") and n[:-1] in hay_lower:
        return True
    return f"{n}s" in hay_lower


def guess_counterpart(ours, ctrl_text, zh):
    """Best guess at what the control calls this, or '' if nothing plausible."""
    cands = {m.group(0) for m in TOKEN_RE.finditer(ctrl_text)}
    if not cands:
        return ""
    close = difflib.get_close_matches(ours, list(cands), n=1, cutoff=0.62)
    if close:
        return close[0]
    # Pinyin fallback, for when one side translated what the other transliterated.
    # Demand at least two syllables AND that they cover most of the candidate —
    # a single weak syllable matches anything (月華 "yuehua" hits "Yue State").
    try:
        from pypinyin import lazy_pinyin
        sylls = [x for x in lazy_pinyin(zh) if len(x) >= 2]
    except Exception:
        sylls = []
    if len(sylls) < 2:
        return ""
    best, best_score = "", 0.0
    for c in cands:
        low = re.sub(r"[^a-z]", "", c.lower())
        if not low:
            continue
        matched = [x for x in sylls if x in low]
        if len(matched) < 2:
            continue
        score = sum(len(x) for x in matched) / len(low)
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.6 else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--book-id", type=int, required=True)
    ap.add_argument("--control", default="mirrorlegacy.json")
    ap.add_argument("--chapters", required=True, help="e.g. 1-30 or 5,7,9")
    ap.add_argument("--min-len", type=int, default=3,
                    help="skip renderings shorter than this (default 3)")
    ap.add_argument("--category", action="append",
                    help="limit to a category (repeatable)")
    ap.add_argument("--align-window", type=int, default=20,
                    help="how far ahead/behind to search for each chapter's true counterpart "
                         "in the control (default 20; 0 disables auto-alignment)")
    ap.add_argument("--split-threshold", type=int, default=4300,
                    help="source chapters at or above this many CJK characters were split "
                         "into two by the control's site, so they consume TWO control "
                         "chapters (book 98: calibrated at 4300, a sharp optimum). 0 = off.")
    ap.add_argument("--span", type=int, default=0,
                    help="how many control chapters either side of the aligned one to test "
                         "against (default 1 = a 3-chapter window). The control's source "
                         "splits some original chapters in two, so one of our chapters can "
                         "correspond to two of theirs; matching against a single chapter "
                         "would report the other half's terms as divergences. Default 0: "
                         "prefer --split-threshold, which widens only where a split actually "
                         "happened instead of blanketing every chapter.")
    ap.add_argument("--min-agreement", type=float, default=0.35,
                    help="chapters agreeing below this are treated as unreliable and "
                         "produce no flags (default 0.35)")
    ap.add_argument("--all", action="store_true",
                    help="also list agreed and unverifiable entities")
    ap.add_argument("--json", dest="json_out", help="write findings to a JSON file")
    args = ap.parse_args()

    wanted = parse_range(args.chapters)
    ctrl, meta = load_control(args.control, wanted)
    if not ctrl:
        print(f"error: no control chapters in range {args.chapters}", file=sys.stderr)
        return 2

    cfg = TranslationConfig()
    db = DatabaseManager(cfg, Logger(cfg))

    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT ch.chapter_number AS n, e.untranslated AS zh, e.translation AS en, "
        "       e.category AS cat "
        "FROM chapter_entities ce "
        "JOIN chapters ch ON ch.id = ce.chapter_id "
        "JOIN entities  e ON e.id = ce.entity_id "
        "WHERE ch.book_id = ?", (args.book_id,))
    rows = cur.fetchall()
    conn.close()

    def g(r, k, i):
        return r[k] if isinstance(r, dict) else r[i]

    per_chapter = defaultdict(list)
    for r in rows:
        n = g(r, "n", 0)
        if n in wanted:
            per_chapter[n].append((g(r, "zh", 1), g(r, "en", 2), g(r, "cat", 3)))

    ours_en, src_len = {}, {}
    for n in sorted(per_chapter):
        c = db.get_chapter(book_id=args.book_id, chapter_number=n)
        if not c:
            continue
        ours_en[n] = "\n".join(
            l for l in (c.get("content") or []) if not l.strip().startswith("["))
        src_len[n] = len("".join(c.get("untranslated") or []))

    # ── auto-alignment ───────────────────────────────────────────────────────
    # The control is a different site's numbering and drifts against ours as the
    # two sources split or merge chapters differently. On book 98 it runs 1:1 to
    # about ch100 and then pulls ~7 ahead. Comparing ch-for-ch past that point
    # compares unrelated chapters and reports the whole range as "diverged",
    # which is how a real 69%-agreement chapter looked like a 19% one.
    def score(n, cn):
        if cn not in ctrl or n not in ours_en:
            return (0, 0)
        ours_low, ctrl_low = norm(ours_en[n]), norm(ctrl[cn])
        hit = tot = 0
        for zh, en, cat in per_chapter[n]:
            if not en or len(en) < max(args.min_len, 4) or CJK_RE.search(en):
                continue
            if contains(ours_low, en):
                tot += 1
                if contains(ctrl_low, en):
                    hit += 1
        return (hit, tot)

    aligned, offsets = {}, {}
    drift = 0
    for n in sorted(ours_en):
        if args.align_window <= 0:
            aligned[n] = n
            continue
        best, best_rate = None, -1.0
        # Search BOTH directions. The offset is not monotonic: the control's site
        # splits chapters (pushing its numbering ahead of ours) but the capture is
        # also partial — 228 of 1164 — so gaps pull it back. Constraining the search
        # to non-decreasing offsets was tried and measurably hurt: book 98 ch96-97
        # genuinely aligns at -1 and scores 42-52% there, against 27-30% when forced
        # forward.
        lo, hi = drift - args.align_window, drift + args.align_window
        for off in range(lo, hi + 1):
            hit, tot = score(n, n + off)
            if tot >= 8:
                r = hit / tot
                # prefer the smaller shift when rates tie
                if r > best_rate + 1e-9 or (abs(r - best_rate) < 1e-9 and best is not None
                                            and abs(off) < abs(best - n)):
                    best, best_rate = n + off, r
        if best is not None and best_rate > 0:
            aligned[n], drift = best, best - n
        else:
            aligned[n] = n + drift
        offsets[n] = aligned[n] - n

    agreed, flagged, unverifiable = {}, defaultdict(list), set()
    chapter_stats = {}

    for n in sorted(ours_en):
        cn = aligned.get(n, n)
        if cn not in ctrl:
            continue
        # Alignment picks the single best-matching control chapter (a sharp signal);
        # the membership test then widens to a span, because the control's source
        # splits some chapters in two. A false divergence is the expensive error
        # here — a false agreement only costs us a miss on a second opinion.
        # How many control chapters did this one of ours become? Their site splits a
        # source chapter in two once it passes a length threshold, and leaves no
        # metadata trace (titles are bare "Chapter N", one sourceUrl each) — so the
        # only reliable signal is OUR source length, which we already have.
        extra = 1 if (args.split_threshold and src_len.get(n, 0) >= args.split_threshold) else 0
        lo, hi = cn - args.span, cn + args.span + extra
        window = [ctrl[c] for c in range(lo, hi + 1) if c in ctrl]
        ours_low = norm(ours_en[n])
        ctrl_low = norm("\n".join(window))
        hit = tot = 0
        for zh, en, cat in per_chapter[n]:
            if not en or len(en) < args.min_len or CJK_RE.search(en):
                continue
            if args.category and cat not in args.category:
                continue
            in_ours = contains(ours_low, en)
            in_ctrl = contains(ctrl_low, en)
            if in_ours:
                tot += 1
                if in_ctrl:
                    hit += 1
            if in_ctrl:
                agreed[(zh, en)] = cat
            elif in_ours:
                flagged[(zh, en, cat)].append(n)
            else:
                unverifiable.add((zh, en))
        chapter_stats[n] = (hit, tot)

    # A chapter whose agreement has collapsed cannot be told apart from a chapter
    # that is simply misaligned with the control (merged/split/offset numbering).
    # Its "divergences" would be an artefact, so drop them rather than report noise.
    unreliable = {n for n, (hit, tot) in chapter_stats.items()
                  if tot >= 8 and hit / tot < args.min_agreement}
    if unreliable:
        for key in list(flagged):
            kept = [n for n in flagged[key] if n not in unreliable]
            if kept:
                flagged[key] = kept
            else:
                del flagged[key]

    # an entity that agrees anywhere in the range is agreed, full stop
    flagged = {k: v for k, v in flagged.items() if (k[0], k[1]) not in agreed}

    print(f"Control: {args.control} — {meta.get('title')!r}, "
          f"{len(ctrl)} chapters in range, source {meta.get('source')}")
    print(f"Book {args.book_id}, chapters {args.chapters}\n")

    print("Per-chapter agreement (sudden collapse = chapter misalignment):")
    for n in sorted(chapter_stats):
        hit, tot = chapter_stats[n]
        pct = f"{100*hit//tot:3d}%" if tot else "  — "
        off = offsets.get(n, 0)
        sp = "*" if (args.split_threshold and src_len.get(n, 0) >= args.split_threshold) else " "
        pct = f"{pct}{sp} {('(ctrl %+d)' % off) if off else '         '}"
        bar = "" if not tot else ("  <-- CHECK ALIGNMENT" if tot >= 5 and hit / tot < 0.34 else "")
        print(f"  ch{n:<4d} {pct}  ({hit}/{tot}){bar}")

    if unreliable:
        print(f"\n⚠️  {len(unreliable)} chapter(s) below {args.min_agreement:.0%} agreement are "
              f"treated as UNRELIABLE and produce no flags:\n    {sorted(unreliable)}")
        print("    Either the terminology genuinely diverged there, or the control's chapter\n"
              "    numbering is out of step with ours. Both look identical from here.")

    print(f"\nAGREED (control uses our rendering): {len(agreed)}")
    print(f"UNVERIFIABLE (neither text uses it): {len(unverifiable)}")
    print(f"FLAGGED (ours uses it, control does not): {len(flagged)}\n")

    # A name divergence is a different animal from a wording divergence: for a
    # name the control almost certainly has a counterpart token we can point at,
    # while for a lowercase common noun it simply worded the idea differently and
    # no token-level guess will help. Report them apart and names first.
    def is_name(en):
        return bool(re.match(r"[A-Z《]", en.strip()))

    names = {k: v for k, v in flagged.items() if is_name(k[1])}
    wording = {k: v for k, v in flagged.items() if not is_name(k[1])}

    def dump(title, group, guess=True):
        if not group:
            return
        print(f"--- {title} ({len(group)}) ---")
        for (zh, en, cat), chs in sorted(group.items(), key=lambda kv: -len(kv[1])):
            gtxt = ""
            if guess:
                blob = "\n".join(ctrl[c] for n in chs
                             for c in range(aligned.get(n, n) - args.span,
                                            aligned.get(n, n) + args.span + 1) if c in ctrl)
                g = guess_counterpart(en, blob, zh)
                gtxt = f"   control may say: {g!r}" if g else ""
            print(f"  {zh}  [{cat}] -> {en!r}   ours ch{chs[:6]}{gtxt}")
        print()

    if flagged:
        print("=" * 72)
        print("A divergence means the two translations differ — not that ours is wrong.\n"
              "Read the source before changing anything.\n")
        dump("NAME divergences — worth a look", names, guess=True)
        dump("WORDING divergences — lower priority, control just phrased it otherwise",
             wording, guess=False)

    if args.all:
        print("\n--- AGREED ---")
        for (zh, en), cat in sorted(agreed.items()):
            print(f"  {zh} [{cat}] -> {en!r}")
        print("\n--- UNVERIFIABLE ---")
        for zh, en in sorted(unverifiable):
            print(f"  {zh} -> {en!r}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({
                "book_id": args.book_id, "chapters": args.chapters,
                "agreed": [{"zh": z, "en": e, "category": c} for (z, e), c in agreed.items()],
                "flagged": [{"zh": z, "en": e, "category": c, "chapters": v}
                            for (z, e, c), v in flagged.items()],
                "unverifiable": [{"zh": z, "en": e} for z, e in sorted(unverifiable)],
            }, fh, ensure_ascii=False, indent=1)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
