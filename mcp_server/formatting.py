"""Output helpers shared by the tools: pagination, truncation, JSON."""
from __future__ import annotations

import json
from typing import Any, Sequence

# A tool result much past this crowds the caller's context for little gain;
# list tools paginate, free-text tools truncate with a pointer to narrow.
CHARACTER_LIMIT = 25_000


def json_out(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


def paginate(items: Sequence, limit: int, offset: int) -> tuple[list, dict]:
    total = len(items)
    page = list(items[offset:offset + limit])
    has_more = offset + len(page) < total
    meta = {
        "total": total,
        "count": len(page),
        "offset": offset,
        "has_more": has_more,
        "next_offset": offset + len(page) if has_more else None,
    }
    return page, meta


def page_footer(meta: dict) -> str:
    if not meta["total"]:
        return ""
    start = meta["offset"] + 1 if meta["count"] else meta["offset"]
    line = f"— {start}–{meta['offset'] + meta['count']} of {meta['total']}"
    if meta["has_more"]:
        line += f"; pass offset={meta['next_offset']} for more"
    return line


def truncate(text: str, limit: int = CHARACTER_LIMIT,
             hint: str = "narrow the query (chapters, pattern) or lower max_hits") -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    cut = cut if cut > limit // 2 else limit
    return (text[:cut] + f"\n\n[truncated: {len(text) - cut:,} more characters — {hint}]")
