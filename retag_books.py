#!/usr/bin/env python3
"""Propose NovelUpdates-compliant tags for every book with Sonnet.

For each book the model reads the description, the BOOK-SPECIFIC NOTES of its
prompt and the current tags, and may open chapters or chapter summaries through
two local tools (the API only asks for a call; this script runs it against the
DB). It answers with NU genres, NU tags, and the few house tags worth keeping.
Read-only: proposals go to a JSON file that apply_book_tags.py applies.

    python3 retag_books.py --out tag_proposals.json [--book 90 ...] [--workers 6]
    python3 apply_book_tags.py --file tag_proposals.json [--apply]

Re-running with the same --out skips books already proposed (--redo to force).
"""
import argparse
import json
import os
import sys
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

import nu_sync
from apply_book_tags import VOCAB_PATH
from backfill_source_titles import anthropic_client
from config import TranslationConfig
from database import DatabaseManager
from footnote_scan_core import extract_book_notes
from get_chapter_summaries import fetch_summaries
from logger import Logger

MODEL = "claude-sonnet-5-5"
MAX_TURNS = 10
READ_WORDS = 6000
SUMMARY_SPAN = 300
DISCUSSION_CH = 0

TOOLS = [
    {
        "name": "chapter_summaries",
        "description": ("The stored one-paragraph summaries of chapters first..last (inclusive, at most "
                        f"{SUMMARY_SPAN} chapters per call). Cheap; the fastest way to see where a story goes."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"first": {"type": "integer"}, "last": {"type": "integer"}},
            "required": ["first", "last"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_chapter",
        "description": (f"The translated English text of one chapter (first {READ_WORDS} words). Use when a "
                        "summary can't settle a question — the protagonist's gender, how explicit the content is, "
                        "whether the romance is real."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"chapter": {"type": "integer"}},
            "required": ["chapter"],
            "additionalProperties": False,
        },
    },
]

SCHEMA = {
    "type": "object",
    # rationale first: fields are written in this order, and lists written before
    # the reasoning came back contradicting it ("Romance is unsupported" beside Romance).
    "properties": {
        "rationale": {"type": "string"},
        "genres": {"type": "array", "items": {"type": "string"}},
        "nu_tags": {"type": "array", "items": {"type": "string"}},
        "house_tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rationale", "genres", "nu_tags", "house_tags"],
    "additionalProperties": False,
}


def system_prompt(nu_genres, nu_tags, house):
    return f"""You tag web novels (mostly Chinese, some Korean/Japanese/Russian, translated to English) for a reading site whose tags should line up with NovelUpdates (NU), so a book can be listed there with them.

For the book you are given, first write the rationale, then choose:

0. rationale — two or three sentences, written before the lists: what the book is, and what you are removing from the current tags and why. The lists must then agree with it.
1. genres — 2 to 6 NU genres that genuinely describe the book. Exact names from the NU GENRES list.
2. nu_tags — the 6 to 12 NU tags a reader would most want to know: the premise, the protagonist, the setting, the power system, the romance situation, anything distinctive. Prefer what the story is about over what the protagonist is like: at most two personality tags ("Cautious Protagonist", "Shameless Protagonist"), and only when the personality defines the book. Exact names from the NU TAGS list. A tag must be central or recurring, not a one-chapter incident. Include "Male Protagonist" or "Female Protagonist" whenever it can be determined. Where the book's current tags say something NU has no exact name for, apply the closest NU tag as well.
3. house_tags — 0 to 6 of the site's own tags that say something no NU name you chose says. Prefer the HOUSE TAGS list. You may keep an existing tag of the book that is not on that list only if it is genuinely descriptive and specific (a fandom name on a fan-fiction book — "harry potter", "naruto" — always is). Never put an NU genre or tag name here, and never a near-synonym of one you already chose ("op protagonist" next to "Overpowered Protagonist").

Rules:
- The current tags are a starting point, not truth; earlier passes made mistakes. Drop what the book doesn't support.
- Origin language is shown separately on the site and is never a tag.
- Use the NU genres with care: "Mature" / "Adult" / "Smut" / "Ecchi" only when the content is actually that; "Seinen"/"Shounen"/"Josei"/"Shoujo" only when the demographic is clear (NU lists most male-lead web novels without them — leave them out when unsure). "Xianxia", "Xuanhuan" and "Wuxia" are distinct: immortal cultivation toward transcendence; Chinese-flavoured fantasy power systems that aren't classic immortal-seeking; martial heroes without the supernatural ascent.
- "Harem" (genre) needs several love interests the story pursues; one love interest is "Romance" or the tag "Romantic Subplot". A book with no romance at all gets the house tag "no romance".
- Tools: chapter_summaries and read_chapter read this book's own stored text. Use them when the description and notes leave a tagging question open (who the protagonist is, whether there is a system, a romance, a time skip into a different setting). A summary sweep of the first chapters and a sample further in is usually enough; don't read for its own sake.

NU GENRES:
{", ".join(nu_genres)}

NU TAGS:
{", ".join(nu_tags)}

HOUSE TAGS (the site's own filter vocabulary; names already identical to an NU name are omitted):
{", ".join(house)}
"""


