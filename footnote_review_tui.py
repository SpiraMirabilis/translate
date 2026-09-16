"""Fullscreen prompt_toolkit review app for footnote candidates.

Left pane: scrollable candidate list (chapter, 中文 term, English term, marks).
Right pane: the full footnote body, source sentence and flags for the cursor
row. Marks write straight through to footnote_candidates.status (committed per
keystroke), so quitting — or crashing — never loses review work and re-entering
resumes exactly where the marks left off.

Keys:
    ↑/↓ j/k     move            g/G          top / bottom
    PgUp/PgDn   page            SPACE        mark keep
    x           mark reject     u            unmark (back to pending)
    a           cycle filter    /            substring filter (Enter apply,
                (all/pending/                Esc clear)
                rejected/flagged)
    q Ctrl-C    quit            Esc          clear filter, or quit

Only 'rejected' is excluded from export — kept AND unmarked rows both export.

Invoked from footnote_scan.py --review; rows arrive with "dup" (later repeat
of an earlier first-mention) and "already" (anchor already in the book's real
footnotes table) precomputed.
"""

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (HSplit, Layout, ScrollOffsets, VSplit,
                                   Window)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

STYLE = Style.from_dict({
    "titlebar": "reverse",
    "toolbar": "reverse",
    "cursor-line": "reverse",
    "dim": "fg:#888888",
    "keep": "fg:ansigreen bold",
    "rej": "fg:ansired bold",
    "warn": "fg:ansiyellow",
    "zh": "fg:ansicyan",
})

FILTERS = ("all", "pending", "rejected", "flagged")
PAGE = 15


def pad(text, width):
    """ljust that accounts for double-width CJK characters, truncating with …"""
    text = text or ""
    if get_cwidth(text) > width:
        out = ""
        for ch in text:
            if get_cwidth(out + ch) > width - 1:
                break
            out += ch
        text = out + "…"
    return text + " " * max(0, width - get_cwidth(text))


