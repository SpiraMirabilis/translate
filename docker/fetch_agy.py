"""Build-time helper: download the Antigravity CLI (`agy`) for this image's
architecture from Google's public release bucket, verify its SHA-256, and
install the binary at the given path.

    python fetch_agy.py <version> <targetarch> <sha256-amd64> <sha256-arm64> <dest>

<version> is the release id as it appears in the bucket path, e.g.
1.5.12-5281398513336320. A checksum mismatch fails the build.
"""
import hashlib
import io
import os
import sys
import tarfile
import urllib.request

BASE = "https://storage.googleapis.com/antigravity-public/antigravity-cli"
ASSETS = {
    "amd64": ("linux-x64", "cli_linux_x64.tar.gz"),
    "arm64": ("linux-arm", "cli_linux_arm64.tar.gz"),
}


def main():
    version, arch, sha_amd64, sha_arm64, dest = sys.argv[1:6]
    if arch not in ASSETS:
        sys.exit(f"agy: unsupported architecture {arch!r} (have: {', '.join(ASSETS)})")
    folder, name = ASSETS[arch]
    want = {"amd64": sha_amd64, "arm64": sha_arm64}[arch].lower()
    url = f"{BASE}/{version}/{folder}/{name}"
    print(f"agy: fetching {url}", flush=True)
    with urllib.request.urlopen(url, timeout=300) as r:
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        sys.exit(f"agy: checksum mismatch for {name}: expected {want}, got {got}")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = next((m for m in tar.getmembers()
                       if m.isfile() and os.path.basename(m.name) == "antigravity"), None)
        if member is None:
            sys.exit("agy: no 'antigravity' binary in the release tarball")
        blob = tar.extractfile(member).read()
    with open(dest, "wb") as f:
        f.write(blob)
    os.chmod(dest, 0o755)
    print(f"agy: installed {dest} ({len(blob) // (1 << 20)} MiB)")


if __name__ == "__main__":
    main()
