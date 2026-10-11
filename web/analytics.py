"""Google Tag Manager snippet for the public reader's HTML pages.

Spliced in server-side by the public process only (web/app_factory.py), not
baked into index.html: the admin host serves the same SPA shell, and the
admin's own browsing must not land in the reader site's analytics.

The container ID comes from ``GTM_ID`` in ``.env`` (e.g. ``GTM-XXXXXXX``) and
is read per call, so a deployment without one simply serves no snippet. The
value is checked against the GTM ID shape before it goes into a <script>.
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

_GTM_ID_RE = re.compile(r"^GTM-[A-Z0-9]{4,12}$")
# Present in every page with_gtm has already touched, whatever the ID.
_MARKER = "<!-- Google Tag Manager -->"
_warned_bad_id = set()


def gtm_id() -> str | None:
    """The configured GTM container ID, or None when unset or malformed."""
    value = os.getenv("GTM_ID", "").strip()
    if not value:
        return None
    if not _GTM_ID_RE.match(value):
        if value not in _warned_bad_id:
            _warned_bad_id.add(value)
            logger.warning(f"GTM_ID {value!r} is not a GTM container ID; analytics disabled")
        return None
    return value


def gtm_head(container_id: str) -> str:
    return (
        f"{_MARKER}\n"
        "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':\n"
        "new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],\n"
        "j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=\n"
        "'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);\n"
        f"}})(window,document,'script','dataLayer','{container_id}');</script>\n"
        "<!-- End Google Tag Manager -->\n"
    )


def gtm_body(container_id: str) -> str:
    return (
        "<!-- Google Tag Manager (noscript) -->\n"
        f'<noscript><iframe src="https://www.googletagmanager.com/ns.html?id={container_id}"\n'
        'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>\n'
        "<!-- End Google Tag Manager (noscript) -->\n"
    )


_HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)
_BODY_RE = re.compile(r"<body[^>]*>", re.IGNORECASE)


def with_gtm(html: str) -> str:
    """Return html with the GTM script opening <head> and the noscript
    iframe opening <body>. Idempotent; a page missing either tag gets
    only the half it has room for. No GTM_ID configured → html unchanged."""
    container_id = gtm_id()
    if not container_id or _MARKER in html:
        return html
    html = _HEAD_RE.sub(lambda m: m.group(0) + "\n" + gtm_head(container_id), html, count=1)
    return _BODY_RE.sub(lambda m: m.group(0) + "\n" + gtm_body(container_id), html, count=1)
