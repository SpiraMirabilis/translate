"""
DigitalOcean Spaces (S3-compatible) storage helper.

Offloads serving of covers, in-chapter illustrations, and generated EPUBs to a
Spaces bucket + CDN edge, keeping local files as the source of truth. Every
operation is a no-op (returns falsy) when Spaces is disabled or unconfigured, so
callers can wrap writes/deletes unconditionally and the app behaves exactly as
before in local-only mode.

Object keys mirror the local relative paths under a configurable prefix, e.g.
local `covers/39.jpg` → key `t9/covers/39.jpg` → CDN
`https://<cdn-base>/t9/covers/39.jpg`. Credentials come from .env
(BUCKET_ENDPOINT / BUCKET_ACCESS_ID / BUCKET_SECRET); everything else from
settings (see settings_store / config.py).
"""
import os
import hashlib
import logging
import threading
from urllib.parse import urlparse

_logger = logging.getLogger("spaces")

_lock = threading.Lock()
_client = None          # cached boto3 S3 client
_client_cfg = None      # the (endpoint, key, secret, region) tuple the client was built for


def _cfg():
    """Pull Spaces config off a TranslationConfig, lazily importing it."""
    from config import TranslationConfig
    # A fresh TranslationConfig just reads already-loaded env/settings; cheap.
    return TranslationConfig()


def is_enabled(config=None):
    """True when Spaces is turned on and credentials are present."""
    c = config or _cfg()
    return bool(
        getattr(c, "spaces_enabled", False)
        and c.spaces_access_key and c.spaces_secret_key
    )


def _regional_endpoint(config):
    """Return the bucket-less regional endpoint boto3 should target.

    The .env BUCKET_ENDPOINT is bucket-specific
    (https://<bucket>.<region>.digitaloceanspaces.com); boto3 wants the regional
    host with the bucket passed separately. Derive it from the region, falling
    back to stripping a leading `<bucket>.` from the configured endpoint.
    """
    region = config.spaces_region or "nyc3"
    ep = (config.spaces_endpoint or "").strip()
    if ep:
        host = urlparse(ep).netloc or ep
        prefix = f"{config.spaces_bucket}."
        if host.startswith(prefix):
            host = host[len(prefix):]
        return f"https://{host}"
    return f"https://{region}.digitaloceanspaces.com"


def _get_client(config):
    """Return a cached boto3 S3 client, or None if disabled/unavailable."""
    global _client, _client_cfg
    if not is_enabled(config):
        return None
    key = (config.spaces_endpoint, config.spaces_access_key,
           config.spaces_secret_key, config.spaces_region)
    with _lock:
        if _client is not None and _client_cfg == key:
            return _client
        try:
            import boto3
            from botocore.config import Config as _BotoConfig
            _client = boto3.client(
                "s3",
                endpoint_url=_regional_endpoint(config),
                region_name=config.spaces_region or "nyc3",
                aws_access_key_id=config.spaces_access_key,
                aws_secret_access_key=config.spaces_secret_key,
                config=_BotoConfig(s3={"addressing_style": "virtual"}),
            )
            _client_cfg = key
            return _client
        except Exception as e:
            _logger.error(f"Failed to init Spaces client: {e}")
            _client = None
            return None


def key_for(config, rel_path):
    """Map a local relative path (e.g. 'covers/39.jpg') to its object key."""
    rel = (rel_path or "").lstrip("/")
    prefix = (config.spaces_prefix or "").strip("/")
    return f"{prefix}/{rel}" if prefix else rel


def public_url(config, key):
    """Build the public CDN URL for an object key."""
    base = (config.spaces_cdn_base or "").rstrip("/")
    return f"{base}/{key}" if base else None


def url_for_relpath(config, rel_path):
    """Convenience: CDN URL for a local relative path, or None if disabled."""
    if not is_enabled(config) or not rel_path:
        return None
    return public_url(config, key_for(config, rel_path))


def upload(config, local_path, key, content_type=None):
    """Upload a local file to Spaces under `key` (public-read). Returns True on success."""
    client = _get_client(config)
    if client is None:
        return False
    if not os.path.exists(local_path):
        _logger.warning(f"Spaces upload skipped, missing file: {local_path}")
        return False
    if not content_type:
        import mimetypes
        content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    try:
        with open(local_path, "rb") as f:
            client.put_object(
                Bucket=config.spaces_bucket, Key=key, Body=f,
                ACL="public-read", ContentType=content_type,
            )
        return True
    except Exception as e:
        _logger.error(f"Spaces upload failed ({key}): {e}")
        return False


