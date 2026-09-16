"""Footnote candidates collected by the translation pass itself.

A book set to scan_mode "translation" gets its footnote candidates back on the
translation response (a top-level ``footnote_candidates`` array) instead of
paying for a second model call. The rules, the anchoring filter and the review
queue are the standalone scanner's — only the delivery changes.

Covered here: what turns the channel on, what the prompt carries, how the
candidates survive chunking and validation, and the storage rule that keeps a
retranslation from eating a human's review.
"""
import pytest

from tests.conftest import FakeLogger

import footnote_scan_core as core
from footnote_scan_core import MODE_INGEST, MODE_INLINE, MODE_SETTING, PROMPT_SETTING


# ── gating ───────────────────────────────────────────────────────────────────

class _Config:
    footnote_inline_scan = True
    entity_note_updates = True
    translation_model = "test:model"


def _book(**over):
    book = {"id": 1, "source_language": "zh", "modules": {}}
    book.update(over)
    return book


def _ctx(settings):
    return {"module_settings": {"footnote_scan": settings}}


def test_inline_is_the_default_and_ingest_is_the_opt_out():
    """A book that never chose a mode scans during translation; only an
    explicit "ingest" buys the second pass back."""
    assert core.inline_enabled(_book(), config=_Config(), ctx=_ctx({}))
    assert not core.inline_enabled(_book(), config=_Config(),
                                   ctx=_ctx({MODE_SETTING: MODE_INGEST}))


def test_inline_needs_the_module_enabled_for_the_book():
    on = _ctx({MODE_SETTING: MODE_INLINE})
    assert core.inline_enabled(_book(), config=_Config(), ctx=on)
    # Explicit per-book override wins over the auto-on-for-zh rule.
    assert not core.inline_enabled(_book(modules={"footnote_scan": False}),
                                   config=_Config(), ctx=on)
    # ...and the auto rule itself still applies.
    assert not core.inline_enabled(_book(source_language="en"),
                                   config=_Config(), ctx=on)


def test_global_switch_overrides_every_book():
    class Off(_Config):
        footnote_inline_scan = False
    assert not core.inline_enabled(_book(), config=Off(),
                                   ctx=_ctx({MODE_SETTING: MODE_INLINE}))


def test_gating_never_raises_on_a_broken_lookup():
    """A settings failure means "not inline", not a failed translation."""
    assert not core.inline_enabled(None, config=_Config())

    class Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("settings table is gone")
    assert not core.inline_enabled(Boom(), config=_Config(), ctx=_ctx({}))


def test_module_skips_its_worker_when_scanning_inline(db, monkeypatch):
    """Otherwise the chapter is scanned twice — once inline, once on ingest."""
    from modules.footnote_scan_module import FootnoteScanModule, footnote_scan_worker
    jobs = []
    monkeypatch.setattr(footnote_scan_worker, "enqueue", jobs.append)

    mod = FootnoteScanModule()
    ctx = {"book": _book(), "db": db, "config": _Config(), "logger": FakeLogger(),
           "chapter_number": 3, "source_lines": ["陈元看着刍狗。"],
           "module_settings": {"footnote_scan": {MODE_SETTING: MODE_INLINE}}}
    mod.event_new_chapter_saved(ctx)
    assert jobs == []

    # ...and a book that set no mode at all takes the same (default) path.
    ctx["module_settings"] = {"footnote_scan": {}}
    mod.event_new_chapter_saved(ctx)
    assert jobs == []

    ctx["module_settings"] = {"footnote_scan": {MODE_SETTING: MODE_INGEST}}
    mod.event_new_chapter_saved(ctx)
    assert len(jobs) == 1


# ── the prompt block ─────────────────────────────────────────────────────────

def test_section_carries_rules_covered_list_and_output_channel():
    out = core.inline_prompt_section("THE RULES", [("刍狗", "straw dogs"), ("唐僧肉", "")])
    assert out.startswith("FOOTNOTE CANDIDATES:")
    assert "THE RULES" in out
    assert "ALREADY FOOTNOTED" in out
    assert "刍狗 = straw dogs" in out
    assert "唐僧肉" in out
    assert '"footnote_candidates"' in out
    # The output channel comes last, so it supersedes anything the rules say.
    assert out.index("THE RULES") < out.index('"footnote_candidates"')


def test_section_omits_the_covered_list_when_there_is_nothing_to_exclude():
    assert "ALREADY FOOTNOTED" not in core.inline_prompt_section("THE RULES")


