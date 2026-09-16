#!/usr/bin/env python3
"""Find entity translations that transliterate their source, and report which
already carry a footnote.

Stronger than _pinyin_audit.py, which asks "does this English token look like
pinyin?" via a syllable-chain decomposition requiring >=2 syllables. That test is
structurally blind to SINGLE-morpheme transliterations (zhan, Hou, gu, xi, yi) --
exactly the most opaque class -- and it false-flags English that happens to
decompose ("teahouse" = te+a+hou+se).

This asks instead: is the English token the actual romanization of the source
characters? Exact rather than heuristic. It also cross-references the book's
footnotes, so the output separates "transliterated" from "transliterated and
still unglossed".

Needs python3-pypinyin (apt).

Usage:  python3 transliteration_audit.py <book_id> [--places]
          --places also reports toponyms, excluded by default as proper names.
        Lines prefixed ">>" have no footnote yet; "characters" is always skipped.
"""
import sys, re, os
sys.path.insert(0, "/home/mdm/t9")
os.environ.pop("DEBUG", None)
from pypinyin import lazy_pinyin
from config import TranslationConfig
from logger import Logger
from db import DatabaseManager

BOOK = int(sys.argv[1]) if len(sys.argv) > 1 else 90
SHOW_PLACES = "--places" in sys.argv

NATURAL = {"tao","dao","qi","yin","yang","kung","fu","gong","wushu","wuxia","xianxia",
           "tai","chi","feng","shui","mahjong","tofu","wok","ginseng","typhoon","kowtow",
           "sifu","shifu"}
UNITS = {"li","cun","zhang","chi","dou","jin","mu","qing","ke","shichen","zhu","dan","shi","liang","zhai"}
NAME_CATS = {"characters"}
PLACE_CATS = {"places"}

cfg = TranslationConfig(); db = DatabaseManager(cfg, Logger(cfg))
with db._conn() as conn:
    cur = conn.cursor()
    cur.execute("SELECT category, untranslated, translation, origin_chapter, note FROM entities WHERE book_id=%s", (BOOK,))
    rows = cur.fetchall()
    cur.execute("SELECT anchor, source_term, body FROM footnotes WHERE book_id=%s", (BOOK,))
    fns = cur.fetchall()

fn_bodies = [((a or "")+" "+(s or "")+" "+(b or "")) for a,s,b in fns]
fn_blob = "\n".join(fn_bodies)

TOK = re.compile(r"[A-Za-z]+")
def han(s): return [c for c in (s or "") if '一' <= c <= '鿿']

def translit_hits(src, tr):
    h = han(src)
    if not h: return []
    syls = lazy_pinyin("".join(h))
    runs = {"".join(syls[i:j]) for i in range(len(syls)) for j in range(i+1, len(syls)+1)}
    return [(w, w.lower()) for w in TOK.findall(tr or "")
            if w.lower() in runs and w.lower() not in NATURAL]

def glossed(unt, words):
    if unt and unt in fn_blob: return True
    for w in words:
        if re.search(r'\b'+re.escape(w)+r'\b', fn_blob, re.I): return True
    return False

out = []
for cat, unt, tr, orig, note in rows:
    if cat in NAME_CATS: continue
    if cat in PLACE_CATS and not SHOW_PLACES: continue
    hits = translit_hits(unt, tr)
    if not hits: continue
    words = sorted({h[0] for h in hits})
    lw = {w.lower() for w in words}
    if lw <= UNITS: continue                      # unit conversions, rejected as a class
    toks = TOK.findall(tr or "")
    cov = sum(1 for w in toks if w.lower() in lw)/max(len(toks),1)
    if cov < 0.34 and len(han(unt)) > 2: continue  # a name element inside a longer phrase
    out.append((cat, orig or 0, unt, tr, words, cov, glossed(unt, words), note or ""))

out.sort(key=lambda r: (r[0], r[1]))
ung = [o for o in out if not o[6]]
print(f"# book {BOOK}: {len(out)} concept-transliterations, {len(ung)} with NO footnote\n")
cc=None
for cat, orig, unt, tr, words, cov, g, note in out:
    if cat!=cc:
        n=sum(1 for o in out if o[0]==cat); u=sum(1 for o in ung if o[0]==cat)
        print(f"\n== {cat}  ({n}, {u} unglossed) =="); cc=cat
    print(f" {'   ' if g else '>> '}ch{orig:<5} {unt} : {tr}   cov={cov:.0%}")