def upload_relpath(config, rel_path, content_type=None):
    """Upload the local file at script_dir/<rel_path> to its mirrored key."""
    if not is_enabled(config):
        return False
    local_path = os.path.join(config.script_dir, rel_path)
    return upload(config, local_path, key_for(config, rel_path), content_type)


def upload_bytes(config, data, key, content_type="application/octet-stream"):
    """Upload raw bytes to Spaces under `key` (public-read). Returns True on success."""
    client = _get_client(config)
    if client is None:
        return False
    try:
        client.put_object(
            Bucket=config.spaces_bucket, Key=key, Body=data,
            ACL="public-read", ContentType=content_type,
        )
        return True
    except Exception as e:
        _logger.error(f"Spaces upload_bytes failed ({key}): {e}")
        return False


class SpacesUnavailable(Exception):
    """Raised by exists() when the answer is unknown (transport/auth failure),
    as opposed to a definitive 404. Callers that would do expensive work on
    'missing' (prewarm rebuild + re-upload of every book) must not treat an
    outage as absence."""


def exists(config, key):
    """True if an object exists at `key`, False if it definitively doesn't
    (404). Raises SpacesUnavailable when the check itself failed."""
    client = _get_client(config)
    if client is None:
        return False
    try:
        client.head_object(Bucket=config.spaces_bucket, Key=key)
        return True
    except Exception as e:
        code = None
        resp = getattr(e, "response", None)
        if isinstance(resp, dict):
            code = str(resp.get("ResponseMetadata", {}).get("HTTPStatusCode", "")) \
                or str(resp.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        _logger.error(f"Spaces exists({key}) check failed (treating as unavailable): {e}")
        raise SpacesUnavailable(str(e)) from e


def delete(config, key):
    """Best-effort delete of an object. Returns True if the call succeeded."""
    client = _get_client(config)
    if client is None:
        return False
    try:
        client.delete_object(Bucket=config.spaces_bucket, Key=key)
        return True
    except Exception as e:
        _logger.error(f"Spaces delete failed ({key}): {e}")
        return False


def delete_relpath(config, rel_path):
    """Best-effort delete of the object mirroring a local relative path."""
    if not is_enabled(config) or not rel_path:
        return False
    return delete(config, key_for(config, rel_path))


def epub_version(book_id, modified_date):
    """Short content-version token for a book's EPUB, derived from modified_date.

    modified_date bumps on every chapter save/edit/replace/delete, so the token
    changes exactly when the EPUB's content does — giving immutable, never-stale
    versioned URLs.
    """
    raw = f"{book_id}:{modified_date or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def epub_key(config, book_id, version):
    """Full object key for a versioned EPUB."""
    return key_for(config, f"epub/{book_id}/{book_id}-{version}.epub")


def prune_epub_versions(config, book_id, keep_key):
    """Delete all EPUB objects for a book except `keep_key` (best-effort)."""
    client = _get_client(config)
    if client is None:
        return 0
    key_prefix = key_for(config, f"epub/{book_id}/")
    deleted = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=config.spaces_bucket, Prefix=key_prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", []) if o["Key"] != keep_key]
            if objs:
                client.delete_objects(Bucket=config.spaces_bucket, Delete={"Objects": objs})
                deleted += len(objs)
    except Exception as e:
        _logger.error(f"Spaces prune_epub_versions failed ({key_prefix}): {e}")
    return deleted


def azw3_key(config, book_id, version):
    """Full object key for a versioned AZW3 (Kindle) file."""
    return key_for(config, f"azw3/{book_id}/{book_id}-{version}.azw3")


def prune_azw3_versions(config, book_id, keep_key):
    """Delete all AZW3 objects for a book except `keep_key` (best-effort)."""
    client = _get_client(config)
    if client is None:
        return 0
    key_prefix = key_for(config, f"azw3/{book_id}/")
    deleted = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=config.spaces_bucket, Prefix=key_prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", []) if o["Key"] != keep_key]
            if objs:
                client.delete_objects(Bucket=config.spaces_bucket, Delete={"Objects": objs})
                deleted += len(objs)
    except Exception as e:
        _logger.error(f"Spaces prune_azw3_versions failed ({key_prefix}): {e}")
    return deleted


def delete_prefix(config, rel_prefix):
    """Best-effort delete of all objects under a local relative prefix (e.g. 'illustrations/39')."""
    client = _get_client(config)
    if client is None:
        return 0
    key_prefix = key_for(config, rel_prefix.rstrip("/")) + "/"
    deleted = 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=config.spaces_bucket, Prefix=key_prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                client.delete_objects(Bucket=config.spaces_bucket, Delete={"Objects": objs})
                deleted += len(objs)
    except Exception as e:
        _logger.error(f"Spaces delete_prefix failed ({key_prefix}): {e}")
    return deleted
