"""Chapter numbering from a chapter's place in a batch of queued chapters.

Used by the batch importers (EPUB, FB2, directory, batch upload). Never by the
scraper: it queues everything the source lists under the source's own numbers
and leaves collisions to the translation-time conflict check, and its backfill
dedup is keyed on those numbers.

- ``next_in_sequence``: an unparseable title continues the batch
  (previous + 1); only the batch's first item falls back to its position.
- ``resequence_batch``: a *parsed* number is questioned too. In 50, 51, "40",
  53 the "40" collides with a chapter the book already has (or another item of
  the batch), sits exactly where the missing 52 belongs, and is renumbered to
  52 before it is ever queued -- unless its text is a repost of the chapter it
  collides with, or it is a retranslation.
"""


def next_in_sequence(parsed, prev, position):
    """Number for one chapter of an imported batch.

    The parsed number wins. A chapter whose title gave none continues the
    batch: the previous chapter's number + 1, and only the batch's first
    chapter falls back to its position. Position alone is wrong whenever the
    batch doesn't start at chapter 1 (an EPUB of ch300-400 put one unparseable
    title at 5) or when skipped items leave holes in the count.
    """
    if parsed is not None:
        return parsed
    if prev is not None:
        return prev + 1
    return position


def _as_lines(content):
    if content is None:
        return None
    if isinstance(content, str):
        return content.split("\n")
    return list(content)


def is_repost(incoming_lines, existing_lines):
    """True when ``incoming_lines`` is a (possibly revised) copy of
    ``existing_lines``: at least half the lines of the shorter side are shared.

    Shared with the translation-time sequence slide
    (``WebInterface._sequence_slide_target``), which uses it for the same
    question: is a colliding chapter a mislabelled new one, or that chapter again?
    """
    def norm(lines):
        return {str(l).strip() for l in (lines or ()) if str(l).strip()}
    inc, ex = norm(incoming_lines), norm(existing_lines)
    return bool(inc) and len(inc & ex) * 2 >= min(len(inc), len(ex) or 1)


def resequence_batch(items, existing_numbers, existing_lines=None):
    """Renumber parsed numbers that the batch's own sequence contradicts.

    ``items`` is the batch in reading order: dicts with ``number`` (int) and
    ``content`` (lines or str), optionally ``retranslation_reason``. Mutated in
    place: a renumbered item gets ``number`` = the slot and
    ``renumbered_from`` = what it parsed as.

    ``existing_numbers``: chapter numbers the book already holds (saved or
    queued). ``existing_lines(n)``: the stored source of the book's chapter n
    (saved, else queued), or None when it can't be read.

    An item is renumbered only when all hold:
    - it has neighbours on both sides, and next == previous + 2 (one slot free
      between them, which is where it sits);
    - its number is not that slot and collides -- with the book, or with
      another item of this batch;
    - the slot itself is free, in the book and in the batch;
    - it is not a retranslation;
    - its text is not a repost of what it collides with (and that text could
      be read -- no evidence, no renumber).

    Returns ``[(from, to), ...]``.
    """
    existing_numbers = set(existing_numbers or ())
    moved = []
    for k in range(1, len(items) - 1):
        item = items[k]
        prev, nxt, number = items[k - 1].get("number"), items[k + 1].get("number"), item.get("number")
        if not all(isinstance(n, int) for n in (prev, nxt, number)):
            continue
        slot = prev + 1
        if nxt != slot + 1 or number == slot:
            continue
        if (item.get("retranslation_reason") or "").strip():
            continue
        batch_others = [it for j, it in enumerate(items) if j != k]
        batch_numbers = {it.get("number") for it in batch_others}
        if slot in existing_numbers or slot in batch_numbers:
            continue
        twins = [it for it in batch_others if it.get("number") == number]
        in_book = number in existing_numbers
        if not twins and not in_book:
            continue
        incoming = _as_lines(item.get("content"))
        against = [_as_lines(t.get("content")) for t in twins]
        if in_book:
            against.append(existing_lines(number) if existing_lines else None)
        if any(lines is None for lines in against):
            continue
        if any(is_repost(incoming, lines) for lines in against):
            continue
        item["renumbered_from"] = number
        item["number"] = slot
        moved.append((number, slot))
    return moved


def book_lines_lookup(db, book_id):
    """``existing_lines`` for ``resequence_batch``: a saved chapter's source,
    else a queued row's content."""
    def lookup(number):
        try:
            ch = db.get_chapter(book_id=book_id, chapter_number=number)
        except Exception:
            ch = None
        if ch and ch.get("untranslated"):
            return _as_lines(ch["untranslated"])
        try:
            return _as_lines(db.queued_source_lines(book_id, number))
        except Exception:
            return None
    return lookup


def resequence_for_book(db, book_id, items, logger=None):
    """``resequence_batch`` against book ``book_id``, with logging.

    A no-op for a batch with no book (a book being created never collides).
    """
    if db is None or book_id is None or len(items) < 3:
        return []
    try:
        existing = db.existing_chapter_numbers(book_id)
    except Exception:
        return []
    moved = resequence_batch(items, existing, book_lines_lookup(db, book_id))
    if moved and logger is not None:
        for frm, to in moved:
            logger.info(f"Batch import book {book_id}: chapter numbered {frm} sits where "
                        f"{to} is missing and {frm} already exists; queuing it as {to}")
    return moved


def log_resequenced(db, book_id, items):
    """One activity line for the chapters a batch renumbered at queue time."""
    moved = [(it["renumbered_from"], it["number"]) for it in items
             if it.get("renumbered_from") is not None]
    if not moved:
        return
    from modules.activity import log_module_activity
    pairs = ", ".join(f"{frm} → {to}" for frm, to in moved)
    log_module_activity(
        db, "info",
        f"Batch import renumbered {len(moved)} chapter(s) whose number collided with an "
        f"existing chapter but sat in a missing slot of the sequence: {pairs}",
        book_id=book_id,
    )


def renumber_metadata(item, metadata=None):
    """Queue-row metadata recording a queue-time renumber (or ``metadata``)."""
    if item.get("renumbered_from") is None:
        return metadata
    out = dict(metadata or {})
    out["renumbered_from"] = item["renumbered_from"]
    return out
