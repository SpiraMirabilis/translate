#!/usr/bin/env python3
"""Download remote <img src="http…"> illustrations referenced by a flat-text raw.

Some raws (sfacg rips in particular) carry the author's own inline artwork as a
bare HTML tag in the middle of the prose::

    　　（衣服参考图）
    　　<img src="https://rss.sfacg.com/web/novel/images/UploadPic/…/xxx.jpg">

The FB2/EPUB importers hand images to `IllustrationCollector` because the bytes
are already inside the container. A flat text file only has the URL, so this
script does the fetching half: it downloads each distinct URL into a local cache
directory and writes a manifest. `queue_flat_text.py --illustrations <manifest>`
then swaps the <img> lines for ⟦IMG:id⟧ markers and persists them exactly the
way fb2_processor does.

Splitting fetch from ingest means the network hit happens once, the images can
be eyeballed before anything reaches the database, and re-runs are offline.

Usage:
    python3 fetch_remote_illustrations.py --file ~/scientist.clean.txt
    python3 fetch_remote_illustrations.py --file ~/scientist.clean.txt --out-dir /tmp/sci-img
"""

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx

from illustrations import ext_for_mime, _looks_decorative

IMG_RE = re.compile(r'<img\b[^>]*?\bsrc\s*=\s*["\']([^"\']+)["\'][^>]*>', re.I)

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
    'Referer': 'https://book.sfacg.com/',
}


def find_images(lines):
    """Yield (line_index, url, alt) for every <img> in the file.

    alt = the nearest preceding non-blank, non-<img> line, which in these raws is
    the author's own caption ("（衣服参考图）", "PS:ai图", "这是 麦子kami 画的樱空").
    """
    for i, line in enumerate(lines):
        for url in IMG_RE.findall(line):
            alt = None
            for j in range(i - 1, max(-1, i - 4), -1):
                cand = lines[j].strip()
                if cand and '<img' not in cand:
                    alt = cand
                    break
            yield i, url, alt


def main():
    ap = argparse.ArgumentParser(description='Fetch remote illustrations from a flat-text raw.')
    ap.add_argument('--file', required=True)
    ap.add_argument('--out-dir', default=None,
                    help='Cache directory (default: <file>.illus/)')
    ap.add_argument('--manifest', default=None,
                    help='Manifest path (default: <out-dir>/manifest.json)')
    ap.add_argument('--timeout', type=float, default=30.0)
    ap.add_argument('--dry-run', action='store_true', help='List images; download nothing.')
    args = ap.parse_args()

    src = os.path.expanduser(args.file)
    out_dir = os.path.expanduser(args.out_dir) if args.out_dir else src.replace('.txt', '') + '.illus'
    manifest_path = args.manifest or os.path.join(out_dir, 'manifest.json')

    with open(src, encoding='utf-8') as f:
        lines = f.read().splitlines()

    found = list(find_images(lines))
    urls = []
    for _, url, alt in found:
        if url not in [u for u, _ in urls]:
            urls.append((url, alt))

    print(f'{len(found)} <img> tag(s), {len(urls)} distinct URL(s) in {src}')
    for i, url, alt in found:
        print(f'  line {i+1}: {url}')
        print(f'            alt: {alt!r}')

    if args.dry_run:
        print('\n[DRY RUN] nothing downloaded')
        return

    os.makedirs(out_dir, exist_ok=True)
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)

    ok = failed = skipped = 0
    with httpx.Client(timeout=args.timeout, follow_redirects=True, headers=HEADERS) as client:
        for url, alt in urls:
            if url in manifest and os.path.exists(os.path.join(out_dir, manifest[url]['filename'])):
                skipped += 1
                continue
            try:
                r = client.get(url)
                r.raise_for_status()
                data = r.content
            except Exception as e:
                print(f'  FAILED {url}: {type(e).__name__}: {e}', file=sys.stderr)
                failed += 1
                continue

            mime = r.headers.get('content-type', '').split(';')[0].strip()
            ext = ext_for_mime(mime)
            digest = hashlib.sha1(data).hexdigest()
            filename = f'{digest[:12]}{ext}'
            path = os.path.join(out_dir, filename)
            with open(path, 'wb') as f:
                f.write(data)

            decorative = _looks_decorative(data, ext)
            manifest[url] = {
                'filename': filename,
                'sha1': digest,
                'bytes': len(data),
                'mime': mime,
                'ext': ext,
                'alt': alt,
                'decorative': bool(decorative),
            }
            flag = '  (would be FILTERED as decorative)' if decorative else ''
            print(f'  ok {len(data):>8} B  {mime:<12} -> {filename}{flag}')
            ok += 1

    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f'\ndownloaded {ok}, cached {skipped}, failed {failed}')
    print(f'images   : {out_dir}')
    print(f'manifest : {manifest_path}')
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
