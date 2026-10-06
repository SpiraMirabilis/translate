"""Usage log: one JSON line per tool call, to see how often (and for what) the
tools are actually used — above all by the translation model on the read-only
server.

File: logs/mcp_usage.log (JSON lines; gitignored by *.log). MCP_USAGE_LOG
overrides the path; MCP_USAGE_LOG=off disables it. Summarise with
`python3 -m mcp_server.usage [--days N]`.

The claudecode provider tags its calls with X-T9-Caller / X-T9-Book /
X-T9-Chapter headers, so each lookup records which chapter it was made for.
"""
import datetime
import json
import os
import threading
from typing import Any, Optional

from .deps import REPO_ROOT

_lock = threading.Lock()
MAX_ARG_CHARS = 200


def log_path() -> Optional[str]:
    path = os.environ.get("MCP_USAGE_LOG", os.path.join(REPO_ROOT, "logs", "mcp_usage.log"))
    return None if path.strip().lower() in ("", "off", "0", "none") else path


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_ARG_CHARS:
        return value[:MAX_ARG_CHARS] + f"…(+{len(value) - MAX_ARG_CHARS})"
    if isinstance(value, list):
        return [_short(v) for v in value[:20]] + ([f"…(+{len(value) - 20})"] if len(value) > 20 else [])
    if isinstance(value, dict):
        return {k: _short(v) for k, v in value.items()}
    return value


def record(entry: dict) -> None:
    """Append one call. Never raises — logging must not fail a tool call."""
    path = log_path()
    if not path:
        return
    try:
        entry = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), **entry}
        if "args" in entry:
            entry["args"] = _short(entry["args"])
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


def caller_headers(ctx) -> dict:
    """X-T9-* headers of the HTTP request behind this call ({} over stdio)."""
    try:
        request = ctx.request_context.request
        headers = getattr(request, "headers", None) or {}
    except Exception:
        return {}
    out = {}
    for key, name in (("x-t9-caller", "caller"), ("x-t9-book", "book"),
                      ("x-t9-chapter", "chapter")):
        value = headers.get(key)
        if value:
            out[name] = int(value) if value.isdigit() else value
    return out


def _load(path: str, since: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("ts", "") >= since:
                rows.append(row)
    return rows


def _tally(rows, key_fn) -> list:
    out = {}
    for r in rows:
        k = key_fn(r)
        out[k] = out.get(k, 0) + 1
    return sorted(out.items(), key=lambda kv: (-kv[1], str(kv[0])))


def summarise(path: str, days: Optional[int] = None) -> str:
    """Tool calls per caller and per tool, and — for the translation model —
    lookups per book and how many chapters made any.

    Lines with "event": "connect" (written 2026-09-29 only, since dropped) are
    skipped.
    """
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat() if days else ""
    calls = [r for r in _load(path, since) if r.get("event") != "connect"]
    if not calls:
        return "No tool calls logged" + (f" in the last {days} day(s)." if days else ".")

    lines = [f"{calls[0]['ts']} → {calls[-1]['ts']}: {len(calls)} tool call(s)",
             "", "By caller:"]
    lines += [f"  {n:6}  {k}" for k, n in
              _tally(calls, lambda r: f"{r.get('mode')}/{r.get('caller') or 'interactive'}")]
    lines += ["", "By tool:"]
    lines += [f"  {n:6}  {k}" for k, n in _tally(calls, lambda r: r.get("tool"))]
    errors = [r for r in calls if not r.get("ok")]
    if errors:
        lines += ["", f"Errors: {len(errors)}"]

    tl = [r for r in calls if r.get("caller") == "translation"]
    if tl:
        chapters = {(r.get("book"), r.get("chapter")) for r in tl}
        lines += ["", f"Translation model: {len(tl)} lookup(s) over {len(chapters)} chapter(s) "
                      f"({len(tl) / len(chapters):.1f} per chapter that used the tools)",
                  "", "  book  lookups  chapters"]
        for b in sorted({b for b, _ in chapters}, key=lambda b: (b is None, b)):
            n = sum(1 for r in tl if r.get("book") == b)
            chs = {c for bb, c in chapters if bb == b}
            lines.append(f"  {str(b):>4}  {n:7}  {len(chs):8}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Summarise logs/mcp_usage.log")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--file", default=None)
    a = p.parse_args()
    target = a.file or log_path()
    if not target or not os.path.exists(target):
        raise SystemExit(f"No usage log at {target}")
    print(summarise(target, a.days))