def dossier(book, notes, chapters):
    rng = f"chapters {chapters[0]}–{chapters[-1]} ({len(chapters)} stored)" if chapters else "no chapters stored"
    parts = [
        f"BOOK {book['id']}: {book['title']}",
        f"Original title: {book.get('source_title') or '—'}" + (f" · NU title: {book['nu_title']}" if book.get("nu_title") else ""),
        f"Author: {book.get('author') or '—'} · Source language: {book.get('source_language') or '—'} · "
        f"Status: {book.get('status') or '—'} · {rng}",
        f"Current tags: {', '.join(t for t in book.get('tags') or [] if 'choice' not in t.lower()) or '(none)'}",
        "",
        "DESCRIPTION:",
        (book.get("description") or "(none)").strip(),
        "",
        "BOOK-SPECIFIC NOTES (the translator's conventions for this book; they reveal characters, setting and terminology):",
        notes or "(none)",
    ]
    return "\n".join(parts)


class Tagger:
    def __init__(self, db, client, model, nu_genres, nu_tags, house):
        self.db, self.client, self.model = db, client, model
        self.genre_by_lower = {g.lower(): g for g in nu_genres}
        self.tag_by_lower = {t.lower(): t for t in nu_tags}
        self.system = system_prompt(nu_genres, nu_tags, house)
        self.db_lock = threading.Lock()

    def _tool(self, book_id, chapters, name, args):
        with self.db_lock:
            if name == "chapter_summaries":
                first, last = int(args["first"]), int(args["last"])
                last = min(last, first + SUMMARY_SPAN - 1)
                rows = fetch_summaries(self.db, book_id, lambda n: first <= n <= last and n > DISCUSSION_CH)
                if not rows:
                    return f"No chapters in {first}–{last}. Stored: {chapters[0]}–{chapters[-1]}." if chapters else "No chapters."
                return "\n\n".join(f"ch{r['chapter']}: {r['title'] or ''}\n{r['summary'] or '(no summary)'}" for r in rows)
            if name == "read_chapter":
                n = int(args["chapter"])
                ch = self.db.get_chapter(book_id=book_id, chapter_number=n)
                if not ch:
                    return f"No chapter {n}."
                words, out = 0, []
                for line in ch.get("content") or []:
                    if isinstance(line, str) and line.strip():
                        out.append(line.strip())
                        words += len(line.split())
                        if words >= READ_WORDS:
                            out.append("[…truncated]")
                            break
                if not out:
                    return f"Chapter {n} has no translated text yet."
                return f"ch{n}: {ch.get('title') or ''}\n" + "\n".join(out)
        return f"Unknown tool {name}"

    def classify(self, book):
        with self.db_lock:
            notes = extract_book_notes(self.db.get_book_prompt_template(book["id"]) or "")
            chapters = sorted(c["chapter"] for c in self.db.list_chapters(book["id"])
                              if c["chapter"] > DISCUSSION_CH)
        messages = [{"role": "user", "content": dossier(book, notes, chapters)}]
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        calls = []
        for _ in range(MAX_TURNS):
            resp = self.client.messages.create(
                model=self.model, max_tokens=16000,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                messages=messages, tools=TOOLS,
                cache_control={"type": "ephemeral"},
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
            u = resp.usage
            usage["input"] += u.input_tokens
            usage["output"] += u.output_tokens
            usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
            usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
            if resp.stop_reason == "refusal":
                raise RuntimeError(f"refused ({getattr(resp.stop_details, 'category', None)})")
            if resp.stop_reason != "tool_use":
                if resp.stop_reason == "max_tokens":
                    raise RuntimeError("answer cut off at max_tokens")
                text = "".join(b.text for b in resp.content if b.type == "text")
                return self._finish(book, json.loads(text), usage, calls)
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    calls.append(f"{b.name}({json.dumps(b.input)})")
                    try:
                        out, err = self._tool(book["id"], chapters, b.name, b.input), False
                    except Exception as e:
                        out, err = f"Error: {e}", True
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})
        raise RuntimeError(f"no answer after {MAX_TURNS} turns")

    def _finish(self, book, ans, usage, calls):
        genres, nu_tags, invalid = [], [], []
        for g in ans["genres"]:
            name = self.genre_by_lower.get(g.strip().lower()) or self.tag_by_lower.get(g.strip().lower())
            (genres if name else invalid).append(name or g)
        for t in ans["nu_tags"]:
            name = self.tag_by_lower.get(t.strip().lower()) or self.genre_by_lower.get(t.strip().lower())
            (nu_tags if name else invalid).append(name or t)
        house = [h.strip().lower() for h in ans["house_tags"] if h.strip()]
        nu_lower = set(self.genre_by_lower) | set(self.tag_by_lower)
        house = [h for h in house if h not in nu_lower]
        tags, seen = [], set()
        for t in genres + nu_tags + house:
            if t.lower() not in seen:
                seen.add(t.lower())
                tags.append(t.lower())
        return {
            "title": book["title"],
            "current": book.get("tags") or [],
            "tags": tags,
            "keep": house,
            "genres": genres,
            "nu_tags": nu_tags,
            "invalid": invalid,
            "rationale": ans["rationale"],
            "tool_calls": calls,
            "usage": usage,
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="proposals JSON (read first; existing entries are skipped)")
    ap.add_argument("--book", type=int, nargs="*", help="only these book ids (default: every book)")
    ap.add_argument("--public-only", action="store_true")
    ap.add_argument("--redo", action="store_true", help="re-propose books already in --out")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()

    nu = nu_sync.load_vocab()
    if not nu:
        sys.exit("No NU vocabulary cached (logs/nu_vocabulary.json) — refresh it from the NovelUpdates page first.")
    nu_genres, nu_tags = nu_sync._names(nu, "genres"), nu_sync._names(nu, "tags")
    nu_lower = {n.lower() for n in nu_genres + nu_tags}
    with open(VOCAB_PATH, encoding="utf-8") as f:
        house = [t for axis in json.load(f)["axes"].values() for t in axis
                 if t not in nu_lower and t != "editor's choice"]

    config = TranslationConfig()
    db = DatabaseManager(config, Logger(config))
    books = [b for b in db.list_books()
             if (not args.book or b["id"] in args.book) and (b.get("is_public") or not args.public_only)]

    proposals = {}
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            proposals = json.load(f)
    todo = [b for b in books if args.redo or str(b["id"]) not in proposals]
    print(f"{len(todo)} book(s) to tag ({len(books) - len(todo)} already in {args.out})", file=sys.stderr)

    tagger = Tagger(db, anthropic_client(), args.model, nu_genres, nu_tags, house)
    save_lock = threading.Lock()
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(tagger.classify, b): b for b in todo}
        for fut in as_completed(futures):
            b = futures[fut]
            try:
                prop = fut.result()
            except Exception as e:
                failed += 1
                print(f"[{b['id']}] FAILED: {e}", file=sys.stderr)
                continue
            with save_lock:
                proposals[str(b["id"])] = prop
                tmp = args.out + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(dict(sorted(proposals.items(), key=lambda kv: int(kv[0]))), f,
                              indent=1, ensure_ascii=False)
                os.replace(tmp, args.out)
            flag = f"  INVALID: {prop['invalid']}" if prop["invalid"] else ""
            print(f"[{b['id']}] {b['title'][:50]} — {len(prop['tool_calls'])} lookups — "
                  f"{', '.join(prop['tags'])}{flag}", file=sys.stderr)
    print(f"done; {failed} failed", file=sys.stderr)


if __name__ == "__main__":
    main()