def test_section_is_empty_when_the_book_does_not_scan_inline():
    assert core.inline_scan_section(None, _book(), _Config(), "陈元。",
                                    ctx=_ctx({MODE_SETTING: MODE_INGEST})) == ""


def test_a_books_custom_rules_ride_the_inline_channel(db):
    book_id = db.create_book("Inline Book", "Author")
    db.set_module_settings(book_id, "footnote_scan", {
        MODE_SETTING: MODE_INLINE, PROMPT_SETTING: "Find Japanese referents only."})
    book = db.get_book(book_id=book_id)
    out = core.inline_scan_section(db, book, _Config(), "陈元看着刍狗。", 3)
    assert "Find Japanese referents only." in out
    assert core.SCAN_RULES not in out
    assert '"footnote_candidates"' in out


# ── engine plumbing ──────────────────────────────────────────────────────────

def _engine():
    from translation_engine import TranslationEngine
    return TranslationEngine(_Config(), FakeLogger(), entity_manager=None)


SOURCE = "陈元看着刍狗，心中一动。\n他冷笑道：先涨后奏，便是如此。"


def test_validate_keeps_anchored_candidates_and_drops_fabrications():
    kept = _engine().validate_footnote_candidates([
        {"term_zh": "刍狗", "term_en": "straw dogs", "body": "From the Daodejing.",
         "sentence": "陈元看着刍狗，心中一动。"},
        # The parodied idiom: the page says 先涨后奏, one character off.
        {"term_zh": "先斩后奏", "term_en": "behead first", "body": "A pun on the idiom.",
         "sentence": "他冷笑道：先涨后奏，便是如此。"},
        # Nowhere in the chapter.
        {"term_zh": "完全虚构", "term_en": "invented", "body": "Not on the page.",
         "sentence": "这句话不存在。"},
        # No body — not a candidate at all.
        {"term_zh": "刍狗", "term_en": "x", "body": "", "sentence": ""},
    ], SOURCE)
    assert [c["term_zh"] for c in kept] == ["刍狗", "先斩后奏"]


@pytest.mark.parametrize("raw", [[], {"not": "a list"}, "nonsense"])
def test_validate_never_raises_on_junk(raw):
    assert _engine().validate_footnote_candidates(raw, SOURCE) == []


def test_an_absent_channel_is_not_an_empty_result():
    """None means the model never opened the channel; [] means it looked and
    found nothing. Only the latter earns a scan row, so the two must not
    collapse into each other."""
    assert _engine().validate_footnote_candidates(None, SOURCE) is None
    assert _engine().validate_footnote_candidates([], SOURCE) == []


def test_a_zero_find_chapter_still_records_a_scan(db):
    """Otherwise the bulk CLI re-scans a genuinely empty chapter forever."""
    book_id = db.create_book("Zero", "Author")
    assert core.persist_inline_candidates(
        db, book_id, 7, "Chapter 7", "test:model", SOURCE, []) == []
    assert db.get_footnote_scans(book_id)[7]["n_found"] == 0


def test_chunks_concatenate_rather_than_overwrite():
    """Each chunk saw only its own slice, so both chunks' finds must survive —
    unlike note_updates, which is keyed and merges."""
    eng = _engine()
    merged = eng.combine_json_chunks(
        {"content": ["a"], "summary": "s1", "entities": {},
         "footnote_candidates": [{"term_zh": "刍狗", "body": "one"}]},
        {"content": ["b"], "summary": "s2", "entities": {},
         "footnote_candidates": [{"term_zh": "先斩后奏", "body": "two"}]},
        3)
    assert [c["term_zh"] for c in merged["footnote_candidates"]] == ["刍狗", "先斩后奏"]


def test_chunk_without_candidates_leaves_the_first_chunks_alone():
    eng = _engine()
    merged = eng.combine_json_chunks(
        {"content": [], "summary": "", "entities": {},
         "footnote_candidates": [{"term_zh": "刍狗", "body": "one"}]},
        {"content": [], "summary": "", "entities": {}}, 3)
    assert len(merged["footnote_candidates"]) == 1


