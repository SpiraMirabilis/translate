"""chatgroup_transformer module — normalize chat/notification source lines into 【…】.

Some web novels are written in a "group chat" format: system notifications are
emitted as quoted ``叮！…`` lines, and chat messages appear as
``username:message`` lines, e.g.::

    "叮！恭喜宿主完成任务，奖励……"
    小埋:开什么玩笑！按照小说设定小埋我的世界不应该是普通日常番吗！

This module rewrites those lines so they carry the full-width bracket
convention 【…】, which is the shape a downstream module (Markdown Notifications,
which renders 【…】 as boxed tables) expects on the translated side. Two line
shapes are matched:

  * **Ding notifications** — a line beginning with a double-quote followed by
    ``叮！`` (or its English rendering, ``Ding`` followed by punctuation —
    ``Ding!`` / ``Ding,`` / ``Ding~``). The surrounding quotes are stripped (a
    trailing quote is optional — its absence does not prevent the match) and the
    remainder is wrapped in 【…】::

        "叮！恭喜宿主……"   →   【叮！恭喜宿主……】
        "Ding! Invitation successful."   →   【Ding! Invitation successful.】

    Caveat: on translated text, a quoted dialogue line *addressing* a character
    surnamed Ding (``"Ding, come here!"``) matches this shape too — the ding
    detector has no entity gate. Acceptable for the chat-group genre this
    module targets.

  * **Chat messages** — ``username:message`` (ASCII ``:`` or full-width ``：``),
    where the username is a short token (up to four space-separated words, so
    translated names like ``Kurosaki Ichigo`` match) with no punctuation **and
    exactly matches a person-like entity for this book** — any entity carrying a
    ``gender`` (characters, chatgroup usernames), including globals. A username
    clears the gate by matching an entity's *untranslated* or *translated* name,
    so the same gate works on both the source and the translated side. The bare
    regex shape proved too loose — ordinary prose that happens to contain ``词:``
    (e.g. ``浦原喜助站在门口：“…”``) was being wrapped — so the entity match is a
    required second gate. The whole (stripped) line is wrapped in 【…】::

        小埋:开什么玩笑！……   →   【小埋:开什么玩笑！……】   (only if 小埋 is a gendered entity)
        Umaru: You've got to be kidding!……   →   【Umaru: You've got to be kidding!……】

    The entity set is loaded from the DB via ``ctx['db']``. When it is
    unavailable (no db/book in context), the gate is skipped and matching falls
    back to the regex alone, preserving the module's prior behavior for those
    call sites.

The transform runs on **both sides of a chapter**: at source ingest
(``transform_source_lines``) and at translated ingest
(``transform_translated_lines``) so newly queued/saved chapters are normalized
automatically, and ``event_add_to_book`` backfills every existing chapter's
untranslated source **and translated content**, plus every queued item for the
book. The translated side exists for chapters translated *before* the module
was enabled — re-wrapping only their source would leave the stored translation
unboxed forever. Disabling the module fires ``event_removed_from_book``, which
reverses the transform on both sides.

Ordering note: this module is registered before ``markdown_notifications``, so
at translated ingest (and when both modules are enabled in one save) the 【…】
wrapping happens first and the table conversion sees it. On removal, module
events fire in reverse registry order, so Markdown Notifications reverts its
tables back to 【…】 before this module unwraps them.

Reversal is intentionally lossy-but-good-enough: 【…】 whose inner text starts
with ``叮！`` (or English ``Ding`` + punctuation) is turned back into a
straight-quote-wrapped line (a trailing quote is always re-added, even if the
original lacked one), and 【…】 whose inner text matches ``username:message``
has its brackets removed. Other 【…】 lines are left untouched.

Forward and reverse are both idempotent: a wrapped line begins with 【 and so no
longer matches either forward pattern, and an unwrapped line no longer matches
the reverse pattern.

Auto-off: never enabled by Source URL or default; turn it on per book via the
Modules dialog.
"""
import json
import re

from .base import TranslationModule

# Double-quote characters we treat as chat/notification quoting: straight (") and
# the curly pair (“ ”).
_DQUOTES = "\"“”"

# The notification opener, source or translated: 叮！, or English "Ding"
# followed by punctuation ("Ding!" / "Ding," / "Ding~"). Bare "Ding <word>" is
# NOT matched — too close to dialogue addressing a character named Ding.
_DING_INNER = r"(?:叮！|Ding[!！~,，])"

# A Ding notification: (optional leading whitespace) a double-quote, then the
# notification opener.
_DING_RE = re.compile(r"^\s*[" + _DQUOTES + r"]" + _DING_INNER)

# Inner text of a 【…】 that reverses to a quoted Ding notification.
_DING_UNWRAP_RE = re.compile(r"^" + _DING_INNER)

