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

Apache's mod_cache_disk answers /api/public from disk without asking the
backend, so while any country is blocked those responses carry
`Vary: CF-IPCountry` — a blocked country misses the cache, reaches us,
and gets the no-store block response instead of a stored chapter.
"""
import html
import re

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
    return any(path == p or path.startswith(p + "/") for p in EXEMPT_PREFIXES)


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


def render_page(text: str) -> str:
    import settings_store
    site = (settings_store.get("public_site_name", "") or "").strip()
    title = html.escape(site or "Unavailable")
    body = "".join(f"<p>{html.escape(line)}</p>"
                   for line in text.splitlines() if line.strip())
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
h1 {{ font-size:1.25rem; margin:0 0 .75rem; }}
p {{ margin:.5rem 0; color:var(--muted); }}
</style></head>
<body><main><h1>{title}</h1>{body}</main></body></html>
"""