def test_template_and_response_format_expose_the_channel():
    eng = _engine()
    import json
    tmpl = json.loads(eng._build_response_template(
        ["characters"], {"characters": {}}, 3, footnote_candidates_enabled=True))
    assert isinstance(tmpl["footnote_candidates"], list)
    assert set(tmpl["footnote_candidates"][0]) == {"term_zh", "term_en", "body", "sentence"}
    # Pass 2 translates prose only — there is nowhere to put them.
    assert "footnote_candidates" not in json.loads(eng._build_response_template(
        ["characters"], {}, 3, mode="translate_only", footnote_candidates_enabled=True))
    assert "footnote_candidates" not in json.loads(eng._build_response_template(
        ["characters"], {}, 3))

    rf = eng._entity_response_format("full", ["characters"], footnote_candidates=True)
    assert rf["footnote_candidates"] is True
    assert "footnote_candidates" not in eng._entity_response_format("full", ["characters"])


def test_gemini_schema_carries_the_channel_without_requiring_it():
    from providers.gemini_provider import GeminiProvider
    g = GeminiProvider.__new__(GeminiProvider)
    for mode in ("full", "entity_only"):
        schema = g._create_response_schema({
            "type": "json_object", "mode": mode, "categories": ["characters"],
            "footnote_candidates": True})
        assert schema["properties"]["footnote_candidates"]["type"] == "array"
        assert "footnote_candidates" not in schema["required"]
    off = g._create_response_schema({"type": "json_object", "mode": "full",
                                     "categories": ["characters"]})
    assert "footnote_candidates" not in off["properties"]


# ── storage ──────────────────────────────────────────────────────────────────

CANDIDATE = {"term_zh": "刍狗", "term_en": "straw dogs", "body": "From the Daodejing.",
             "sentence": "陈元看着刍狗，心中一动。"}


def test_persist_stores_anchored_candidates_and_records_the_scan(db):
    book_id = db.create_book("Store", "Author")
    kept = core.persist_inline_candidates(
        db, book_id, 3, "Chapter 3", "test:model", SOURCE,
        [CANDIDATE, {"term_zh": "完全虚构", "term_en": "x", "body": "Nope.",
                     "sentence": "不存在。"}])
    assert [c["term_zh"] for c in kept] == ["刍狗"]
    rows = db.list_footnote_candidates(book_id)
    assert [r["term_zh"] for r in rows] == ["刍狗"]
    assert rows[0]["status"] == "pending"
    # The scan row is what stops the bulk CLI re-scanning the same chapter.
    assert db.get_footnote_scans(book_id)[3]["n_found"] == 1


def test_persist_collapses_a_term_found_in_two_chunks(db):
    book_id = db.create_book("Dupes", "Author")
    kept = core.persist_inline_candidates(
        db, book_id, 3, "Chapter 3", "test:model", SOURCE,
        [CANDIDATE, dict(CANDIDATE, body="Said differently.")])
    assert len(kept) == 1


def test_persist_is_a_noop_without_source_text(db):
    book_id = db.create_book("Empty", "Author")
    assert core.persist_inline_candidates(
        db, book_id, 3, "t", "test:model", "   ", [CANDIDATE]) == []
    assert db.list_footnote_candidates(book_id) == []


def test_retranslation_keeps_reviewed_rows_and_refreshes_pending(db):
    """The reason preserve_reviewed exists: inline scanning re-runs on every
    retranslation, and a keep/reject decision has to outlive one."""
    book_id = db.create_book("Reviewed", "Author")
    db.record_footnote_scan(book_id, 3, "Chapter 3", "test:model", "hash1", [
        CANDIDATE,
        {"term_zh": "唐僧肉", "term_en": "Tang monk's flesh", "body": "Journey to the West.",
         "sentence": "x"},
        {"term_zh": "铁公鸡", "term_en": "iron rooster", "body": "A skinflint.", "sentence": "y"},
    ])
    rows = {r["term_zh"]: r for r in db.list_footnote_candidates(book_id)}
    db.update_footnote_candidate(rows["刍狗"]["id"], status="accepted")
    db.update_footnote_candidate(rows["唐僧肉"]["id"], status="rejected")

    db.record_footnote_scan(
        book_id, 3, "Chapter 3", "test:model", "hash2",
        # The model re-proposes an accepted term and finds one genuinely new one.
        [dict(CANDIDATE, body="A worse rewrite."),
         {"term_zh": "东山再起", "term_en": "comeback", "body": "Xie An.", "sentence": "z"}],
        preserve_reviewed=True)

    after = {r["term_zh"]: r for r in db.list_footnote_candidates(book_id)}
    assert after["刍狗"]["status"] == "accepted"
    assert after["刍狗"]["body"] == CANDIDATE["body"], "a decided row was rewritten"
    assert after["唐僧肉"]["status"] == "rejected"
    assert "铁公鸡" not in after, "an undecided row should have been replaced"
    assert after["东山再起"]["status"] == "pending"
    assert db.get_footnote_scans(book_id)[3]["n_found"] == len(after)


