#!/usr/bin/env python3
"""Fill books.source_title (the novel's title in its source language) for the
existing library without looking each one up by hand.

Two sources, in order:

1. **The book's source_url.** The scraper fetches the page (`POST /page`,
   rendered HTML, priority low so it queues behind the scrape worker's jobs).
   A trimmed extract — <title>, the meta tags, the <h1>/<h2> text — goes to
   Haiku, which names the novel and says whether it plausibly is the book
   we hold. `og:novel:book_name`, which most Chinese novel sites carry, wins
   over Haiku's reading when both exist; a disagreement is flagged.
2. **The cover**, for books with no URL or a page that failed: Haiku reads the
   source-language title off the medium cover derivative. Calligraphic covers
   can be misread, so these rows are marked for a closer look.

Chinese-source titles are always run through OpenCC t2s (trad_simp), whatever
the book's own trad_to_simp setting — Taiwanese mirrors (twkan, novel543, the
.tw sites) serve traditional glyphs, and t2s leaves simplified text unchanged.

Dry run by default: writes a proposals JSON and prints a review table.
Applying is a separate step, from the file, after review:

    python3 backfill_source_titles.py                       # all books missing one
    python3 backfill_source_titles.py -b 4 17 79            # just these
    python3 backfill_source_titles.py --apply /tmp/t9_source_titles/proposals.json

Edit the JSON before applying to fix or drop a row (set "title" to null to skip
it). Fetched HTML is cached in --cache-dir, so a rerun doesn't re-scrape.
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from scraper_client import FINAL, ScraperClient, ScraperError

HAIKU = "claude-haiku-5-5"
DEFAULT_DIR = "/tmp/t9_source_titles"
LANG_NAMES = {"zh": "Chinese", "ko": "Korean", "kr": "Korean", "ja": "Japanese",
              "jp": "Japanese", "ru": "Russian", "en": "English"}


# ---------------------------------------------------------------------------
# Scraper: single-page fetch
# ---------------------------------------------------------------------------

class PageClient(ScraperClient):
    """ScraperClient plus the /page job, whose result is raw HTML, not JSON."""

    def submit_page(self, url, wait_ms=0, priority="low"):
        return self._req("POST", "/page", {"url": url, "format": "html",
                                           "waitMs": wait_ms, "priority": priority})[1]

    def page_html(self, job_id):
        """(html, final_url) of a finished page job."""
        req = urllib.request.Request(f"{self.base_url}/jobs/{job_id}/result",
                                     headers={"Authorization": f"Bearer {self.token}"} if self.token else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                raw = res.read()
                charset = res.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace"), res.headers.get("X-Final-Url")
        except urllib.error.HTTPError as e:
            raise ScraperError(f"GET /jobs/{job_id}/result: HTTP {e.code}") from None

    def fetch(self, url, timeout=900, poll=5):
        job = self.submit_page(url)
        start = time.monotonic()
        while job["status"] not in FINAL:
            if time.monotonic() - start > timeout:
                raise ScraperError(f"page job {job['id']} timed out ({job['status']})")
            time.sleep(poll)
            job = self.status(job["id"])
        if job["status"] != "done":
            raise ScraperError(f"page job {job['id']} {job['status']}: {job.get('error', '')}")
        return self.page_html(job["id"])


def cached_html(client, book, cache_dir, refetch=False):
    path = os.path.join(cache_dir, "html", f"{book['id']}.html")
    if not refetch and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    html, _final = client.fetch(book["source_url"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return html


# ---------------------------------------------------------------------------
# HTML → compact extract
# ---------------------------------------------------------------------------

_META_KEYS = ("og:novel:book_name", "og:title", "og:novel:author", "og:description",
              "keywords", "description", "title")


def page_extract(html):
    """The parts of a book page that name the book, ~a few hundred tokens."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    ex = {"title": (soup.title.get_text(" ", strip=True) if soup.title else "")[:200], "meta": {}}
    for m in soup.find_all("meta"):
        key = (m.get("property") or m.get("name") or "").strip().lower()
        if key in _META_KEYS and m.get("content"):
            ex["meta"][key] = m["content"].strip()[:300]
    heads = []
    for tag in soup.find_all(["h1", "h2"]):
        t = tag.get_text(" ", strip=True)
        if t and t not in heads:
            heads.append(t[:120])
        if len(heads) >= 6:
            break
    ex["headings"] = heads
    return ex


def clean_title(t):
    t = (t or "").strip().strip("《》「」『』\"'").strip()
    return t or None


# ---------------------------------------------------------------------------
# Haiku
# ---------------------------------------------------------------------------