class ReviewApp:
    def __init__(self, set_status_fn, rows, book_label=""):
        # set_status_fn(candidate_id, status) persists a review decision —
        # the TUI itself no longer knows where candidates are stored.
        self._set_status_fn = set_status_fn
        self.rows = rows
        self.book_label = book_label
        self.cursor = 0
        self.filter_idx = 0          # index into FILTERS
        self.search = ""             # applied substring filter
        self.search_mode = False     # typing into the filter prompt
        self.search_buf = ""
        self.visible = list(range(len(rows)))
        self._apply_filters()
        self.app = self._build_app()

    # ── filtering ────────────────────────────────────────────────────────────
    def _row_passes(self, r):
        f = FILTERS[self.filter_idx]
        if f == "pending" and r["status"] != "pending":
            return False
        if f == "rejected" and r["status"] != "rejected":
            return False
        if f == "flagged" and not (r.get("dup") or r.get("already")):
            return False
        if self.search:
            hay = " ".join(str(r.get(k) or "") for k in
                           ("term_zh", "term_en", "body", "sentence")).lower()
            if self.search.lower() not in hay:
                return False
        return True

    def _apply_filters(self, keep_row=None):
        self.visible = [i for i, r in enumerate(self.rows) if self._row_passes(r)]
        if keep_row is not None and keep_row in self.visible:
            self.cursor = self.visible.index(keep_row)
        else:
            self.cursor = min(self.cursor, max(0, len(self.visible) - 1))

    def current(self):
        if not self.visible:
            return None
        return self.rows[self.visible[self.cursor]]

    # ── mutation ─────────────────────────────────────────────────────────────
    def set_status(self, status):
        r = self.current()
        if r is None:
            return
        r["status"] = status
        self._set_status_fn(r["id"], status)
        # vim-like flow: marking advances to the next row (unless the filter
        # just swallowed the current one, which _apply_filters handles).
        if FILTERS[self.filter_idx] == "all" and not self.search:
            self.move(1)
        else:
            self._apply_filters()

    def move(self, delta):
        if self.visible:
            self.cursor = max(0, min(len(self.visible) - 1, self.cursor + delta))

    # ── rendering ────────────────────────────────────────────────────────────
    def counts(self):
        c = {"accepted": 0, "rejected": 0, "pending": 0}
        for r in self.rows:
            c[r["status"]] = c.get(r["status"], 0) + 1
        return c

    def title_fragments(self):
        c = self.counts()
        pos = f"{self.cursor + 1}/{len(self.visible)}" if self.visible else "0/0"
        return [("class:titlebar",
                 f" Footnote review — {self.book_label} · {len(self.rows)} candidates"
                 f" · {c['accepted']} kept · {c['rejected']} rejected"
                 f" · filter:{FILTERS[self.filter_idx]}"
                 + (f" /{self.search}" if self.search else "")
                 + f" · {pos} ")]

    def left_fragments(self):
        frags = []
        if not self.visible:
            return [("class:dim", "\n  (no candidates match the current filter)")]
        for i, ri in enumerate(self.visible):
            r = self.rows[ri]
            is_cur = (i == self.cursor)
            base = "class:cursor-line" if is_cur else ""
            if is_cur:
                frags.append(("[SetCursorPosition]", ""))
            frags.append((base, "▸" if is_cur else " "))
            frags.append((base, pad(f"ch{r['chapter_number']}", 7)))
            frags.append((base + " class:zh", pad(r.get("term_zh"), 14)))
            frags.append((base, pad(r.get("term_en"), 30)))
            if r["status"] == "accepted":
                frags.append((base + " class:keep", "✓keep "))
            elif r["status"] == "rejected":
                frags.append((base + " class:rej", "✗rej  "))
            else:
                frags.append((base, "      "))
            if r.get("dup"):
                frags.append((base + " class:dim", "dup "))
            if r.get("already"):
                frags.append((base + " class:warn", "⚠"))
            frags.append(("", "\n"))
        return frags

    def right_fragments(self):
        r = self.current()
        if r is None:
            return [("class:dim", "nothing selected")]
        frags = [("bold", f"{r.get('term_en') or '?'}"),
                 ("class:zh", f"  {r.get('term_zh') or ''}\n"),
                 ("class:dim",
                  f"ch{r['chapter_number']}"
                  + (f" — {r['chapter_title']}" if r.get("chapter_title") else "")
                  + f"  ·  {r.get('model') or ''}\n")]
        status_style = {"accepted": "class:keep", "rejected": "class:rej",
                        "pending": "class:dim"}[r["status"]]
        marks = {"accepted": "✓ keep", "rejected": "✗ rejected",
                 "pending": "unmarked (exports)"}[r["status"]]
        frags.append((status_style, marks + "\n"))
        if r.get("already"):
            frags.append(("class:warn", "⚠ already footnoted in this book\n"))
        if r.get("dup"):
            frags.append(("class:dim", "dup — repeat of an earlier mention\n"))
        frags.append(("", "\n" + (r.get("body") or "") + "\n"))
        if r.get("sentence"):
            frags.append(("class:dim", "\nSOURCE:\n"))
            frags.append(("", r["sentence"] + "\n"))
        return frags

    def toolbar_fragments(self):
        if self.search_mode:
            return [("class:toolbar", f" Filter: {self.search_buf}█"
                                      "   (Enter apply · Esc cancel)")]
        return [("class:toolbar",
                 " ↑↓/jk move · SPACE keep · x reject · u unmark · a filter"
                 " · / search · g/G top/bot · q quit ")]

    # ── app assembly ─────────────────────────────────────────────────────────
    def _build_app(self):
        kb = KeyBindings()
        in_search = Condition(lambda: self.search_mode)

        @kb.add("up", filter=~in_search)
        @kb.add("k", filter=~in_search)
        def _(event):
            self.move(-1)

        @kb.add("down", filter=~in_search)
        @kb.add("j", filter=~in_search)
        def _(event):
            self.move(1)

        @kb.add("pageup", filter=~in_search)
        def _(event):
            self.move(-PAGE)

        @kb.add("pagedown", filter=~in_search)
        def _(event):
            self.move(PAGE)

        @kb.add("g", filter=~in_search)
        def _(event):
            self.cursor = 0

        @kb.add("G", filter=~in_search)
        def _(event):
            self.cursor = max(0, len(self.visible) - 1)

        @kb.add(" ", filter=~in_search)
        def _(event):
            self.set_status("accepted")

        @kb.add("x", filter=~in_search)
        def _(event):
            self.set_status("rejected")

        @kb.add("u", filter=~in_search)
        def _(event):
            self.set_status("pending")

        @kb.add("a", filter=~in_search)
        def _(event):
            cur = self.current()
            self.filter_idx = (self.filter_idx + 1) % len(FILTERS)
            self._apply_filters(keep_row=None if cur is None
                                else self.rows.index(cur))

        @kb.add("/", filter=~in_search)
        def _(event):
            self.search_mode = True
            self.search_buf = self.search

        @kb.add("q", filter=~in_search)
        @kb.add("c-c")
        def _(event):
            event.app.exit()

        @kb.add("escape", filter=~in_search, eager=True)
        def _(event):
            if self.search:
                self.search = ""
                self._apply_filters()
            else:
                event.app.exit()

        # ── search-entry mode ────────────────────────────────────────────────
        @kb.add("enter", filter=in_search)
        def _(event):
            self.search = self.search_buf.strip()
            self.search_mode = False
            self._apply_filters()

        @kb.add("escape", filter=in_search, eager=True)
        def _(event):
            self.search_mode = False
            self.search_buf = ""

        @kb.add("backspace", filter=in_search)
        def _(event):
            self.search_buf = self.search_buf[:-1]

        @kb.add(Keys.Any, filter=in_search)
        def _(event):
            if event.data and event.data.isprintable():
                self.search_buf += event.data

        left = Window(
            FormattedTextControl(self.left_fragments, focusable=True,
                                 show_cursor=False),
            scroll_offsets=ScrollOffsets(top=2, bottom=2),
            width=D(weight=11))
        right = Window(
            FormattedTextControl(self.right_fragments),
            wrap_lines=True, width=D(weight=9))
        root = HSplit([
            Window(FormattedTextControl(self.title_fragments), height=1,
                   style="class:titlebar"),
            VSplit([left, Window(width=1, char="│", style="class:dim"), right]),
            Window(FormattedTextControl(self.toolbar_fragments), height=1,
                   style="class:toolbar"),
        ])
        return Application(layout=Layout(root, focused_element=left),
                           key_bindings=kb, full_screen=True, style=STYLE)

    def run(self):
        self.app.run()
        return self.counts()


def run_review(set_status_fn, rows, book_label=""):
    """Run the fullscreen review over candidate rows (dicts with dup/already
    flags precomputed). Marks persist immediately via set_status_fn(id,
    status); returns final counts."""
    return ReviewApp(set_status_fn, rows, book_label).run()
