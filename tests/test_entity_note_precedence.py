"""Entity `note` precedence on update.

The first-pass translation model routinely re-emits entities that already exist,
and it may volunteer a `note` for them. A volunteered note must never clobber a
curated one. A note set by a human during review must win.

`ui.py` encodes this by choosing the COALESCE direction per entity:
    human-edited  -> note = COALESCE(?, note)   reviewer's note wins
    model-emitted -> note = COALESCE(note, ?)   existing note always wins

These tests pin the two SQL expressions, which is where the behaviour actually
lives (the surrounding ui.py code is an interactive flow that is hard to drive
in a unit test).
"""
import pytest

MODEL_EMITTED = "COALESCE(note, ?)"   # existing note wins
HUMAN_EDITED = "COALESCE(?, note)"    # incoming note wins


def _mk(db):
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS note_probe (id INTEGER PRIMARY KEY, note TEXT)")
        cur.execute("DELETE FROM note_probe")
        conn.commit()


def _set(db, note):
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO note_probe (id, note) VALUES (1, ?)", (note,))
        conn.commit()


def _update(db, expr, incoming):
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE note_probe SET note = {expr} WHERE id = 1", (incoming,))
        conn.commit()


def _get(db):
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT note FROM note_probe WHERE id = 1")
        return cur.fetchone()[0]


@pytest.mark.parametrize("existing,incoming,expected", [
    ("curated", "model note", "curated"),   # must NOT clobber
    (None, "model note", "model note"),     # fills an empty note
    ("curated", None, "curated"),           # omitting a note is harmless
])
def test_model_emitted_note_never_clobbers(db, existing, incoming, expected):
    _mk(db)
    _set(db, existing)
    _update(db, MODEL_EMITTED, incoming)
    assert _get(db) == expected


@pytest.mark.parametrize("existing,incoming,expected", [
    ("curated", "reviewer note", "reviewer note"),  # reviewer wins
    (None, "reviewer note", "reviewer note"),
    ("curated", None, "curated"),                   # reviewer omitted one
])
def test_human_edited_note_wins(db, existing, incoming, expected):
    _mk(db)
    _set(db, existing)
    _update(db, HUMAN_EDITED, incoming)
    assert _get(db) == expected


def test_ui_uses_both_directions():
    """Regression guard: ui.py must branch on human_edited_keys, not hardcode one."""
    src = open("ui.py", encoding="utf-8").read()
    assert "human_edited_keys" in src
    assert MODEL_EMITTED in src, "model-emitted path must preserve an existing note"
    assert HUMAN_EDITED in src, "human-edited path must let the reviewer's note win"
