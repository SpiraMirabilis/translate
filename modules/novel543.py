"""novel543 module — strips source-site boilerplate from novel543.com raws.

The boilerplate (formerly stripped unconditionally for every book in
``database.py``) is specific to novel543.com:
  * "warm reminder" notices opening with 温馨提示 / 溫馨提示 (simp/trad) + colon.
  * VIP / ad-free membership ad blocks: an illustration marker ⟦IMG:id⟧ on its
    own line, followed by a "现推出VIP会员免广告功能" line and a "点击查看" line.

This module owns that logic; it only runs for books whose source_url matches
novel543.com (or where the user force-enables it).
"""
import re

from .base import TranslationModule

_REMINDER_AD_RE = re.compile(r'^\s*(?:温馨提示|溫馨提示)\s*[:：]')
_IMG_MARKER_RE = re.compile(r'^\s*⟦IMG:[0-9a-f]+⟧\s*$')
_CLICK_AD_LINES = {"点击查看", "点击查看。", "點擊查看", "Click to view", "Click to view."}


def _is_vip_ad(line):
    if not isinstance(line, str):
        return False
    low = line.lower()
    return "vip" in low and ("免广告" in line or "免廣告" in line
                             or "ad-free" in low or "ad free" in low)


def _boilerplate_drop_indices(lines):
    """Indices of boilerplate source lines to drop: reminder notices, VIP/ad-free
    ad text, "click to view" lines, and an ⟦IMG⟧ marker that heads such an ad."""
    drop = set()
    n = len(lines)
    for i, l in enumerate(lines):
        if not isinstance(l, str):
            continue
        if _REMINDER_AD_RE.match(l) or _is_vip_ad(l) or l.strip() in _CLICK_AD_LINES:
            drop.add(i)
        elif _IMG_MARKER_RE.match(l):
            # Drop an image marker only when the next non-blank line is a VIP/ad-free
            # ad — never a genuine in-chapter illustration.
            j = i + 1
            while j < n and isinstance(lines[j], str) and not lines[j].strip():
                j += 1
            if j < n and _is_vip_ad(lines[j]):
                drop.add(i)
    return drop


def strip_boilerplate_lines(content):
    """Drop novel543 source-site boilerplate from source content.

    Accepts a list of lines or a newline-joined string and returns the same
    type with the boilerplate removed. Idempotent.
    """
    if isinstance(content, list):
        drop = _boilerplate_drop_indices(content)
        return [l for i, l in enumerate(content) if i not in drop]
    if isinstance(content, str):
        lines = content.split('\n')
        drop = _boilerplate_drop_indices(lines)
        return '\n'.join(l for i, l in enumerate(lines) if i not in drop)
    return content


class Novel543Module(TranslationModule):
    id = "novel543"
    name = "Novel543"
    description = ("Strip novel543.com reminder notices (温馨提示) and VIP/ad-free "
                   "ad blocks (⟦IMG⟧ + VIP membership + 点击查看) from source text.")
    auto_url_patterns = ["novel543.com"]

    def transform_source_lines(self, content, ctx):
        return strip_boilerplate_lines(content)
