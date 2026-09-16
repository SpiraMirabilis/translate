#!/usr/bin/env python3
"""
One-off migration: upload existing local cover + illustration files to Spaces.

Mirrors each local file under <script_dir>/covers/ and <script_dir>/illustrations/
to its object key (prefix/<rel>), skipping objects that already exist. Run once
after enabling Spaces so direct CDN URLs resolve for already-imported books.

Usage:
    python3 backfill_spaces.py --dry-run     # show what would upload
    python3 backfill_spaces.py               # upload missing objects
    python3 backfill_spaces.py --force       # re-upload even if present
"""
import argparse
import os
import sys

import spaces
from config import TranslationConfig


def _regen_cover_derivatives(config):
    """Ensure thumb + medium derivatives exist (and upload) for every full cover.

    Pre-existing covers were saved before the medium tier existed, so generate
    any missing derivatives before the upload walk picks them up.
    """
    import re
    import cover_images
    covers_dir = os.path.join(config.script_dir, "covers")
    if not os.path.isdir(covers_dir):
        return
    made = 0
    for name in os.listdir(covers_dir):
        stem, ext = os.path.splitext(name)
        # Full covers are '<book_id>.<ext>' — skip derivative webps.
        if not re.fullmatch(r"\d+", stem) or ext.lower() == ".webp":
            continue
        book_id = int(stem)
        book = {"id": book_id, "cover_image": f"covers/{name}"}
        for kind in cover_images.SIZES:
            rel = cover_images.derivative_relpath(book_id, kind)
            if not os.path.exists(os.path.join(config.script_dir, rel)):
                if cover_images.ensure_derivative(config, book, kind):
                    made += 1
    if made:
        print(f"generated {made} missing cover derivatives")


def _iter_media(script_dir):
    """Yield (abs_path, rel_path) for every file under covers/ and illustrations/."""
    for sub in ("covers", "illustrations"):
        base = os.path.join(script_dir, sub)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for name in files:
                abs_path = os.path.join(root, name)
                rel_path = os.path.relpath(abs_path, script_dir)
                yield abs_path, rel_path


def main():
    ap = argparse.ArgumentParser(description="Backfill local cover/illustration files to Spaces.")
    ap.add_argument("--dry-run", action="store_true", help="List actions without uploading")
    ap.add_argument("--force", action="store_true", help="Upload even if the object already exists")
    args = ap.parse_args()

    config = TranslationConfig()
    # Allow running the backfill even if the live toggle is still off — we just
    # need credentials present.
    config.spaces_enabled = True
    if not spaces.is_enabled(config):
        print("ERROR: Spaces credentials missing (BUCKET_ACCESS_ID / BUCKET_SECRET in .env).")
        return 1

    # Generate any missing cover derivatives first so the walk uploads them too.
    if not args.dry_run:
        _regen_cover_derivatives(config)

    uploaded = skipped = failed = 0
    for abs_path, rel_path in _iter_media(config.script_dir):
        key = spaces.key_for(config, rel_path)
        if not args.force and spaces.exists(config, key):
            skipped += 1
            continue
        if args.dry_run:
            print(f"WOULD UPLOAD {rel_path} -> {key}")
            uploaded += 1
            continue
        if spaces.upload(config, abs_path, key):
            print(f"uploaded {rel_path} -> {key}")
            uploaded += 1
        else:
            print(f"FAILED   {rel_path}")
            failed += 1

    verb = "would upload" if args.dry_run else "uploaded"
    print(f"\nDone: {verb} {uploaded}, skipped (already present) {skipped}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