def anthropic_client():
    import anthropic
    key = os.environ.get("ANTHROPIC_KEY")
    if not key:
        try:
            from dotenv import dotenv_values
            key = dotenv_values(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")).get("ANTHROPIC_KEY")
        except Exception:
            pass
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def _ask(client, content):
    resp = client.messages.create(
        model=HAIKU, max_tokens=1024, output_config={"effort": "low"},
        messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in reply: {text[:200]!r}")
    return json.loads(m.group(0))


def haiku_from_page(client, book, extract):
    lang = LANG_NAMES.get((book.get("source_language") or "zh").lower(), "source-language")
    prompt = (
        f"This is an extract of a web-novel site's book page. Name the novel it is about, "
        f"in {lang}, exactly as the page writes it: the bare title only — no author, site name, "
        f"\"latest chapters\"/\"read online\" suffixes, or brackets. Do not translate it.\n\n"
        f"We hold this novel as \"{book['title']}\""
        + (f" by {book['author']}" if book.get("author") else "")
        + ". Say whether the page plausibly is that novel (the English title may be a loose "
          "translation; a page about a clearly different book is not).\n\n"
        f"Extract:\n{json.dumps(extract, ensure_ascii=False, indent=1)}\n\n"
        'Reply with JSON only: {"title": "..." or null, "matches": true|false, "note": "short reason"}')
    return _ask(client, prompt)


def haiku_from_cover(client, book, image_path):
    lang = LANG_NAMES.get((book.get("source_language") or "zh").lower(), "source-language")
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    media = "image/webp" if image_path.endswith(".webp") else "image/jpeg"
    prompt = (
        f"This is the cover of a web novel we hold as \"{book['title']}\". Read the novel's title "
        f"as written on the cover in {lang} — the exact characters, not a translation. Ignore "
        f"author names, slogans and site watermarks. If no {lang} title is legible, return null. "
        f"Use \"confidence\": \"low\" for stylised lettering you are unsure of.\n\n"
        'Reply with JSON only: {"title": "..." or null, "confidence": "high"|"low", "note": "short reason"}')
    return _ask(client, [
        {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
        {"type": "text", "text": prompt}])


# ---------------------------------------------------------------------------
# Per-book resolution
# ---------------------------------------------------------------------------

def to_simplified(book, title):
    """t2s for every Chinese-source title (a no-op on simplified text)."""
    if not title or (book.get("source_language") or "zh").lower() != "zh":
        return title
    import trad_simp
    return trad_simp.convert_text(title)


def resolve(book, scraper, claude, db, args):
    row = {"book_id": book["id"], "english_title": book["title"],
           "source_language": book.get("source_language"), "source_url": book.get("source_url"),
           "title": None, "method": None, "flags": []}

    if book.get("source_url") and not args.covers_only:
        try:
            html = cached_html(scraper, book, args.cache_dir, refetch=args.refetch)
            ex = page_extract(html)
            meta = clean_title(ex["meta"].get("og:novel:book_name"))
            ans = haiku_from_page(claude, book, ex)
            read = clean_title(ans.get("title"))
            row["title"], row["method"] = (meta, "meta") if meta else (read, "haiku-page")
            if meta and read and to_simplified(book, read) != to_simplified(book, meta):
                row["flags"].append(f"haiku read {read}")
            if ans.get("matches") is False:
                row["flags"].append(f"page may be another book: {ans.get('note', '')}")
        except Exception as e:
            row["flags"].append(f"page failed: {e}")

    if not row["title"] and not args.no_covers and book.get("cover_image"):
        import cover_images
        path = cover_images.ensure_derivative(db.config, book, "medium")
        if path:
            try:
                ans = haiku_from_cover(claude, book, path)
                row["title"] = clean_title(ans.get("title"))
                row["method"] = "cover"
                if ans.get("confidence") != "high":
                    row["flags"].append(f"low-confidence cover read: {ans.get('note', '')}")
            except Exception as e:
                row["flags"].append(f"cover failed: {e}")

    if row["title"]:
        simp = to_simplified(book, row["title"])
        if simp != row["title"]:
            row["as_read"] = row["title"]
            row["title"] = simp
    return row


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_db():
    from config import TranslationConfig
    from database import DatabaseManager
    from logger import Logger
    config = TranslationConfig()
    return DatabaseManager(config, Logger(config))


def print_table(rows):
    for r in rows:
        mark = "!" if r["flags"] else " "
        t = r["title"] or "—"
        conv = f"  (t2s from {r['as_read']})" if r.get("as_read") else ""
        print(f"{mark} {r['book_id']:>4}  {t:<24} [{r['method'] or 'none'}]  {r['english_title']}{conv}")
        for fl in r["flags"]:
            print(f"        ↳ {fl}")


def cmd_apply(db, path):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)["proposals"]
    n = 0
    for r in rows:
        if r.get("title"):
            db.set_book_source_title(r["book_id"], r["title"])
            n += 1
    print(f"Wrote source_title on {n} book(s).")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-b", "--books", type=int, nargs="+", help="only these book ids")
    ap.add_argument("--all", action="store_true", help="include books that already have a source_title")
    ap.add_argument("--covers-only", action="store_true", help="skip the source pages")
    ap.add_argument("--no-covers", action="store_true", help="never fall back to the cover")
    ap.add_argument("--refetch", action="store_true", help="ignore cached HTML")
    ap.add_argument("--cache-dir", default=DEFAULT_DIR)
    ap.add_argument("--out", help=f"proposals file (default {DEFAULT_DIR}/proposals.json)")
    ap.add_argument("--apply", metavar="FILE", help="write the titles from a reviewed proposals file")
    args = ap.parse_args(argv)

    db = build_db()
    if args.apply:
        cmd_apply(db, args.apply)
        return 0

    import scrape_sync
    scraper = PageClient(str(scrape_sync.setting("scraper_url")), token=scrape_sync.scraper_token())
    claude = anthropic_client()

    books = [db.get_book(book_id=b["id"]) for b in db.list_books()
             if not b.get("is_original")
             and (args.all or not b.get("source_title"))
             and (not args.books or b["id"] in args.books)]
    books.sort(key=lambda b: b["id"])
    print(f"{len(books)} book(s) to resolve", file=sys.stderr)

    rows = []
    for i, book in enumerate(books, 1):
        host = urlparse(book.get("source_url") or "").hostname or "no url"
        print(f"[{i}/{len(books)}] book {book['id']} ({host})…", file=sys.stderr, flush=True)
        rows.append(resolve(book, scraper, claude, db, args))

    out = args.out or os.path.join(args.cache_dir, "proposals.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "proposals": rows},
                  f, ensure_ascii=False, indent=1)
    print_table(rows)
    found = sum(1 for r in rows if r["title"])
    flagged = sum(1 for r in rows if r["flags"])
    print(f"\n{found}/{len(rows)} resolved, {flagged} flagged. Review {out}, then --apply it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
