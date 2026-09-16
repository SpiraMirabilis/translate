#!/usr/bin/env python3
"""Report whether a chunk of Chinese text is predominantly traditional or simplified.

Reads stdin by default, or a file given as an argument:

    python3 script_ratio.py < chapter.txt
    python3 script_ratio.py chapter.txt

Classification is per character, using OpenCC as an oracle:

* run the character through ``t2s`` — if it changes, only traditional writes it
  that way (發 → 发), so the character is **traditional-only**;
* otherwise run it through ``s2t`` — if it changes, only simplified writes it
  that way (发 → 發), so the character is **simplified-only**;
* otherwise both scripts spell it identically (的, 我, 山) — **shared**.

Shared characters are the large majority of any real text, so the verdict is
decided on the *decisive* characters alone (traditional-only vs simplified-only)
and both percentages are reported against both denominators.

Caveat on the simplified count: simplification merged several traditional
characters into one (臺/檯/颱 → 台, 裡/裏 → 里, 後/后 → 后), so ``s2t`` expands
those and this script calls them simplified-only — even though traditional text
legitimately writes 台 (the bound morpheme), 里 (the distance unit) and 后
(empress). Genuinely traditional text therefore lands near 98%, not 100%; the
sample list at the bottom is there so you can see which characters the
remainder is, and a mixed verdict driven entirely by 台/里/后/干 is not mixed.
"""

import argparse
import sys
import unicodedata

# Unihan blocks that count as CJK for this tally. Kana/Hangul are deliberately
# excluded — they are neither script and belong in the non-CJK bucket.
CJK_RANGES = (
    (0x3400, 0x4DBF),    # Ext A
    (0x4E00, 0x9FFF),    # URO
    (0xF900, 0xFAFF),    # Compatibility ideographs
    (0x20000, 0x2A6DF),  # Ext B
    (0x2A700, 0x2EBEF),  # Ext C-F
    (0x2F800, 0x2FA1F),  # Compatibility supplement
    (0x30000, 0x323AF),  # Ext G-H
)

TRADITIONAL = "traditional"
SIMPLIFIED = "simplified"
SHARED = "shared"


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in CJK_RANGES)


def make_classifier():
    """Return a cached per-character classifier backed by OpenCC."""
    try:
        import opencc
    except ImportError:
        sys.exit(
            "OpenCC is not installed. Install it with:\n"
            "  sudo apt install python3-opencc libopencc1.1 libopencc-data\n"
            "(or: pip install OpenCC)"
        )

    t2s = opencc.OpenCC("t2s")
    s2t = opencc.OpenCC("s2t")
    cache: dict[str, str] = {}

    def classify(ch: str) -> str:
        verdict = cache.get(ch)
        if verdict is None:
            if t2s.convert(ch) != ch:
                verdict = TRADITIONAL
            elif s2t.convert(ch) != ch:
                verdict = SIMPLIFIED
            else:
                verdict = SHARED
            cache[ch] = verdict
        return verdict

    return classify


def analyze(text: str) -> dict:
    classify = make_classifier()

    counts = {TRADITIONAL: 0, SIMPLIFIED: 0, SHARED: 0}
    samples = {TRADITIONAL: {}, SIMPLIFIED: {}}
    non_cjk = {"whitespace": 0, "latin": 0, "punctuation": 0, "other": 0}

    for ch in text:
        if is_cjk(ch):
            verdict = classify(ch)
            counts[verdict] += 1
            if verdict in samples:
                samples[verdict][ch] = samples[verdict].get(ch, 0) + 1
        elif ch.isspace():
            non_cjk["whitespace"] += 1
        elif ch.isascii() and ch.isalnum():
            non_cjk["latin"] += 1
        elif unicodedata.category(ch).startswith("P"):
            non_cjk["punctuation"] += 1
        else:
            non_cjk["other"] += 1

    return {
        "total": len(text),
        "cjk": sum(counts.values()),
        "counts": counts,
        "samples": samples,
        "non_cjk": non_cjk,
    }


def pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):5.1f}%" if total else "    --"


def top(sample: dict, limit: int) -> str:
    ranked = sorted(sample.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return "  ".join(f"{ch}×{n}" for ch, n in ranked)


def report(stats: dict, show_samples: int) -> str:
    total = stats["total"]
    cjk = stats["cjk"]
    c = stats["counts"]
    decisive = c[TRADITIONAL] + c[SIMPLIFIED]
    non_cjk_total = sum(stats["non_cjk"].values())

    lines = []
    lines.append(f"Total characters:      {total:>8,}")
    lines.append(f"CJK characters:        {cjk:>8,}  ({pct(cjk, total)} of text)")
    lines.append("")
    lines.append("Of CJK characters:                     of all CJK   of decisive")
    lines.append(
        f"  traditional-only:    {c[TRADITIONAL]:>8,}      "
        f"{pct(c[TRADITIONAL], cjk)}        {pct(c[TRADITIONAL], decisive)}"
    )
    lines.append(
        f"  simplified-only:     {c[SIMPLIFIED]:>8,}      "
        f"{pct(c[SIMPLIFIED], cjk)}        {pct(c[SIMPLIFIED], decisive)}"
    )
    lines.append(
        f"  shared (same in both):{c[SHARED]:>7,}      {pct(c[SHARED], cjk)}"
    )
    lines.append("")
    lines.append(f"Non-CJK characters:    {non_cjk_total:>8,}  ({pct(non_cjk_total, total)} of text)")
    for label, n in stats["non_cjk"].items():
        lines.append(f"  {label + ':':<20} {n:>8,}  ({pct(n, total)} of text)")
    lines.append("")

    if cjk == 0:
        lines.append("Verdict: no CJK characters found.")
    elif decisive == 0:
        lines.append(
            "Verdict: indeterminate — every CJK character is spelled identically "
            "in both scripts."
        )
    else:
        trad_share = c[TRADITIONAL] / decisive
        if trad_share >= 0.9:
            verdict = "TRADITIONAL"
        elif trad_share <= 0.1:
            verdict = "SIMPLIFIED"
        elif trad_share >= 0.5:
            verdict = "MIXED, leaning traditional"
        else:
            verdict = "MIXED, leaning simplified"
        lines.append(
            f"Verdict: {verdict}  "
            f"({pct(c[TRADITIONAL], decisive)} traditional / "
            f"{pct(c[SIMPLIFIED], decisive)} simplified of "
            f"{decisive:,} decisive characters)"
        )

    if show_samples:
        for label, key in (("traditional", TRADITIONAL), ("simplified", SIMPLIFIED)):
            if stats["samples"][key]:
                lines.append("")
                lines.append(f"Most common {label}-only characters:")
                lines.append("  " + top(stats["samples"][key], show_samples))

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Detect whether text is predominantly traditional or simplified Chinese.",
    )
    ap.add_argument("file", nargs="?", help="file to read (default: stdin)")
    ap.add_argument(
        "--samples",
        type=int,
        default=10,
        metavar="N",
        help="show the N most common decisive characters of each script (0 to hide)",
    )
    args = ap.parse_args()

    if args.file:
        with open(args.file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        if sys.stdin.isatty():
            print("Reading from stdin — paste text, then Ctrl-D:", file=sys.stderr)
        text = sys.stdin.read()

    print(report(analyze(text), args.samples))


if __name__ == "__main__":
    main()