def test_without_the_flag_a_rescan_still_replaces_everything(db):
    """The on-ingest and import paths rely on replace-all; only the re-scan
    paths opt in."""
    book_id = db.create_book("Replace", "Author")
    db.record_footnote_scan(book_id, 3, "t", "test:model", "h1", [CANDIDATE])
    row = db.list_footnote_candidates(book_id)[0]
    db.update_footnote_candidate(row["id"], status="accepted")
    db.record_footnote_scan(book_id, 3, "t", "test:model", "h2", [])
    assert db.list_footnote_candidates(book_id) == []


# ── first-mention dedupe across the book ─────────────────────────────────────
#
# The ALREADY FOOTNOTED block in the prompt only *asks* the model not to
# re-propose a settled referent. The bulk CLI backstops that with a
# dedup_candidates sweep at the end of a run; the inline path has no run to
# sweep, so it enforces the same rule at persist time.

def test_a_term_settled_at_an_earlier_chapter_is_dropped(db):
    book_id = db.create_book("Repeats", "Author")
    db.record_footnote_scan(book_id, 5, "Chapter 5", "test:model", "h5", [CANDIDATE])

    kept = core.persist_inline_candidates(
        db, book_id, 900, "Chapter 900", "test:model", SOURCE,
        [dict(CANDIDATE, body="Explained again, worse."),
         {"term_zh": "先斩后奏", "term_en": "behead first", "body": "A pun.",
          "sentence": "他冷笑道：先涨后奏，便是如此。"}])
    assert [c["term_zh"] for c in kept] == ["先斩后奏"]
    assert len(db.list_footnote_candidates(book_id, chapter=900)) == 1


def test_an_earlier_rejection_also_suppresses_it(db):
    """The decision was 'not this one'; re-asking 900 chapters later is the
    same wasted review. Matches dedup_candidates, which dedupes against every
    earlier row regardless of status."""
    book_id = db.create_book("Rejected", "Author")
    db.record_footnote_scan(book_id, 5, "Chapter 5", "test:model", "h5", [CANDIDATE])
    row = db.list_footnote_candidates(book_id, chapter=5)[0]
    db.update_footnote_candidate(row["id"], status="rejected")

    assert core.persist_inline_candidates(
        db, book_id, 900, "Chapter 900", "test:model", SOURCE, [CANDIDATE]) == []


def test_a_real_footnote_suppresses_it_too(db):
    book_id = db.create_book("Footnoted", "Author")
    chapter_id = db.save_chapter(book_id, 900, "Chapter 900",
                                 ["陈元看着刍狗。"], ["Chen Yuan."])
    db.add_footnote(book_id, chapter_id, "straw dogs", "From the Daodejing.",
                    source_term="刍狗")
    assert core.persist_inline_candidates(
        db, book_id, 900, "Chapter 900", "test:model", SOURCE, [CANDIDATE]) == []


def test_a_later_chapters_find_does_not_suppress_an_earlier_one(db):
    """Reading order decides first mention: retranslating ch5 must still be
    able to collect a term ch900 happens to hold."""
    book_id = db.create_book("Order", "Author")
    db.record_footnote_scan(book_id, 900, "Chapter 900", "test:model", "h9", [CANDIDATE])
    kept = core.persist_inline_candidates(
        db, book_id, 5, "Chapter 5", "test:model", SOURCE, [CANDIDATE])
    assert [c["term_zh"] for c in kept] == ["刍狗"]


def test_rescanning_the_same_chapter_does_not_suppress_itself(db):
    """Its own rows are at chapter N, not before it — a retranslation must be
    able to re-collect what it found last time."""
    book_id = db.create_book("Self", "Author")
    core.persist_inline_candidates(
        db, book_id, 900, "Chapter 900", "test:model", SOURCE, [CANDIDATE])
    kept = core.persist_inline_candidates(
        db, book_id, 900, "Chapter 900", "test:model", SOURCE, [CANDIDATE])
    assert [c["term_zh"] for c in kept] == ["刍狗"]
    assert len(db.list_footnote_candidates(book_id, chapter=900)) == 1
