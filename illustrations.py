"""
Shared illustration support: marker format, extraction/dedup/filtering, and
on-disk storage for in-chapter images imported from EPUB/FB2.

An illustration is represented inside a chapter's content (a list of lines) as
an inert marker line:

    ⟦IMG:7f3a2c⟧

The marker rides through the AI translation pass as a position anchor; the
opaque id maps (via the `illustrations` DB table) to a file on disk under
`<script_dir>/illustrations/<book_id>/<marker_id><ext>`. See
`translation_engine.reconcile_illustration_markers` for how markers that the
model drops/mangles are recovered, and `web/api/books.py` for serving.
"""
import os
import re
import hashlib
import secrets

# Strict marker: exactly an opaque lowercase-hex id wrapped in math brackets
# (U+27E6/U+27E7). These brackets do not occur in CJK/Russian prose and LLMs
# reliably pass them through unchanged.
MARKER_RE = re.compile(r'^\s*⟦IMG:([0-9a-f]{4,})⟧\s*$')

# Lenient matcher used during post-translation reconciliation to recover a
# marker the model lightly mangled — e.g. translated/substituted brackets
# (【IMG:..】, [IMG:..], (IMG:..)), stray spaces, or case changes. Matches
# anywhere in a line, not just whole-line.
MARKER_RE_LENIENT = re.compile(r'[⟦【\[(<{]\s*IMG[:：]\s*([0-9A-Fa-f]{4,})\s*[⟧】\])>}]')

# Decorative-image filter thresholds (tunable). Images smaller than this are
# almost always spacers, drop-caps, scene-break separators, or tracking pixels.
MIN_IMAGE_BYTES = 3000
MIN_IMAGE_DIMENSION = 64  # px; skip if both dimensions are under this

_MIME_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/pjpeg": ".jpg",
    "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
    "image/svg+xml": ".svg", "image/bmp": ".bmp", "image/tiff": ".tiff",
}


def make_marker(marker_id):
    """Return the canonical marker line for an id."""
    return f"⟦IMG:{marker_id}⟧"


def parse_marker(line):
    """Return the marker_id if `line` is a clean marker line, else None."""
    if not isinstance(line, str):
        return None
    m = MARKER_RE.match(line)
    return m.group(1) if m else None


def parse_marker_lenient(line):
    """Return a marker_id recovered from a possibly-mangled line, else None.

    Lowercased so it matches the stored (lowercase) id even if the model
    upper-cased the hex.
    """
    if not isinstance(line, str):
        return None
    m = MARKER_RE_LENIENT.search(line)
    return m.group(1).lower() if m else None


def ext_for_mime(mime):
    """Map an image MIME type to a file extension (default .jpg)."""
    return _MIME_EXT.get((mime or "").strip().lower(), ".jpg")


def markers_in(lines):
    """Return the ordered list of clean marker ids present in a content array."""
    out = []
    for line in lines or []:
        mid = parse_marker(line)
        if mid:
            out.append(mid)
    return out


def _looks_decorative(image_bytes, ext):
    """Heuristic: is this image a decorative/tiny asset we should skip?

    SVGs are kept regardless of byte size (vector separators are rare and small
    SVGs are often real figures). Raster images under the byte threshold, or
    whose decoded dimensions are both tiny, are treated as decorative.
    """
    if ext == ".svg":
        return False
    if len(image_bytes) < MIN_IMAGE_BYTES:
        return True
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(image_bytes)) as im:
            w, h = im.size
            if w < MIN_IMAGE_DIMENSION and h < MIN_IMAGE_DIMENSION:
                return True
    except Exception:
        # Undecodable as a raster image — keep it; the byte-size gate already
        # filtered the obvious junk.
        pass
    return False


class IllustrationCollector:
    """Accumulates images for one book-import run, deduping by content hash.

    Used by the EPUB/FB2 processors during chapter extraction: each `add()`
    either returns a stable marker_id (to embed in the content) or None when the
    image is filtered out as decorative. Identical bytes always yield the same
    marker_id, so a repeated image is stored once.
    """

    def __init__(self):
        self._by_hash = {}      # sha1 -> marker_id
        self._items = {}        # marker_id -> {data, ext, alt, original_href}

    def add(self, image_bytes, mime=None, ext=None, alt=None, original_href=None):
        """Register an image; return its marker_id, or None if filtered out."""
        if not image_bytes:
            return None
        ext = ext or ext_for_mime(mime)
        if _looks_decorative(image_bytes, ext):
            return None

        digest = hashlib.sha1(image_bytes).hexdigest()
        existing = self._by_hash.get(digest)
        if existing:
            return existing

        marker_id = self._new_id()
        self._by_hash[digest] = marker_id
        self._items[marker_id] = {
            "data": image_bytes,
            "ext": ext,
            "alt": alt,
            "original_href": original_href,
        }
        return marker_id

    def get(self, marker_id):
        return self._items.get(marker_id)

    def _new_id(self):
        while True:
            mid = secrets.token_hex(3)  # 6 hex chars
            if mid not in self._items:
                return mid


def illustrations_dir(config, book_id):
    """Absolute path to a book's illustrations directory (created if missing)."""
    d = os.path.join(config.script_dir, "illustrations", str(book_id))
    os.makedirs(d, exist_ok=True)
    return d


def store_chapter_illustrations(db_manager, config, book_id, content_lines,
                                collector, queue_id=None, ordinal_start=0):
    """Persist the collected illustrations referenced by a chapter's content.

    Writes each image file (idempotent — skips if already on disk) and inserts a
    row via DatabaseManager.add_illustration (idempotent on (book_id, marker_id)).
    Returns the number of newly persisted illustrations.
    """
    stored = 0
    ordinal = ordinal_start
    base = None
    for marker_id in markers_in(content_lines):
        item = collector.get(marker_id)
        if not item:
            continue  # marker present but image was deduped/persisted elsewhere
        if base is None:
            base = illustrations_dir(config, book_id)
        filename = f"{marker_id}{item['ext']}"
        rel_path = f"illustrations/{book_id}/{filename}"
        abs_path = os.path.join(base, filename)
        if not os.path.exists(abs_path):
            with open(abs_path, "wb") as f:
                f.write(item["data"])
        # Mirror to Spaces/CDN for direct serving (no-op when disabled).
        try:
            import spaces
            if spaces.is_enabled(config):
                spaces.upload_relpath(config, rel_path)
        except Exception:
            pass
        added = db_manager.add_illustration(
            book_id=book_id,
            marker_id=marker_id,
            filename=rel_path,
            alt=item.get("alt"),
            original_href=item.get("original_href"),
            ordinal=ordinal,
            queue_id=queue_id,
        )
        if added:
            stored += 1
        ordinal += 1
    return stored
