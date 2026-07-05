"""
Cover image derivatives (thumb / medium) generation + Spaces mirroring.

Three tiers per book cover:
  - original : as uploaded (covers/<id>.<ext>) — EPUB embedding + master copy
  - medium   : covers/<id>_medium.webp (~512px) — Library grid + book detail hero
  - thumb    : covers/<id>_thumb.webp  (~80px)  — admin list rows, Reader TOC

Derivatives are WebP, generated from the original with Pillow, and uploaded to
Spaces when enabled. Shared by web/api/books.py, web/api/public.py, and
backfill_spaces.py so the logic lives in exactly one place.
"""
import os
import logging

_logger = logging.getLogger("cover_images")

# (max_width, max_height) — thumbnail() preserves aspect ratio within the box.
SIZES = {
    "thumb":  (80, 112),    # 2x the 32x44 admin/TOC row
    "medium": (512, 768),   # crisp on grid cards + detail hero at 2x retina
}


def derivative_relpath(book_id, kind):
    """Relative path for a derivative, e.g. 'covers/39_medium.webp'."""
    return f"covers/{book_id}_{kind}.webp"


def ensure_derivative(config, book, kind):
    """Return the local path to a cover derivative, generating it if missing.

    Generates from the book's full cover (book['cover_image']) and, when Spaces
    is enabled, uploads the derivative. Returns None when the book has no cover
    or generation fails.
    """
    if kind not in SIZES:
        return None
    cover_rel = (book or {}).get("cover_image")
    book_id = (book or {}).get("id")
    if not cover_rel or book_id is None:
        return None

    rel = derivative_relpath(book_id, kind)
    out_path = os.path.join(config.script_dir, rel)

    if not os.path.exists(out_path):
        src_path = os.path.join(config.script_dir, cover_rel)
        if not os.path.exists(src_path):
            return None
        try:
            from PIL import Image
            with Image.open(src_path) as img:
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                img.thumbnail(SIZES[kind], Image.LANCZOS)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                img.save(out_path, "WEBP", quality=82, method=6)
        except Exception as e:
            _logger.error(f"Cover {kind} generation failed for book {book_id}: {e}")
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            return None
        _upload(config, rel)

    return out_path


def generate_all(config, book):
    """Generate (and upload) every derivative for a book's cover."""
    for kind in SIZES:
        ensure_derivative(config, book, kind)


def _upload(config, rel):
    try:
        import spaces
        if spaces.is_enabled(config):
            spaces.upload_relpath(config, rel)
    except Exception:
        pass
