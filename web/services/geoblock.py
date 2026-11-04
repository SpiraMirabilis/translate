"""
Country geoblocking for the public reader.

The country comes from Cloudflare's CF-IPCountry header (ISO 3166-1
alpha-2, plus XX = unknown and T1 = Tor), which the edge adds to every
proxied request — no GeoIP database of our own. A request without the
header (direct to origin, local tooling) is never blocked: this is a
courtesy wall, not a security boundary, and VPNs walk straight past it.

Settings (settings.json, read live per request so the admin process
saving them reaches the public process without a restart):
- geoblock_countries: comma-separated codes, e.g. "CN,RU". Blank = off.
- geoblock_message: what a blocked visitor reads. Blank = DEFAULT_MESSAGE.
  Each line is one block of Markdown: a paragraph (links, *em*, **strong**,
  ![images](…)) or a #–### heading; bare email addresses and URLs link
  themselves. See render_message().

Apache's mod_cache_disk answers /api/public from disk without asking the
backend, so while any country is blocked those responses carry
`Vary: CF-IPCountry` — a blocked country misses the cache, reaches us,
and gets the no-store block response instead of a stored chapter.
"""
import functools
import html
import posixpath
import re
from urllib.parse import urlsplit

DEFAULT_MESSAGE = "This site isn't available in your region."
STATUS = 403

# Paths that answer even for a blocked country: the watchdog's health
# probe, and unsubscribing from reply emails (someone who subscribed
# before a block went up must still be able to stop the mail).
EXEMPT_PREFIXES = ("/api/health", "/api/public/comments/unsubscribe")

_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]$")


def parse_countries(value: str) -> list:
    """'cn, ru  kp' -> ['CN', 'KP', 'RU']. Raises ValueError on a bad code."""
    codes = set()
    for part in re.split(r"[\s,;]+", value or ""):
        if not part:
            continue
        code = part.upper()
        if not _CODE_RE.match(code):
            raise ValueError(f"{part!r} is not a two-letter country code")
        codes.add(code)
    return sorted(codes)


def blocked_countries() -> frozenset:
    import settings_store
    try:
        return frozenset(parse_countries(settings_store.get("geoblock_countries", "")))
    except ValueError:
        # A hand-edited settings.json with a typo must not take the site
        # down or silently block nothing: keep every code that does parse.
        raw = re.split(r"[\s,;]+", settings_store.get("geoblock_countries", "") or "")
        return frozenset(p.upper() for p in raw if _CODE_RE.match(p.upper()))


def request_country(request) -> str:
    return (request.headers.get("cf-ipcountry", "") or "").strip().upper()


def is_exempt(path: str) -> bool:
    if any(path == p or path.startswith(p + "/") for p in EXEMPT_PREFIXES):
        return True
    # The images the message shows must load for the visitor reading it.
    return path in message_image_paths(message())


def message() -> str:
    import settings_store
    return (settings_store.get("geoblock_message", "") or "").strip() or DEFAULT_MESSAGE


def block_response(request):
    from fastapi.responses import HTMLResponse, JSONResponse

    headers = {"Cache-Control": "no-store", "Vary": "CF-IPCountry"}
    text = message()
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": text, "geoblocked": True},
                            status_code=STATUS, headers=headers)
    return HTMLResponse(render_page(text), status_code=STATUS, headers=headers)


_ALLOWED_TAGS = ["p", "h1", "h2", "h3", "a", "em", "strong", "br", "img"]
_ALLOWED_ATTRIBUTES = {"a": ["href"], "img": ["src", "alt"]}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg"}
_SRC_RE = re.compile(r'<img[^>]*\ssrc="([^"]*)"')


@functools.lru_cache(maxsize=8)
def render_message(text: str) -> str:
    """The message as HTML, one block (paragraph or heading) per non-blank line.

    Deliberately not the Reader's pipeline: this page ships no JS, and a
    courtesy notice needs headings, links, emphasis and an image, not tables
    and footnotes. Raw HTML is escaped before Markdown sees it, so only
    Markdown can make tags, and bleach then keeps just those with safe
    schemes (`[x](javascript:…)` loses its href); lists and the like are
    flattened to a paragraph. Cached: a blocked country's bots hit this on
    every request, and the text only changes in Settings."""
    import bleach
    import markdown

    md = markdown.Markdown()
    paras = []
    for line in text.splitlines():
        if not line.strip():
            continue
        md.reset()
        rendered = md.convert(html.escape(line, quote=False))
        cleaned = bleach.clean(rendered, tags=_ALLOWED_TAGS,
                               attributes=_ALLOWED_ATTRIBUTES,
                               protocols=_ALLOWED_PROTOCOLS, strip=True)
        linked = bleach.linkify(cleaned, parse_email=True).strip()
        if not linked:
            continue
        if not re.match(r"<(p|h[1-3])>", linked):
            linked = f"<p>{linked}</p>"
        paras.append(linked)
    return "".join(paras)


@functools.lru_cache(maxsize=8)
def message_image_paths(text: str) -> frozenset:
    """Local paths of the images the message shows, which is_exempt lets
    through. Only image-file paths, so an `![](/library)` can't open a page;
    the host isn't checked (behind the proxy we can't trust ours), which at
    worst exempts an image path we serve anyway."""
    paths = set()
    for src in _SRC_RE.findall(render_message(text)):
        path = posixpath.normpath(urlsplit(html.unescape(src)).path or "/")
        if path.startswith("/") and posixpath.splitext(path)[1].lower() in _IMAGE_EXTS:
            paths.add(path)
    return frozenset(paths)


def render_page(text: str) -> str:
    import settings_store
    site = (settings_store.get("public_site_name", "") or "").strip()
    title = html.escape(site or "Unavailable")
    body = render_message(text)
    # A message with its own heading replaces the site-name one.
    heading = "" if "<h1>" in body else f"<h1>{title}</h1>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>
:root {{ --bg:#f8fafc; --fg:#1e293b; --muted:#64748b; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f172a; --fg:#e2e8f0; --muted:#94a3b8; }} }}
body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
       background:var(--bg); color:var(--fg); padding:16px; box-sizing:border-box;
       font:16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width:32rem; text-align:center; }}
h1 {{ font-size:1.25rem; margin:1rem 0 .75rem; }}
h1:first-child {{ margin-top:0; }}
p {{ margin:.5rem 0; color:var(--muted); }}
a {{ color:var(--fg); }}
img {{ max-width:100%; height:auto; border-radius:8px; }}
</style></head>
<body><main>{heading}{body}</main></body></html>
"""
