"""Static HTML for the reader routes, spliced into the served SPA shell.

The reader is a React SPA: the HTML Google (and every other non-JS client)
fetches for a chapter URL is the same 4 KB shell as every other URL — title
"Translator", no text, no links. Google's first-pass indexer works from that
raw HTML, so 45k byte-identical pages read as thin duplicates and never earn
the render budget that would show the prose ("Discovered - currently not
indexed", 38k pages, 2026-09).

This module builds, per route, the pieces the shell lacks: a real <title>, a
meta description, and a `#ssr` block carrying the actual page — the chapter
heading and prose with previous/next/book links, the book page with its
chapter list, the library with its book list. Everyone gets the same HTML
(no bot-specific variant — that is cloaking); `main.jsx` removes the block
once React mounts, exactly as it hides the `#spa-fallback` banner.

Visibility mirrors the public API: `is_public` books, `published_only`
chapters. Prose is rendered by `output_formatter.render_lines_html`, the same
renderer the HTML export and WordPress use, so tables, footnotes and inline
sentinels come out as they do everywhere else.
"""
from __future__ import annotations

import re
from html import escape

from output_formatter import render_lines_html

# Google truncates descriptions around here; we cut on a word boundary.
DESCRIPTION_MAX = 160

_IMG_PLACEHOLDER_RE = re.compile(r"<p>⟦IMG:([0-9a-f]+)⟧</p>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<title>.*?</title>", re.DOTALL)

# Minimal, self-contained styling so the block is readable with no JS and no
# app CSS. Colors are explicit: the shell's <body> is a dark Tailwind class
# that only applies once the bundle's stylesheet has loaded.
#
# Theme: the block is on screen from first paint until React mounts, so it
# must match the theme the reader is about to show or it flashes. _THEME_SCRIPT
# runs inline before the block's content is parsed and copies the reader's
# saved theme (localStorage 'reader-prefs', useReaderPrefs.js — default
# 'dark') onto <html data-ssr-theme>. With JS off there is no attribute and
# the block stays light, which is what e-ink readers want.
_THEME_SCRIPT = (
    "<script>(function(){var t='dark';try{var p=JSON.parse(localStorage.getItem('reader-prefs')||'{}');"
    "if(p.theme==='light'||p.theme==='sepia')t=p.theme}catch(e){}"
    "document.documentElement.setAttribute('data-ssr-theme',t)})()</script>"
)

_STYLE = (
    "<style>"
    "#ssr{background:#fff;color:#111;font-family:Georgia,'Times New Roman',serif;"
    "max-width:42rem;margin:0 auto;padding:24px 16px;line-height:1.6;font-size:18px}"
    "html[data-ssr-theme=dark] #ssr{background:#0f172a;color:#e2e8f0}"
    "html[data-ssr-theme=dark] #ssr h2{color:#94a3b8}"
    "html[data-ssr-theme=dark] #ssr a{color:#a5b4fc}"
    "html[data-ssr-theme=dark] #ssr td,html[data-ssr-theme=dark] #ssr th{border-color:#475569}"
    "html[data-ssr-theme=sepia] #ssr{background:#fffbeb;color:#451a03}"
    "html[data-ssr-theme=sepia] #ssr h2{color:#92400e}"
    "html[data-ssr-theme=sepia] #ssr a{color:#9a3412}"
    "html[data-ssr-theme=light] body,html[data-ssr-theme=sepia] body{background:#fff}"
    "#ssr h1{font-size:1.5em;margin:0 0 .25em}"
    "#ssr h2{font-size:1.1em;font-weight:normal;color:#555;margin:0 0 1.5em}"
    "#ssr nav{display:flex;flex-wrap:wrap;gap:1em;margin:1.5em 0;font-size:.9em}"
    "#ssr a{color:#1a4fa3}"
    "#ssr img{max-width:100%}"
    "#ssr table{border-collapse:collapse}#ssr td,#ssr th{border:1px solid #ccc;padding:.2em .5em}"
    "#ssr .books,#ssr .chapters{list-style:none;padding:0}"
    "#ssr .books li,#ssr .chapters li{margin:.3em 0}"
    "</style>"
)

# What every #ssr block opens with: theme script first, then the stylesheet.
_OPEN = '<div id="ssr">' + _THEME_SCRIPT + _STYLE


def _text(s) -> str:
    return escape(str(s or ""), quote=True)


