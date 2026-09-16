#!/usr/bin/env python3
"""Read and write a book's customised footnote-scanning prompt.

The footnote candidate scanner's RULES are per-book: the ``footnote_scan``
module's "Scan system prompt" setting (``system_prompt``) replaces the built-in
rules wholesale — blank means "use the built-in ones", which are the contents of
prompts/footnote_scan_prompt.txt. The same stored rules are used by the on-ingest
module, the inline (scan_mode=translation) scan folded into the translation call,
and footnote_scan.py. This script is the CLI for that one setting, for the days
when the Book → Modules → Footnote Candidate Scanner modal is not where you are.

What is NOT yours to set: the OUTPUT paragraph. footnote_scan_core appends it
from code (a different one for a standalone scan and an inline one), because the
parser and the hallucination filter depend on its shape. An output section left
in a customised prompt is stripped at assembly time — --effective shows exactly
what the model will be sent.

Usage:
    python3 footnote_scan_prompt.py -b 90                    # status
    python3 footnote_scan_prompt.py -b 90 --get              # stored rules (stdout)
    python3 footnote_scan_prompt.py -b 90 --effective        # rules + output paragraph
    python3 footnote_scan_prompt.py -b 90 --effective --inline
    python3 footnote_scan_prompt.py --stock                  # the built-in rules
    python3 footnote_scan_prompt.py -b 90 --diff             # stored vs built-in
    python3 footnote_scan_prompt.py -b 90 --set /tmp/rules.txt
    cat rules.txt | python3 footnote_scan_prompt.py -b 90 --set -
    python3 footnote_scan_prompt.py -b 90 --edit             # $EDITOR on a copy
    python3 footnote_scan_prompt.py -b 90 --clear            # back to built-in
    python3 footnote_scan_prompt.py --list                   # books with an override

Writes go through the same path the web API uses (modules.apply_module_settings_change),
so a book with a module backfill in flight is refused rather than raced, and the
module's OTHER settings (scan model, scan_mode) are merged, never clobbered —
set_module_settings replaces the whole per-module row set.
"""

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
os.environ.pop("DEBUG", None)

from footnote_scan_core import (MODE_INLINE, MODE_SETTING, PROMPT_SETTING,
                                SCAN_MODULE_ID, resolve_system_prompt,
                                scan_rules, stock_scan_rules)


def load_stored(db, book_id):
    """Raw stored settings for the footnote_scan module: ``{key: value}``."""
    return db.get_module_settings(book_id, SCAN_MODULE_ID) or {}


def stored_prompt(stored):
    """The book's custom rules, or "" when it has no override."""
    return str(stored.get(PROMPT_SETTING) or "").strip()


def allowed_keys():
    """Setting keys the module actually declares (unknown keys are dropped, as
    the API's _normalize_module_settings does)."""
    from modules import REGISTRY
    return {f["key"] for f in REGISTRY[SCAN_MODULE_ID].settings_schema}


def write_prompt(db, book, text, config, logger):
    """Persist ``text`` as the book's custom scan prompt (empty = clear it).

    The module's other stored settings are carried over: set_module_settings is
    authoritative for the whole (book, module) pair, so a partial write would
    silently drop the book's scan model and scan_mode.
    """
    from modules import ModuleTaskBusyError, apply_module_settings_change

    book_id = book["id"]
    settings = {k: v for k, v in load_stored(db, book_id).items()
                if k in allowed_keys()}
    text = (text or "").strip()
    if text:
        settings[PROMPT_SETTING] = text
    else:
        settings.pop(PROMPT_SETTING, None)
    try:
        ok = apply_module_settings_change(
            db, book, SCAN_MODULE_ID, settings, config, logger)
    except ModuleTaskBusyError as e:
        print(f"Refused: {e}", file=sys.stderr)
        return False
    if not ok:
        print("Failed to save module settings (see log).", file=sys.stderr)
    return ok


def read_new_text(spec):
    """Read the replacement prompt from a file path, or stdin for '-'."""
    if spec == "-":
        return sys.stdin.read()
    with open(spec, "r", encoding="utf-8") as f:
        return f.read()


