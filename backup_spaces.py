#!/usr/bin/env python3
"""
Private-bucket backup storage for the t9 MySQL dumps.

Deliberately standalone: no `config`/`settings_store` imports, no dependency on
the web app being importable. A backup has to work on a day when the app does
not.

⚠️  This is NOT `spaces.py`. That module serves covers/illustrations/EPUBs and
uploads everything with `ACL="public-read"` — routing a database dump through it
would publish the dump on the CDN. Everything here uploads `ACL="private"` to a
separate bucket that has no CDN attached.

Config comes from .env only: BACKUP_BUCKET / BACKUP_BUCKET_REGION /
BACKUP_BUCKET_ENDPOINT, plus BACKUP_BUCKET_ACCESS_ID / BACKUP_BUCKET_SECRET —
which fall back to the app's BUCKET_ACCESS_ID / BUCKET_SECRET when one key
covers both buckets.

Usage:
    backup_spaces.py list
    backup_spaces.py upload <local.sql.gz> [--key KEY]
    backup_spaces.py fetch <name|latest> --dest DIR
    backup_spaces.py prune [--dry-run] [--daily N] [--monthly N]
"""
import argparse
import hashlib
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlparse

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

PREFIX = "mysql/"
DAILY_KEEP = 14
MONTHLY_KEEP = 6

# t9-20260906-033002.sql.gz
NAME_RE = re.compile(r"-(\d{8})-(\d{6})\.sql\.gz$")


def _env(key, default=""):
    """Read one key out of .env. Never sources the file — no shell eval."""
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return default


def _conf():
    bucket = _env("BACKUP_BUCKET")
    if not bucket:
        raise SystemExit("ERROR: BACKUP_BUCKET is not set in .env")
    region = _env("BACKUP_BUCKET_REGION", "nyc3")
    endpoint = _env("BACKUP_BUCKET_ENDPOINT") or f"https://{region}.digitaloceanspaces.com"
    # Accept a bucket-scoped endpoint (the BUCKET_ENDPOINT convention) and strip
    # the leading "<bucket>." — boto3 wants the regional host with the bucket
    # passed separately.
    host = urlparse(endpoint).netloc or endpoint
    if host.startswith(f"{bucket}."):
        host = host[len(bucket) + 1:]
    # Prefer a key scoped to the backup bucket; fall back to the app's Spaces
    # key when the same key covers both buckets.
    key_id = _env("BACKUP_BUCKET_ACCESS_ID") or _env("BUCKET_ACCESS_ID")
    secret = _env("BACKUP_BUCKET_SECRET") or _env("BUCKET_SECRET")
    if not key_id or not secret:
        raise SystemExit("ERROR: no bucket credentials in .env "
                         "(BACKUP_BUCKET_ACCESS_ID/SECRET or BUCKET_ACCESS_ID/SECRET)")
    return bucket, region, f"https://{host}", key_id, secret


def _client():
    bucket, region, endpoint, key_id, secret = _conf()
    import boto3
    from botocore.config import Config as BotoConfig
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=BotoConfig(s3={"addressing_style": "virtual"},
                          retries={"max_attempts": 5, "mode": "standard"}),
    )
    return client, bucket


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _stamp(key):
    """Parse the dump timestamp out of a key, or None if it isn't one of ours."""
    m = NAME_RE.search(key)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


# ---------------------------------------------------------------- operations

def list_backups(client=None, bucket=None):
    """[(key, stamp, size)] under the mysql/ prefix, newest first."""
    if client is None:
        client, bucket = _client()
    out = []
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": PREFIX}
        if token:
            kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw)
        for obj in resp.get("Contents", []):
            stamp = _stamp(obj["Key"])
            if stamp:
                out.append((obj["Key"], stamp, obj["Size"]))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    out.sort(key=lambda r: r[1], reverse=True)
    return out


def upload(local_path, key=None):
    """Upload a dump privately, then verify it landed at full size.

    Returns True only after a HEAD confirms the byte count, so the caller can
    safely treat True as "this copy exists off-box".
    """
    client, bucket = _client()
    if key is None:
        key = PREFIX + os.path.basename(local_path)
    size = os.path.getsize(local_path)
    digest = _sha256(local_path)

    client.upload_file(
        local_path, bucket, key,
        ExtraArgs={
            "ACL": "private",
            "ContentType": "application/gzip",
            "Metadata": {"sha256": digest},
        },
    )

    head = client.head_object(Bucket=bucket, Key=key)
    remote = head["ContentLength"]
    if remote != size:
        print(f"ERROR: size mismatch after upload: local={size} remote={remote}",
              file=sys.stderr)
        return False
    print(f"uploaded s3://{bucket}/{key} ({size} bytes, sha256={digest[:16]}…)")
    return True