# A chat line: username:message. The username is one to four short
# space-separated words (translated names like "Kurosaki Ichigo" span several)
# containing no colon, no bracket, no pipe (already-converted Markdown table
# rows start with |), no quote, and no sentence punctuation — this keeps prose
# lines that merely happen to contain a colon from matching. The message must
# start with a non-space character; a single space after the colon is allowed
# (the translated-side convention).
_NAME_TOKEN = r"[^\s:：【】|" + _DQUOTES + r"，。！？、,.!?]{1,20}"
_USER_RE = re.compile(
    r"^\s*(" + _NAME_TOKEN + r"(?: " + _NAME_TOKEN + r"){0,3})[:：]\s?\S")

# A whole-line 【…】 wrapper (what the forward transform produces). Greedy inner
# so nested brackets — e.g. 【Umaru: hi 【Thumbs Up】】 — match through the LAST
# closer.
_WRAPPED_RE = re.compile(r"^\s*【(.*)】\s*$")


def _book_id(book):
    return book.get("id") if (book and hasattr(book, "get")) else None


def _load_entity_names(db, book_id, settings):
    """Set of names allowed as chat usernames, per the module settings.

    Returns ``None`` to mean "skip the entity gate" (regex-only matching) — used
    when ``restrict_to_entities`` is off, or when the set can't be determined (no
    db/book, or a query error), so the module still works on call sites without a
    db. Otherwise returns the union of ``untranslated`` **and** ``translation``
    names — the same set gates both the source-side (Chinese username) and the
    translated-side (English username) transforms — sourced either from the
    default gender gate (characters/chatgroup usernames — any gendered entity) or
    from the explicit ``username_categories`` chosen in settings.
    """
    if not settings.get("restrict_to_entities", True):
        return None
    if db is None or not book_id:
        return None
    use_default = settings.get("use_default_username_restriction", True)
    try:
        conn = db.backend.get_connection()
        cur = conn.cursor()
        if use_default:
            cur.execute(
                "SELECT untranslated, translation FROM entities "
                "WHERE (book_id = ? OR book_id IS NULL) "
                "AND gender IS NOT NULL AND gender != ''",
                (book_id,))
        else:
            cats = [c for c in (settings.get("username_categories") or []) if c]
            if not cats:
                conn.close()
                return set()  # explicit custom mode with no categories → allow none
            placeholders = ",".join("?" for _ in cats)
            cur.execute(
                "SELECT untranslated, translation FROM entities "
                "WHERE book_id = ? AND category IN (" + placeholders + ")",
                (book_id, *cats))
        names = {v for row in cur.fetchall() for v in row[:2] if v}
        conn.close()
        return names
    except Exception:
        return None


def _username_ok(name, names):
    """Whether ``name`` clears the entity gate. ``None`` names → gate skipped."""
    return True if names is None else name in names


def _wrap_line(line, names):
    """Wrap a Ding-notification or chat line in 【…】; pass anything else through.

    Chat (``username:message``) lines are wrapped only when the username also
    matches a known entity (see :func:`_username_ok`).
    """
    if not isinstance(line, str):
        return line
    if _DING_RE.match(line):
        inner = line.strip()
        if inner and inner[0] in _DQUOTES:
            inner = inner[1:]
        if inner and inner[-1] in _DQUOTES:  # trailing quote optional
            inner = inner[:-1]
        return "【" + inner + "】"
    m = _USER_RE.match(line)
    if m and _username_ok(m.group(1), names):
        return "【" + line.strip() + "】"
    return line


def _unwrap_line(line, names):
    """Reverse :func:`_wrap_line` for the two shapes this module produces."""
    if not isinstance(line, str):
        return line
    m = _WRAPPED_RE.match(line)
    if not m:
        return line
    inner = m.group(1)
    if _DING_UNWRAP_RE.match(inner):
        # Re-emit as a quoted notification; a trailing quote is always added,
        # even if the original source lacked one (reversal need not be exact).
        return "\"" + inner + "\""
    um = _USER_RE.match(inner)
    if um and _username_ok(um.group(1), names):
        return inner
    return line  # some other 【…】 — not ours, leave it


def _apply(content, per_line):
    """Apply ``per_line`` to each line of a list- or string-shaped content blob.

    Returns the same type it received, and returns the original object unchanged
    when nothing was rewritten (so callers can cheaply detect no-ops).
    """
    if isinstance(content, list):
        out = [per_line(l) for l in content]
        return out if out != content else content
    if isinstance(content, str):
        parts = content.split("\n")
        out = [per_line(l) for l in parts]
        return "\n".join(out) if out != parts else content
    return content


def _wrap_lines(content, names):
    return _apply(content, lambda l: _wrap_line(l, names))


def _unwrap_lines(content, names):
    return _apply(content, lambda l: _unwrap_line(l, names))