def lint(text):
    """Warnings about a prompt about to be stored. Returns a list of strings."""
    problems = []
    if "ANCHORING" not in text.upper():
        problems.append(
            "no ANCHORING rule — the hallucination filter drops every candidate "
            "the model cannot point at in the source, so a prompt that does not "
            "ask for verbatim anchoring will mostly produce discarded rows.")
    if scan_rules(text) != text.strip():
        problems.append(
            "a trailing OUTPUT section was detected and will be stripped at "
            "assembly time (the output shape is code-owned) — see --effective.")
    return problems


def cmd_status(db, book, stored, enabled, settings):
    custom = stored_prompt(stored)
    print(f"Book {book['id']} — {book.get('title')}")
    print(f"  module enabled: {'yes' if enabled else 'no'}"
          + ("" if enabled else "  (prompt stored but unused)"))
    print(f"  scan_mode:      {settings.get(MODE_SETTING) or MODE_INLINE}"
          + ("" if stored.get(MODE_SETTING) else "  (default — no stored row)"))
    print(f"  scan model:     {settings.get('model')}"
          + ("" if (settings.get(MODE_SETTING) or MODE_INLINE) != MODE_INLINE
             else "  (unused: inline scans use the translation model)"))
    if custom:
        print(f"  scan prompt:    CUSTOM ({len(custom)} chars, "
              f"{len(custom.splitlines())} lines)")
        for w in lint(custom):
            print(f"    ! {w}")
    else:
        stock = stock_scan_rules()
        print(f"  scan prompt:    built-in ({len(stock)} chars, "
              f"{len(stock.splitlines())} lines)")
    return 0


