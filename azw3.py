"""
AZW3 (Kindle KF8) generation helper.

There is no viable pure-Python KF8 writer, so we convert our already-generated
EPUB to AZW3 with Calibre's ``ebook-convert`` CLI. Calibre is installed as the
self-contained *isolated* binary bundle (``/opt/calibre``) which ships its own
Python + Qt and touches no apt/system packages; conversion runs headless via
``QT_QPA_PLATFORM=offscreen`` (no X server, no xvfb).

Every entry point degrades gracefully when the converter is absent:
``is_available()`` returns False and callers hide the AZW3 links / return 503.
"""
import os
import shutil
import subprocess
import logging

_logger = logging.getLogger("azw3")

# Convert timeout — big books (1000+ chapters) can take a couple of minutes.
_CONVERT_TIMEOUT = 15 * 60


def ebook_convert_path():
    """Resolve the ebook-convert binary: env override, isolated bundle, then PATH."""
    override = os.getenv("EBOOK_CONVERT_PATH", "").strip()
    if override:
        return override if os.path.exists(override) else None
    bundled = "/opt/calibre/ebook-convert"
    if os.path.exists(bundled):
        return bundled
    return shutil.which("ebook-convert")


def is_available():
    """True when an ebook-convert binary can be located."""
    return ebook_convert_path() is not None


def convert_epub_to_azw3(epub_path, out_path, logger=None):
    """Convert an EPUB file to AZW3 at ``out_path``.

    Returns True on success. Never raises — logs and returns False so callers
    can fall back to serving just the EPUB.
    """
    log = logger or _logger
    binary = ebook_convert_path()
    if not binary:
        log.error("AZW3 conversion requested but ebook-convert is not available")
        return False
    if not epub_path or not os.path.exists(epub_path):
        log.error("AZW3 conversion source EPUB missing: %s", epub_path)
        return False

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Convert into a temp file and os.replace() on success: ebook-convert
    # writing straight to out_path leaves a PARTIAL file behind on timeout or
    # failure, and callers' mtime/existence checks would then treat that
    # corrupt artifact as valid forever. The temp name keeps the .azw3
    # extension because ebook-convert infers the output format from it.
    tmp_path = f"{out_path}.tmp-{os.getpid()}.azw3"

    # Bundled Qt needs a platform plugin; "offscreen" keeps it fully headless.
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    try:
        proc = subprocess.run(
            [binary, epub_path, tmp_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=_CONVERT_TIMEOUT,
        )
        if proc.returncode != 0 or not os.path.exists(tmp_path):
            log.error(
                "ebook-convert failed (rc=%s) for %s\nstderr: %s",
                proc.returncode, epub_path, (proc.stderr or "")[-2000:],
            )
            return False
        os.replace(tmp_path, out_path)
    except subprocess.TimeoutExpired:
        log.error("ebook-convert timed out after %ss converting %s", _CONVERT_TIMEOUT, epub_path)
        return False
    except OSError as e:
        log.error("Failed to launch ebook-convert (%s): %s", binary, e)
        return False
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    log.info("Converted EPUB -> AZW3: %s", out_path)
    return True
