#!/usr/bin/env python3
"""Delete orphan ai-title-only session files left behind by the claudecode provider.

Background:
The `claudecode` translation provider passes `--no-session-persistence` to the
Claude Code CLI, which suppresses the main conversation transcript but does NOT
suppress a background AI-title-generation prompt. That prompt writes a one-line
`{"type":"ai-title",...}` jsonl per translation call into
`~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. Over thousands of chapters
these files accumulate in `claude --resume`, evicting real conversations.

`providers/claude_code_provider.py` sweeps them automatically, but the deferred
timer relies on the t9 process staying alive, and stuff slips through. This
script is the manual fallback — run it any time the list grows uncomfortable.

Usage:
    python3 clean_claude_code_orphans.py                   # all project dirs
    python3 clean_claude_code_orphans.py --dry-run         # show what would go
    python3 clean_claude_code_orphans.py --project=-home-mdm-t9   # note the '='
    python3 clean_claude_code_orphans.py --min-age 0       # delete fresh ones too
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


PROJECTS_DIR = Path.home() / ".claude" / "projects"
MAX_FILE_BYTES = 1024  # ai-title lines run ~130 bytes; cap well below real sessions


def file_is_ai_title_only(path: Path) -> bool:
    """True iff every non-blank line in the file is an ai-title event."""
    try:
        with path.open("r", encoding="utf-8") as f:
            saw_line = False
            for line in f:
                line = line.strip()
                if not line:
                    continue
                saw_line = True
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return False
                if obj.get("type") != "ai-title":
                    return False
            return saw_line
    except OSError:
        return False


def sweep_project(project_dir: Path, min_age_sec: float, dry_run: bool) -> tuple[int, int]:
    """Returns (deleted, scanned) counts for one project dir."""
    cutoff = time.time() - min_age_sec
    deleted = 0
    scanned = 0
    for entry in os.scandir(project_dir):
        if not entry.name.endswith(".jsonl"):
            continue
        scanned += 1
        try:
            st = entry.stat()
        except OSError:
            continue
        if st.st_size > MAX_FILE_BYTES:
            continue
        if st.st_mtime > cutoff:
            continue
        path = Path(entry.path)
        if not file_is_ai_title_only(path):
            continue
        if dry_run:
            try:
                preview = path.read_text(encoding="utf-8").strip()
            except OSError:
                preview = "(unreadable)"
            print(f"  would delete {entry.name}: {preview[:120]}")
        else:
            try:
                path.unlink()
            except OSError as e:
                print(f"  could not delete {entry.name}: {e}", file=sys.stderr)
                continue
        deleted += 1
    return deleted, scanned


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--project",
        help="Limit to one project dir (e.g. -home-mdm-t9). Default: all under ~/.claude/projects.",
    )
    parser.add_argument(
        "--min-age",
        type=float,
        default=30.0,
        help="Skip files younger than this many seconds (default: 30, matches provider's safety cutoff).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted, change nothing.")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-project output, just print totals.")
    args = parser.parse_args()

    if not PROJECTS_DIR.is_dir():
        print(f"No projects directory at {PROJECTS_DIR}", file=sys.stderr)
        return 1

    if args.project:
        target = PROJECTS_DIR / args.project
        if not target.is_dir():
            print(f"No such project dir: {target}", file=sys.stderr)
            return 1
        projects = [target]
    else:
        projects = [p for p in PROJECTS_DIR.iterdir() if p.is_dir()]

    total_deleted = 0
    total_scanned = 0
    for proj in sorted(projects):
        deleted, scanned = sweep_project(proj, args.min_age, args.dry_run)
        total_deleted += deleted
        total_scanned += scanned
        if not args.quiet and scanned > 0:
            verb = "would delete" if args.dry_run else "deleted"
            print(f"{proj.name}: {verb} {deleted}/{scanned} jsonl files")

    verb = "Would delete" if args.dry_run else "Deleted"
    print(f"\n{verb} {total_deleted} of {total_scanned} jsonl files across {len(projects)} project(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