def cmd_list(db):
    """Every book with a stored custom scan prompt."""
    rows = []
    with db._conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT s.book_id, b.title, s.value_json FROM book_module_settings s "
            "LEFT JOIN books b ON b.id = s.book_id "
            "WHERE s.module_id = ? AND s.setting_key = ? ORDER BY s.book_id",
            (SCAN_MODULE_ID, PROMPT_SETTING))
        rows = cursor.fetchall()
    if not rows:
        print("No book has a custom footnote scan prompt.")
        return 0
    stock = stock_scan_rules()
    for book_id, title, raw in rows:
        try:
            text = str(json.loads(raw) or "").strip()
        except (json.JSONDecodeError, TypeError):
            text = ""
        if not text:
            continue
        mark = " (identical to built-in)" if text == stock else ""
        print(f"{book_id:>4}  {len(text):>6} chars  {title or '?'}{mark}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Get/set a book's customised footnote scanning prompt.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("-b", "--book", help="Book id or exact title")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--get", action="store_true",
                      help="print the stored custom rules (exit 1 if there are none)")
    mode.add_argument("--effective", action="store_true",
                      help="print the complete prompt the model is sent "
                           "(rules + the code-owned output paragraph)")
    mode.add_argument("--stock", action="store_true",
                      help="print the built-in rules (no book needed)")
    mode.add_argument("--diff", action="store_true",
                      help="unified diff of the stored rules against the built-in ones")
    mode.add_argument("--set", metavar="PATH",
                      help="replace the prompt from a file ('-' for stdin); "
                           "empty input clears the override")
    mode.add_argument("--edit", action="store_true",
                      help="open the current prompt (or a copy of the built-in "
                           "one) in $EDITOR and save it if changed")
    mode.add_argument("--clear", action="store_true",
                      help="delete the override — back to the built-in rules")
    mode.add_argument("--list", action="store_true",
                      help="list every book with a custom scan prompt")
    parser.add_argument("--inline", action="store_true",
                        help="with --effective: the inline (scan_mode=translation) "
                             "form instead of the standalone one")
    parser.add_argument("--force", action="store_true",
                        help="save even when the prompt fails a lint check")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --set/--edit/--clear: show what would change, write nothing")
    args = parser.parse_args()

    from config import TranslationConfig
    from db import DatabaseManager
    from logger import Logger

    config = TranslationConfig()
    logger = Logger(config)
    writing = bool(args.set or args.edit or args.clear)
    db = DatabaseManager(config, logger, strict_writes=writing)

    if args.stock:
        print(stock_scan_rules())
        return 0
    if args.list:
        return cmd_list(db)

    if not args.book:
        parser.error("-b/--book is required (except with --stock and --list)")

    from get_entities import resolve_book
    book = resolve_book(db, args.book)
    if not book:
        print(f"Book not found: {args.book!r}", file=sys.stderr)
        return 1
    book_id = book["id"]

    from modules import module_config
    enabled, settings = module_config(book, SCAN_MODULE_ID, db=db)
    stored = load_stored(db, book_id)
    custom = stored_prompt(stored)

    if args.get:
        if not custom:
            print(f"Book {book_id} has no custom scan prompt (built-in in use).",
                  file=sys.stderr)
            return 1
        print(custom)
        return 0

    if args.effective:
        print(f"# {'custom' if custom else 'built-in'} rules + "
              f"{'inline' if args.inline else 'standalone'} output paragraph — "
              f"book {book_id} {book.get('title')}", file=sys.stderr)
        print(resolve_system_prompt(custom, inline=args.inline))
        return 0

    if args.diff:
        if not custom:
            print(f"Book {book_id} has no custom scan prompt (built-in in use).",
                  file=sys.stderr)
            return 1
        diff = list(difflib.unified_diff(
            stock_scan_rules().splitlines(), custom.splitlines(),
            fromfile="built-in", tofile=f"book {book_id}", lineterm=""))
        if not diff:
            print("Stored prompt is identical to the built-in rules.",
                  file=sys.stderr)
            return 0
        print("\n".join(diff))
        return 0

    if args.clear:
        if not custom:
            print(f"Book {book_id} already uses the built-in rules — nothing to clear.")
            return 0
        if args.dry_run:
            print(f"Would clear the {len(custom)}-char custom prompt on book {book_id}.")
            return 0
        if not write_prompt(db, book, "", config, logger):
            return 1
        print(f"Cleared the custom scan prompt on book {book_id} — built-in rules now apply.")
        return 0

    if args.set or args.edit:
        if args.set:
            new_text = read_new_text(args.set)
        else:
            editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
            seed = custom or stock_scan_rules()
            with tempfile.NamedTemporaryFile(
                    "w", suffix=f"-book{book_id}-scan-prompt.txt",
                    encoding="utf-8", delete=False) as tf:
                tf.write(seed)
                path = tf.name
            try:
                subprocess.call([*editor.split(), path])
                with open(path, "r", encoding="utf-8") as f:
                    new_text = f.read()
            finally:
                os.unlink(path)

        new_text = new_text.strip()
        if new_text == custom:
            print("No change.")
            return 0
        if not new_text:
            if args.dry_run:
                print(f"Would clear the custom prompt on book {book_id} (empty input).")
                return 0
            if not write_prompt(db, book, "", config, logger):
                return 1
            print(f"Empty input — cleared the custom scan prompt on book {book_id}.")
            return 0

        problems = lint(new_text)
        for w in problems:
            print(f"! {w}", file=sys.stderr)
        blocking = [p for p in problems if p.startswith("no ANCHORING")]
        if blocking and not args.force:
            print("Refusing to save (pass --force to override).", file=sys.stderr)
            return 1

        if args.dry_run:
            action = "replace" if custom else "set"
            print(f"Would {action} the scan prompt on book {book_id} "
                  f"({len(custom)} → {len(new_text)} chars).")
            if custom:
                print("\n".join(difflib.unified_diff(
                    custom.splitlines(), new_text.splitlines(),
                    fromfile="stored", tofile="new", lineterm="")))
            return 0
        if not write_prompt(db, book, new_text, config, logger):
            return 1
        print(f"Saved a {len(new_text)}-char custom scan prompt on book {book_id}"
              f" — {book.get('title')}.")
        if not enabled:
            print("Note: the footnote_scan module is currently OFF for this book, "
                  "so nothing will use it yet.")
        return 0

    return cmd_status(db, book, stored, enabled, settings)


if __name__ == "__main__":
    sys.exit(main())