def fetch(name, dest):
    """Download a dump (or 'latest') and verify its sha256 against the object."""
    client, bucket = _client()
    if name == "latest":
        rows = list_backups(client, bucket)
        if not rows:
            raise SystemExit("ERROR: no backups in the bucket")
        key = rows[0][0]
    else:
        key = name if name.startswith(PREFIX) else PREFIX + name

    os.makedirs(dest, exist_ok=True)
    out = os.path.join(dest, os.path.basename(key))
    head = client.head_object(Bucket=bucket, Key=key)
    expected = (head.get("Metadata") or {}).get("sha256")

    print(f"downloading s3://{bucket}/{key} -> {out}")
    client.download_file(bucket, key, out)

    if expected:
        actual = _sha256(out)
        if actual != expected:
            print(f"ERROR: sha256 mismatch! expected {expected} got {actual}",
                  file=sys.stderr)
            return None
        print(f"sha256 OK ({actual[:16]}…)")
    else:
        # Pre-metadata objects: fall back to the byte count.
        if os.path.getsize(out) != head["ContentLength"]:
            print("ERROR: size mismatch", file=sys.stderr)
            return None
        print("no sha256 metadata on object; size OK")
    # Last line, machine-readable: restore_mysql.sh reads the path from here
    # rather than guessing at the newest file in the directory.
    print(f"PATH={out}")
    return out


def _keepers(rows, daily, monthly):
    """Which keys survive: newest `daily` dumps + earliest of each of the last
    `monthly` calendar months.

    The month comes from the timestamp in the key rather than the object's
    LastModified, so a re-uploaded object doesn't silently change bucket.
    """
    keep = {k for k, _, _ in rows[:daily]}
    by_month = {}
    for key, stamp, _ in rows:
        by_month.setdefault((stamp.year, stamp.month), []).append((stamp, key))
    for month in sorted(by_month, reverse=True)[:monthly]:
        # earliest dump in that month
        keep.add(min(by_month[month])[1])
    return keep


def prune(daily=DAILY_KEEP, monthly=MONTHLY_KEEP, dry_run=False):
    client, bucket = _client()
    rows = list_backups(client, bucket)
    keep = _keepers(rows, daily, monthly)
    doomed = [k for k, _, _ in rows if k not in keep]

    if not doomed:
        print(f"prune: {len(rows)} backup(s), nothing to remove "
              f"(keep {daily} daily + {monthly} monthly)")
        return 0
    for key in doomed:
        if dry_run:
            print(f"prune: would delete {key}")
        else:
            client.delete_object(Bucket=bucket, Key=key)
            print(f"prune: deleted {key}")
    print(f"prune: kept {len(keep)} of {len(rows)}")
    return len(doomed)


def _human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    p_up = sub.add_parser("upload")
    p_up.add_argument("path")
    p_up.add_argument("--key")

    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("name")
    p_fetch.add_argument("--dest", required=True)

    p_prune = sub.add_parser("prune")
    p_prune.add_argument("--daily", type=int, default=DAILY_KEEP)
    p_prune.add_argument("--monthly", type=int, default=MONTHLY_KEEP)
    p_prune.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()

    if args.cmd == "list":
        rows = list_backups()
        if not rows:
            print("(no backups)")
            return 0
        total = 0
        for key, stamp, size in rows:
            total += size
            print(f"{stamp:%Y-%m-%d %H:%M:%S}  {_human(size):>7}  {key}")
        print(f"-- {len(rows)} backup(s), {_human(total)} total")
        return 0

    if args.cmd == "upload":
        return 0 if upload(args.path, args.key) else 1

    if args.cmd == "fetch":
        return 0 if fetch(args.name, args.dest) else 1

    if args.cmd == "prune":
        prune(args.daily, args.monthly, args.dry_run)
        return 0

    return 1


if __name__ == "__main__":
    # Keep cron logs readable: a bucket that is missing, unreachable or
    # forbidden is an operational fact, not a stack trace.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        detail = f"{code}: {exc}" if code else f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {detail}", file=sys.stderr)
        sys.exit(1)