def _plain(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _truncate(text: str, limit: int = DESCRIPTION_MAX) -> str:
    text = _WS_RE.sub(" ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return (cut or text[: limit - 1]).rstrip(" ,;:") + "…"


def _chapter_label(ch: dict) -> str:
    n = ch.get("chapter")
    title = (ch.get("title") or "").strip()
    return f"Chapter {n}: {title}" if title else f"Chapter {n}"


def strip_leading_heading(lines: list) -> list:
    """Drop a leading 'Chapter N' line, as the public API does."""
    if lines and isinstance(lines[0], str) and re.match(r"^Chapter\s+\d+", lines[0], re.IGNORECASE):
        return lines[1:]
    return list(lines or [])


def render_prose(lines: list, illustrations: dict | None = None) -> str:
    """Chapter lines → HTML, with ⟦IMG⟧ placeholders resolved or dropped."""
    html = render_lines_html(strip_leading_heading(lines))

    def img(m):
        url = (illustrations or {}).get(m.group(1))
        return f'<p class="illustration"><img src="{_text(url)}" alt="" loading="lazy"></p>' if url else ""

    return _IMG_PLACEHOLDER_RE.sub(img, html)


def chapter_page(book: dict, ch: dict, chapters: list, site_name: str,
                 illustrations: dict | None = None) -> dict:
    """{title, description, html} for /library/read/{book}/{n}."""
    book_id = book["id"]
    nums = [c["chapter"] for c in chapters]
    n = ch["chapter"]
    try:
        i = nums.index(n)
        prev_n = nums[i - 1] if i > 0 else None
        next_n = nums[i + 1] if i + 1 < len(nums) else None
    except ValueError:
        prev_n = next_n = None

    prose = render_prose(ch.get("content") or [], illustrations)
    label = _chapter_label(ch)
    title = f"{label} — {book['title']} — {site_name}"
    description = _truncate(_plain(prose)) or _truncate(book.get("description") or "")

    links = []
    if prev_n is not None:
        links.append(f'<a rel="prev" href="/library/read/{book_id}/{prev_n}">← Chapter {prev_n}</a>')
    links.append(f'<a href="/library/book/{book_id}">{_text(book["title"])}</a>')
    if next_n is not None:
        links.append(f'<a rel="next" href="/library/read/{book_id}/{next_n}">Chapter {next_n} →</a>')
    nav = "<nav>" + " ".join(links) + "</nav>"

    html = (
        f'{_OPEN}<article>'
        f"<h1>{_text(label)}</h1>"
        f'<h2><a href="/library/book/{book_id}">{_text(book["title"])}</a>'
        f'{" by " + _text(book["author"]) if book.get("author") else ""}</h2>'
        f"{nav}{prose}{nav}"
        f"</article></div>"
    )
    return {"title": title, "description": description, "html": html}


def book_page(book: dict, chapters: list, site_name: str) -> dict:
    """{title, description, html} for /library/book/{book}."""
    book_id = book["id"]
    title = f"{book['title']} — {site_name}"
    desc_src = book.get("description") or ""
    description = _truncate(desc_src) or _truncate(
        f"{book['title']}, {len(chapters)} chapters, read online at {site_name}.")
    items = "".join(
        f'<li><a href="/library/read/{book_id}/{c["chapter"]}">{_text(_chapter_label(c))}</a></li>'
        for c in chapters)
    desc_html = "".join(f"<p>{_text(p)}</p>" for p in re.split(r"\n\s*\n", desc_src) if p.strip())
    html = (
        f'{_OPEN}<article>'
        f"<h1>{_text(book['title'])}</h1>"
        f'<h2>{"by " + _text(book["author"]) if book.get("author") else ""}'
        f'{" · " if book.get("author") else ""}{len(chapters)} chapters</h2>'
        f'<nav><a href="/library">← Library</a></nav>'
        f"{desc_html}"
        f'<ol class="chapters">{items}</ol>'
        f"</article></div>"
    )
    return {"title": title, "description": description, "html": html}


def library_page(books: list, site_name: str) -> dict:
    """{title, description, html} for /library and /."""
    items = "".join(
        f'<li><a href="/library/book/{b["id"]}">{_text(b["title"])}</a>'
        f'{" by " + _text(b["author"]) if b.get("author") else ""}'
        f' — {b.get("published_chapter_count", b.get("chapter_count", 0))} chapters</li>'
        for b in books)
    html = (
        f'{_OPEN}'
        f"<h1>{_text(site_name)}</h1>"
        f'<ul class="books">{items}</ul>'
        f"</div>"
    )
    description = _truncate(
        f"{site_name}: {len(books)} translated web novels, free to read online.")
    return {"title": site_name, "description": description, "html": html}


def splice(shell: str, page: dict) -> str:
    """Put a page's title, description and #ssr block into the SPA shell."""
    head = (f'<meta name="description" content="{_text(page["description"])}" />'
            if page.get("description") else "")
    html = _TITLE_RE.sub(f"<title>{_text(page['title'])}</title>", shell, count=1)
    if head:
        html = html.replace("</head>", head + "</head>", 1)
    marker = '<div id="root"></div>'
    if marker in html:
        return html.replace(marker, marker + page["html"], 1)
    # Shell without the root div (tests, hand-edited index.html): first thing in <body>.
    return re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + page["html"], html, count=1)