def _rewrite_blob(raw, bound):
    """Apply ``bound`` to a stored content blob (JSON list, or legacy raw string).

    ``bound`` is a one-arg callable (the names-bound wrap/unwrap). Returns
    ``(new_raw, changed)``. JSON lists are re-serialized; a blob that doesn't
    parse as a JSON list is treated as raw newline-joined text.
    """
    if not raw:
        return raw, False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        data = None
    if isinstance(data, list):
        new = bound(data)
        if new != data:
            return json.dumps(new, ensure_ascii=False), True
        return raw, False
    new = bound(raw)
    if new != raw:
        return new, True
    return raw, False


class ChatgroupTransformerModule(TranslationModule):
    id = "chatgroup_transformer"
    name = "Chatgroup Transformer"
    description = ("Normalize 'group chat' novels: wrap quoted 叮！/Ding "
                  "notifications and username:message chat lines in 【…】 so the "
                  "Markdown Notifications module can render them — on both the "
                  "source and the translated text. Enabling it rewrites every "
                  "existing chapter (source + translation) and every queued item; "
                  "disabling it reverses the transform.")
    # Auto-off: no default, no URL patterns — manual per book only.
    # The 【…】 backfill depends on these settings, so re-derive it when they change.
    rebuild_on_settings_change = True

    settings_schema = [
        {"key": "restrict_to_entities", "type": "bool", "default": True,
         "label": "Restrict chat lines to valid entities",
         "help": ("Only wrap username:message lines whose username matches a known "
                  "entity. Off = wrap every line matching the chat shape (looser).")},
        {"key": "use_default_username_restriction", "type": "bool", "default": True,
         "label": "Use default username restriction (only entities that have a gender)",
         "help": "Off = choose which entity categories count as chat usernames instead.",
         "show_if": {"restrict_to_entities": True}},
        {"key": "username_categories", "type": "multiselect", "default": [],
         "options_source": "book_categories",
         "label": "Categories where chat usernames are defined",
         "help": "Entities in these categories are treated as valid chat usernames.",
         "show_if": {"restrict_to_entities": True,
                     "use_default_username_restriction": False}},
    ]

    def _settings(self, ctx):
        return self.resolve_settings((ctx.get("module_settings") or {}).get(self.id))

    def transform_source_lines(self, content, ctx):
        names = _load_entity_names(
            ctx.get("db"), _book_id(ctx.get("book")), self._settings(ctx))
        return _wrap_lines(content, names)

    def transform_translated_lines(self, content, ctx):
        # Same transform on the translated side: catches Ding/username lines the
        # model emitted without brackets (e.g. when the source predates this
        # module's wrapping). Runs before markdown_notifications (registry order),
        # so the wrapping is seen by its table conversion.
        names = _load_entity_names(
            ctx.get("db"), _book_id(ctx.get("book")), self._settings(ctx))
        return _wrap_lines(content, names)

    def event_add_to_book(self, ctx):
        """Backfill: wrap chat/notification lines in every chapter + queued item."""
        self._rewrite_all(ctx, _wrap_lines, "wrapped")

    def event_removed_from_book(self, ctx):
        """Reverse: unwrap the 【…】 lines this module produced."""
        self._rewrite_all(ctx, _unwrap_lines, "unwrapped")

    def _rewrite_all(self, ctx, fn, verb):
        db = ctx.get("db")
        book = ctx.get("book")
        logger = ctx.get("logger")
        if db is None or not book:
            return
        book_id = _book_id(book)
        if not book_id:
            return
        names = _load_entity_names(db, book_id, self._settings(ctx))
        bound = lambda content: fn(content, names)

        conn = db.backend.get_connection()
        cur = conn.cursor()
        changed = 0
        translations_changed = 0

        # Existing chapters — rewrite the untranslated source AND the stored
        # translation (chapters translated before the module was enabled carry
        # unwrapped Ding/username lines on the translated side too).
        cur.execute(
            "SELECT id, untranslated_content, translated_content "
            "FROM chapters WHERE book_id = ?",
            (book_id,))
        for cid, raw_src, raw_tr in cur.fetchall():
            new_src, ch_src = _rewrite_blob(raw_src, bound)
            new_tr, ch_tr = _rewrite_blob(raw_tr, bound)
            if ch_src or ch_tr:
                cur.execute(
                    "UPDATE chapters SET untranslated_content = ?, "
                    "translated_content = ? WHERE id = ?",
                    (new_src, new_tr, cid))
                changed += 1
                if ch_tr:
                    translations_changed += 1

        # Queued (not-yet-translated) items — rewrite their source content.
        cur.execute("SELECT id, content FROM queue WHERE book_id = ?", (book_id,))
        for qid, raw in cur.fetchall():
            new, ch = _rewrite_blob(raw, bound)
            if ch:
                cur.execute(
                    "UPDATE queue SET content = ? WHERE id = ?", (new, qid))
                changed += 1

        conn.commit()
        conn.close()
        if translations_changed:
            try:
                db.invalidate_epub_cache(book_id)
            except Exception:
                pass
        if logger:
            logger.info(
                f"chatgroup_transformer: {verb} {changed} item(s) for book {book_id}")
