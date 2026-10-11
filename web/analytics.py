"""Google Tag Manager snippet for the public reader's HTML pages.

Spliced in server-side by the public process only (web/app_factory.py), not
baked into index.html: the admin host serves the same SPA shell, and the
admin's own browsing must not land in the reader site's analytics.
"""
import re

GTM_ID = "GTM-PFQ598LM"

GTM_HEAD = (
    "<!-- Google Tag Manager -->\n"
    "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':\n"
    "new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],\n"
    "j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=\n"
    "'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);\n"
    f"}})(window,document,'script','dataLayer','{GTM_ID}');</script>\n"
    "<!-- End Google Tag Manager -->\n"
)

GTM_BODY = (
    "<!-- Google Tag Manager (noscript) -->\n"
    f'<noscript><iframe src="https://www.googletagmanager.com/ns.html?id={GTM_ID}"\n'
    'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>\n'
    "<!-- End Google Tag Manager (noscript) -->\n"
)

_HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)
_BODY_RE = re.compile(r"<body[^>]*>", re.IGNORECASE)


def with_gtm(html: str) -> str:
    """Return html with the GTM script opening <head> and the noscript
    iframe opening <body>. Idempotent; a page missing either tag gets
    only the half it has room for."""
    if GTM_ID in html:
        return html
    html = _HEAD_RE.sub(lambda m: m.group(0) + "\n" + GTM_HEAD, html, count=1)
    return _BODY_RE.sub(lambda m: m.group(0) + "\n" + GTM_BODY, html, count=1)
