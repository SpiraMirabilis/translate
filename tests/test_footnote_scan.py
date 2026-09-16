"""Unit tests for the footnote-candidate scanner — the pure core functions
(footnote_scan_core), the CLI helpers, the main-DB candidate store
(FootnoteCandidatesRepo) and the on-ingest module hook. No model calls."""
import json
import os

import pytest

from footnote_scan import (load_candidates, parse_chapter_spec, parse_delay,
                           plan_scan)
from footnote_scan_core import (EMPTY_REPLY_RETRIES, MODE_INGEST, MODE_SETTING,
                                PROMPT_SETTING,
                                SCAN_MODULE_ID, SYSTEM_PROMPT, SCAN_RULES,
                                OUTPUT_STANDALONE, OUTPUT_INLINE, scan_rules,
                                CoveredRegistry,
                                ScanReplyError, book_scan_prompt,
                                build_user_prompt, call_model, chunk_lines,
                                dedup_candidates, dedupe_first_mention,
                                first_mention_key, pairs_from_footnote_rows,
                                parse_model_response, resolve_system_prompt,
                                verify_candidates)


# ── parse_chapter_spec ────────────────────────────────────────────────────────

def test_spec_none_matches_all():
    assert parse_chapter_spec(None) is None
    assert parse_chapter_spec("") is None


@pytest.mark.parametrize("expr,yes,no", [
    ("42", [42], [41, 43]),
    ("1-50", [1, 25, 50], [0, 51]),
    ("50-1", [1, 50], [51]),           # reversed range normalises
    (">100", [101, 999], [100, 1]),
    (">=100", [100, 101], [99]),
    ("<5", [1, 4], [5, 6]),
    ("<=5", [5, 1], [6]),
    ("=7", [7], [6, 8]),
    ("42,44,46", [42, 44, 46], [43, 45]),
    ("1-3,>10", [1, 3, 11], [4, 10]),
    (" 2 , 5-6 ", [2, 5, 6], [3, 4, 7]),
])
def test_spec_forms(expr, yes, no):
    pred = parse_chapter_spec(expr)
    for n in yes:
        assert pred(n), f"{expr!r} should match {n}"
    for n in no:
        assert not pred(n), f"{expr!r} should not match {n}"


@pytest.mark.parametrize("expr", ["abc", "1-", "-5", "5..9", ">x", ",", "1,,x"])
def test_spec_malformed(expr):
    with pytest.raises(ValueError):
        parse_chapter_spec(expr)


# ── parse_delay ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("expr,seconds", [
    (None, 0.0),
    ("", 0.0),
    ("5", 5.0),
    ("5s", 5.0),
    ("5S", 5.0),
    ("1.5s", 1.5),
    ("500ms", 0.5),
    ("1m", 60.0),
    ("1.5m", 90.0),
    ("1h", 3600.0),
    (" 2 s ", 2.0),
])
def test_parse_delay(expr, seconds):
    assert parse_delay(expr) == seconds


@pytest.mark.parametrize("expr", ["s", "5x", "ms", "abc", "-5", "5ss"])
def test_parse_delay_malformed(expr):
    with pytest.raises(ValueError):
        parse_delay(expr)


# ── parse_model_response ──────────────────────────────────────────────────────

GOOD_ITEM = {"term_zh": "观音土", "term_en": "Guanyin clay",
             "body": "Guanyin clay (观音土): famine clay.", "sentence": "她吃了观音土。"}


def test_parse_plain_array():
    out = parse_model_response(json.dumps([GOOD_ITEM], ensure_ascii=False))
    assert out == [GOOD_ITEM]


def test_parse_empty_array():
    assert parse_model_response("[]") == []
    assert parse_model_response("  []  ") == []


def test_parse_code_fenced():
    raw = "```json\n" + json.dumps([GOOD_ITEM], ensure_ascii=False) + "\n```"
    assert parse_model_response(raw) == [GOOD_ITEM]


def test_parse_drops_malformed_items():
    raw = json.dumps([GOOD_ITEM, "junk", {"term_en": "no body"},
                      {"body": "  "}, {"body": "only body"}], ensure_ascii=False)
    out = parse_model_response(raw)
    assert len(out) == 2
    assert out[0] == GOOD_ITEM
    assert out[1] == {"term_zh": "", "term_en": "", "body": "only body",
                      "sentence": ""}


def test_parse_strips_whitespace():
    raw = json.dumps([{"term_zh": " 画皮 ", "term_en": " The Painted Skin ",
                       "body": " tale. ", "sentence": " s "}], ensure_ascii=False)
    assert parse_model_response(raw)[0] == {
        "term_zh": "画皮", "term_en": "The Painted Skin",
        "body": "tale.", "sentence": "s"}


def test_parse_rejects_non_list():
    with pytest.raises(ValueError):
        parse_model_response('{"a": 1}')


def test_parse_rejects_garbage():
    with pytest.raises(json.JSONDecodeError):
        parse_model_response("I found no referents in this chapter.")


# ── call_model (empty / unparseable replies) ─────────────────────────────────

class FakeProvider:
    """Returns the queued replies in order; each is (content, finish_reason)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat_completion(self, model, messages, **kwargs):
        self.calls.append(messages)
        content, reason = self.replies.pop(0)
        return {"choices": [{"message": {"content": content, "role": "assistant"},
                             "finish_reason": reason}],
                "usage": {"completion_tokens": 8192}}

    def get_response_content(self, response):
        return response["choices"][0]["message"]["content"]


ARRAY = json.dumps([GOOD_ITEM], ensure_ascii=False)


def test_call_model_retries_an_empty_reply():
    # A reasoning model burning its whole budget on thinking returns "" with
    # finish_reason "length"; the next draw fits.
    p = FakeProvider(("", "length"), (None, "length"), (ARRAY, "stop"))
    assert call_model(p, "m", "prompt", 0, quiet=True) == [GOOD_ITEM]
    assert len(p.calls) == 3
    # Retries resend the original prompt — never a nudge with an empty
    # assistant turn (which several APIs reject outright).
    assert all(len(m) == 2 for m in p.calls)


def test_call_model_reports_truncation_not_a_json_error():
    p = FakeProvider(*[("", "length")] * (EMPTY_REPLY_RETRIES + 1))
    with pytest.raises(ScanReplyError) as exc:
        call_model(p, "m", "prompt", 0, quiet=True)
    msg = str(exc.value)
    assert "empty reply" in msg and "finish_reason=length" in msg
    assert "max_output_tokens" in msg


def test_call_model_nudges_once_then_quotes_the_reply():
    p = FakeProvider(("Here you go:", "stop"), ("still prose", "stop"))
    with pytest.raises(ScanReplyError) as exc:
        call_model(p, "m", "prompt", 0, quiet=True)
    assert "still prose" in str(exc.value)
    assert len(p.calls) == 2
    assert p.calls[1][-2]["role"] == "assistant"  # the nudge quotes the reply


def test_call_model_nudge_recovers():
    p = FakeProvider(("Sure! ```json\nnot json```", "stop"), (ARRAY, "stop"))
    assert call_model(p, "m", "prompt", 0, quiet=True) == [GOOD_ITEM]


# ── chunk_lines ───────────────────────────────────────────────────────────────

def test_chunk_single():
    lines = ["aaa", "bbb", "ccc"]
    assert chunk_lines(lines, 1000) == ["aaa\nbbb\nccc"]


def test_chunk_splits_and_preserves_text():
    lines = ["a" * 40, "b" * 40, "c" * 40]
    chunks = chunk_lines(lines, 90)
    assert len(chunks) == 2
    assert "\n".join(chunks) == "\n".join(lines)


def test_chunk_oversized_line_alone():
    chunks = chunk_lines(["x" * 500, "y"], 100)
    assert chunks == ["x" * 500, "y"]


# ── hallucination filter ──────────────────────────────────────────────────────

SOURCE = ("她饿极了，只好去挖观音土充饥。\n"
          "老人讲起《画皮》的故事，声音发抖。\n"
          "那位大人向来先涨后奏，谁也拦不住。\n"
          "公司搞的是 996，谁都心知肚明。")


def _cand(term_zh, sentence="", term_en="X"):
    return {"term_zh": term_zh, "term_en": term_en, "body": "b",
            "sentence": sentence}


def test_verify_keeps_terms_present_in_source():
    kept, dropped = verify_candidates(
        [_cand("观音土"), _cand("画皮")], SOURCE)
    assert [c["term_zh"] for c in kept] == ["观音土", "画皮"]
    assert dropped == []


def test_verify_drops_unanchored_term():
    """Nowhere in the chapter, and no sentence to locate it: a fabrication."""
    kept, dropped = verify_candidates([_cand("白蛇传")], SOURCE)
    assert kept == []
    assert [c["term_zh"] for c in dropped] == ["白蛇传"]


def test_verify_drops_unanchored_term_with_invented_sentence():
    kept, dropped = verify_candidates(
        [_cand("白蛇传", sentence="他讲起了白蛇传的传说。")], SOURCE)
    assert kept == [] and len(dropped) == 1


def test_verify_ignores_punctuation_and_line_breaks():
    kept, _ = verify_candidates(
        [_cand("挖观音土，充饥")], "她饿极了，只好去挖观音\n土充饥。")
    assert len(kept) == 1


def test_verify_keeps_twisted_idiom_named_by_its_original():
    """The page says 先涨后奏; the model reports the idiom it puns on."""
    kept, dropped = verify_candidates([_cand("先斩后奏")], SOURCE)
    assert len(kept) == 1 and dropped == []


def test_verify_one_swap_pass_needs_idiom_length():
    # 观音土 is 3 chars, so 观音水 gets no fuzzy pass, and there is no sentence
    # to anchor it either.
    kept, dropped = verify_candidates([_cand("观音水")], SOURCE)
    assert kept == [] and len(dropped) == 1


def test_verify_two_swaps_needs_a_sentence_anchor():
    assert verify_candidates([_cand("先斩后报")], SOURCE)[1]      # dropped
    # ...but the same term quoting the real page is a parodied idiom, not a lie
    kept, _ = verify_candidates(
        [_cand("先斩后报", sentence="那位大人向来先涨后奏，谁也拦不住。")], SOURCE)
    assert len(kept) == 1


def test_verify_keeps_discontinuous_idiom_via_quoted_sentence():
    """薅羊毛 is on the page as 薅一薅公司的羊毛 — the term is never contiguous."""
    src = "张羽轻松道：“我们顺手薅一薅公司的羊毛。”"
    kept, dropped = verify_candidates(
        [_cand("薅羊毛", sentence="张羽轻松道：“我们顺手薅一薅公司的羊毛。”")], src)
    assert len(kept) == 1 and dropped == []


def test_verify_keeps_multi_part_term_when_all_parts_present():
    src = "希望能有伯乐认识自己这一匹千里马。"
    kept, dropped = verify_candidates(
        [_cand("伯乐 / 千里马"), _cand("伯乐 / 汗血宝马")], src)
    assert [c["term_zh"] for c in kept] == ["伯乐 / 千里马"]
    assert [c["term_zh"] for c in dropped] == ["伯乐 / 汗血宝马"]


def test_verify_non_cjk_term():
    kept, dropped = verify_candidates([_cand("996"), _cand("007")], SOURCE)
    assert [c["term_zh"] for c in kept] == ["996"]
    assert [c["term_zh"] for c in dropped] == ["007"]


def test_verify_termless_candidate_falls_back_to_sentence():
    kept, dropped = verify_candidates(
        [_cand("", sentence="老人讲起《画皮》的故事，声音发抖。"),
         _cand("", sentence="他掏出了定海神针。"),
         _cand("", sentence="")], SOURCE)
    assert len(kept) == 1 and len(dropped) == 2


# ── plan_scan ─────────────────────────────────────────────────────────────────

def _job(ch, h="h1"):
    return {"chapter": ch, "content_hash": h, "title": f"t{ch}"}


def test_plan_fresh_all_scanned():
    jobs = [_job(1), _job(2)]
    to_scan, skipped, stale = plan_scan(jobs, {}, force=False)
    assert to_scan == jobs and skipped == [] and stale == []


def test_plan_skips_scanned_same_hash():
    jobs = [_job(1), _job(2)]
    scans = {1: {"content_hash": "h1"}}
    to_scan, skipped, stale = plan_scan(jobs, scans, force=False)
    assert to_scan == [jobs[1]] and skipped == [jobs[0]] and stale == []


def test_plan_rescans_on_hash_change():
    jobs = [_job(1, "NEW")]
    scans = {1: {"content_hash": "OLD"}}
    to_scan, skipped, stale = plan_scan(jobs, scans, force=False)
    assert to_scan == jobs and stale == jobs and skipped == []


def test_plan_force_rescans_everything():
    jobs = [_job(1)]
    scans = {1: {"content_hash": "h1"}}
    to_scan, skipped, stale = plan_scan(jobs, scans, force=True)
    assert to_scan == jobs and skipped == [] and stale == []


# ── candidate store round-trip (main-DB repo) ─────────────────────────────────

@pytest.fixture
def store(db):
    """(db, book_a_id, book_b_id) — DatabaseManager plus two real books."""
    a = db.create_book("Store Book A")
    b = db.create_book("Store Book B")
    return db, a, b


def _found(term_zh, term_en, body="b", status=None):
    f = {"term_zh": term_zh, "term_en": term_en, "body": body, "sentence": "s"}
    if status:
        f["status"] = status
    return f


def _record(db, book_id, job, model, found):
    assert db.record_footnote_scan(book_id, job["chapter"], job["title"],
                                   model, job["content_hash"], found)


def test_record_and_reload(store):
    db, a, _ = store
    _record(db, a, _job(5), "claude:test",
            [_found("观音土", "Guanyin clay"), _found("画皮", "The Painted Skin")])
    rows = load_candidates(db, a)
    assert [r["term_en"] for r in rows] == ["Guanyin clay", "The Painted Skin"]
    assert all(r["status"] == "pending" and r["chapter_number"] == 5 for r in rows)
    scans = db.get_footnote_scans(a)
    assert scans[5]["n_found"] == 2 and scans[5]["content_hash"] == "h1"


def test_record_zero_find_still_guards(store):
    db, a, _ = store
    _record(db, a, _job(6), "claude:test", [])
    assert load_candidates(db, a) == []
    assert db.get_footnote_scans(a)[6]["n_found"] == 0
    # and plan_scan now skips it
    to_scan, skipped, _ = plan_scan([_job(6)], db.get_footnote_scans(a), force=False)
    assert to_scan == [] and len(skipped) == 1


def test_rescan_replaces_not_duplicates(store):
    db, a, _ = store
    _record(db, a, _job(5), "claude:test", [_found("a", "A")])
    _record(db, a, _job(5, "h2"), "claude:test",
            [_found("b", "B"), _found("c", "C")])
    rows = load_candidates(db, a)
    assert [r["term_en"] for r in rows] == ["B", "C"]
    assert db.get_footnote_scans(a)[5]["content_hash"] == "h2"


def test_candidates_scoped_by_book(store):
    db, a, b = store
    _record(db, a, _job(1), "m", [_found("a", "A")])
    _record(db, b, _job(1), "m", [_found("b", "B")])
    assert [r["term_en"] for r in load_candidates(db, a)] == ["A"]


def test_load_candidates_chapter_filter(store):
    db, a, _ = store
    _record(db, a, _job(1), "m", [_found("a", "A")])
    _record(db, a, _job(9), "m", [_found("b", "B")])
    pred = parse_chapter_spec(">5")
    assert [r["term_en"] for r in load_candidates(db, a, pred)] == ["B"]


def test_record_preserves_imported_status(store):
    # The import script replays reviewed rows — statuses must survive; an
    # unknown status is coerced to pending, never stored raw.
    db, a, _ = store
    _record(db, a, _job(3), "m", [_found("a", "A", status="accepted"),
                                  _found("b", "B", status="rejected"),
                                  _found("c", "C", status="bogus")])
    assert [r["status"] for r in load_candidates(db, a)] == \
        ["accepted", "rejected", "pending"]


def test_update_candidate_status_and_edits(store):
    db, a, _ = store
    _record(db, a, _job(5), "m", [_found("观音土", "Guanyin clay")])
    cand = load_candidates(db, a)[0]
    assert db.update_footnote_candidate(cand["id"], status="accepted",
                                        term_en="Guanyin Clay", body="edited")
    got = db.get_footnote_candidate(cand["id"])
    assert (got["status"], got["term_en"], got["body"]) == \
        ("accepted", "Guanyin Clay", "edited")
    # invalid status is refused outright
    assert db.update_footnote_candidate(cand["id"], status="kept") is False
    assert db.get_footnote_candidate(cand["id"])["status"] == "accepted"
    # no-field call is a no-op
    assert db.update_footnote_candidate(cand["id"]) is False


def test_batch_status(store):
    db, a, _ = store
    _record(db, a, _job(5), "m", [_found("a", "A"), _found("b", "B"),
                                  _found("c", "C")])
    ids = [r["id"] for r in load_candidates(db, a)]
    assert db.set_footnote_candidates_status(ids[:2], "rejected") == 2
    assert db.set_footnote_candidates_status([], "rejected") == 0
    assert db.set_footnote_candidates_status(ids, "kept") == 0  # invalid status
    assert [r["status"] for r in load_candidates(db, a)] == \
        ["rejected", "rejected", "pending"]


def test_book_counts(store):
    db, a, b = store
    _record(db, a, _job(5), "m", [_found("a", "A", status="accepted"),
                                  _found("b", "B", status="rejected"),
                                  _found("c", "C")])
    _record(db, a, _job(6, "h6"), "m", [])
    _record(db, b, _job(1), "m", [_found("d", "D")])
    counts = {r["book_id"]: r for r in db.footnote_candidate_book_counts()}
    assert counts[a]["pending"] == 1 and counts[a]["accepted"] == 1 \
        and counts[a]["rejected"] == 1 and counts[a]["total"] == 3
    assert counts[a]["chapters_scanned"] == 2
    assert counts[b]["pending"] == 1 and counts[b]["total"] == 1
    # pending-desc ordering: both have 1 pending → lower book id first
    ordered = db.footnote_candidate_book_counts()
    assert [r["book_id"] for r in ordered] == sorted([a, b])


# ── first-mention dedupe ──────────────────────────────────────────────────────

def _row(id, ch, zh, en):
    return {"id": id, "chapter_number": ch, "term_zh": zh, "term_en": en}


def test_first_mention_key_prefers_zh():
    assert first_mention_key(_row(1, 1, "观音土", "Guanyin Clay")) == "观音土"
    assert first_mention_key(_row(1, 1, "", "Guanyin Clay")) == "guanyin clay"


def test_dedupe_first_mention():
    rows = [_row(1, 5, "观音土", "Guanyin clay"),
            _row(2, 5, "画皮", "The Painted Skin"),
            _row(3, 12, "观音土", "Guanyin clay"),
            _row(4, 40, "观音土", "guanyin CLAY")]  # same zh, different en casing
    firsts, repeats = dedupe_first_mention(rows)
    assert [r["id"] for r in firsts] == [1, 2]
    assert [d["id"] for d in repeats[1]] == [3, 4]


def test_dedupe_falls_back_to_english_casefold():
    rows = [_row(1, 1, "", "Yamen"), _row(2, 9, "", "yamen")]
    firsts, repeats = dedupe_first_mention(rows)
    assert [r["id"] for r in firsts] == [1]
    assert [d["id"] for d in repeats[1]] == [2]


# ── already-covered terms ─────────────────────────────────────────────────────

def test_pairs_from_footnote_rows():
    rows = [
        {"anchor": "Guanyin clay", "source_term": "观音土", "body": "..."},
        {"anchor": "yamen", "source_term": None,
         "body": "yamen (衙门): the office of a local official."},
        {"anchor": "观音土", "source_term": None, "body": "source-side row"},
        {"anchor": "no chinese anywhere", "source_term": None, "body": "n/a"},
    ]
    assert pairs_from_footnote_rows(rows) == [
        ("观音土", "Guanyin clay"), ("衙门", "yamen"), ("观音土", "")]


def test_registry_real_pairs_apply_everywhere():
    reg = CoveredRegistry([("白蛇传", "Legend of the White Snake")], [])
    assert reg.covered_for(1, "远古的白蛇传神话") == \
        [("白蛇传", "Legend of the White Snake")]
    # ...but only when the term actually appears in the chapter text
    assert reg.covered_for(1, "无关的文本") == []


def test_registry_candidates_only_earlier_chapters():
    reg = CoveredRegistry([], [(5, "观音土", "Guanyin clay")])
    text = "她吃了观音土。"
    assert reg.covered_for(6, text) == [("观音土", "Guanyin clay")]
    assert reg.covered_for(5, text) == []   # own chapter re-scan regenerates
    assert reg.covered_for(4, text) == []


def test_registry_add_and_dedup():
    reg = CoveredRegistry([("观音土", "Guanyin clay")], [])
    reg.add(5, [{"term_zh": "观音土", "term_en": "guanyin clay"},
                {"term_zh": "画皮", "term_en": "The Painted Skin"},
                {"term_zh": "", "term_en": "nameless"}])
    got = reg.covered_for(9, "观音土和画皮")
    assert got == [("观音土", "Guanyin clay"), ("画皮", "The Painted Skin")]


def test_covered_block_in_prompt():
    p = build_user_prompt("Book", 9, "T", {}, "text",
                          covered=[("画皮", "The Painted Skin"), ("坠龙", "")])
    assert "ALREADY FOOTNOTED" in p
    assert "画皮 = The Painted Skin" in p
    assert "坠龙\n" in p and "坠龙 =" not in p
    assert "ALREADY FOOTNOTED" not in build_user_prompt("B", 9, "T", {}, "x")


def test_dedup_candidates_removes_pending_repeats(store):
    db, a, _ = store
    _record(db, a, _job(5), "m",
            [_found("观音土", "Guanyin clay"), _found("画皮", "Painted Skin")])
    _record(db, a, _job(7, "h7"), "m",
            [_found("观音土", "Guanyin clay"), _found("坠龙", "fallen dragon")])
    _record(db, a, _job(9, "h9"), "m", [_found("画皮", "Painted Skin")])
    # a hand-reviewed later repeat must survive
    for r in load_candidates(db, a):
        if r["chapter_number"] == 9:
            assert db.update_footnote_candidate(r["id"], status="accepted")
    assert dedup_candidates(db, a) == 1   # only ch7's 观音土 repeat
    terms = [(r["chapter_number"], r["term_zh"])
             for r in load_candidates(db, a)]
    assert terms == [(5, "观音土"), (5, "画皮"), (7, "坠龙"), (9, "画皮")]
    assert dedup_candidates(db, a) == 0   # idempotent (9 is accepted)
    # the pruned chapter's scan row stays honest
    assert db.get_footnote_scans(a)[7]["n_found"] == 1


# ── on-ingest module hook ─────────────────────────────────────────────────────

@pytest.fixture
def scan_jobs(monkeypatch):
    """Capture footnote_scan_worker enqueues instead of spawning the worker."""
    from modules.footnote_scan_module import footnote_scan_worker
    jobs = []
    monkeypatch.setattr(footnote_scan_worker, "enqueue", jobs.append)
    return jobs


def _scan_on_ingest(db, book_id, **extra):
    """Put a book on the second-pass scan. Scanning during translation is the
    default now, and that path never touches the module's worker."""
    settings = {MODE_SETTING: MODE_INGEST}
    settings.update(extra)
    assert db.set_module_settings(book_id, SCAN_MODULE_ID, settings)


def test_new_chapter_does_not_fire_a_second_pass_by_default(db, scan_jobs):
    """Default scan_mode is "translation": the candidates come back on the
    translation response, so ingesting a chapter must not queue a scan of its
    own."""
    book_id = db.create_book("Default Book")
    assert db.update_book(book_id, modules={"footnote_scan": True})
    assert db.save_chapter(book_id, 1, "t", ["中文原文"], ["translated"])
    assert scan_jobs == []


def test_new_chapter_fires_scan_once(db, scan_jobs):
    book_id = db.create_book("Hooked Book")
    assert db.update_book(book_id, modules={"footnote_scan": True})
    _scan_on_ingest(db, book_id)
    assert db.save_chapter(book_id, 1, "t", ["中文原文"], ["translated"])
    assert len(scan_jobs) == 1
    job = scan_jobs[0]
    assert job["book"]["id"] == book_id and job["chapter_number"] == 1
    assert job["model_spec"] == "deepseek:deepseek-v4-pro"
    # a re-save (retranslation / editor save) must NOT re-fire
    assert db.save_chapter(book_id, 1, "t", ["中文原文"], ["retranslated"])
    assert len(scan_jobs) == 1
    # a sourceless chapter (original-work style) doesn't enqueue either
    assert db.save_chapter(book_id, 2, "t", [], ["original prose"])
    assert len(scan_jobs) == 1


def test_scan_input_is_post_transform_source(db, scan_jobs):
    """The scan must always see the FINAL transformed source. Two guarantees:
    apply_source_ingest rewrites the source BEFORE persistence (the hook only
    fires post-commit), and the scan worker re-fetches the chapter from the DB
    rather than trusting anything passed in memory — so a scan can never run
    against pre-transform text regardless of module registry order."""
    pytest.importorskip("opencc")
    book_id = db.create_book("Trad Book")
    assert db.update_book(book_id, modules={"footnote_scan": True,
                                            "trad_to_simp": True})
    _scan_on_ingest(db, book_id)
    assert db.save_chapter(book_id, 1, "t", ["他們去了萬妖之門"], ["translated"])
    assert len(scan_jobs) == 1
    # What the worker will scan is the stored source — already simplified.
    from footnotes import content_to_list
    ch = db.get_chapter(book_id=book_id, chapter_number=1)
    assert content_to_list(ch["untranslated"]) == ["他们去了万妖之门"]
    # And the hook itself was handed the post-transform lines too.
    assert scan_jobs[0]["book"]["id"] == book_id


def test_new_chapter_hook_auto_on_for_zh_source(db, scan_jobs):
    """No module override needed: a Chinese-source book has the scanner on."""
    book_id = db.create_book("Auto Book")  # source_language defaults to zh
    _scan_on_ingest(db, book_id)
    assert db.save_chapter(book_id, 1, "t", ["中文原文"], ["translated"])
    assert len(scan_jobs) == 1


def test_new_chapter_hook_respects_module_toggle(db, scan_jobs):
    # Explicit off beats the zh auto-rule.
    zh_off = db.create_book("Unhooked Book")
    assert db.update_book(zh_off, modules={"footnote_scan": False})
    _scan_on_ingest(db, zh_off)
    assert db.save_chapter(zh_off, 1, "t", ["中文原文"], ["translated"])
    assert scan_jobs == []
    # A non-Chinese source book doesn't auto-enable (nothing for it to find).
    ko = db.create_book("Korean Book", source_language="ko")
    _scan_on_ingest(db, ko)
    assert db.save_chapter(ko, 1, "t", ["한국어 원문"], ["translated"])
    assert scan_jobs == []
    # ...but an explicit on still forces it.
    assert db.update_book(ko, modules={"footnote_scan": True})
    assert db.save_chapter(ko, 2, "t", ["한국어 원문"], ["translated"])
    assert len(scan_jobs) == 1


def test_module_settings_model_override(db, scan_jobs):
    book_id = db.create_book("Custom Model Book")
    assert db.update_book(book_id, modules={"footnote_scan": True})
    _scan_on_ingest(db, book_id, model="claude:claude-opus-4-8")
    assert db.save_chapter(book_id, 1, "t", ["中文原文"], ["translated"])
    assert scan_jobs[0]["model_spec"] == "claude:claude-opus-4-8"


# ── per-book scan prompt ──────────────────────────────────────────────────────

def test_resolve_system_prompt_falls_back_to_stock():
    for blank in (None, "", "   \n "):
        assert resolve_system_prompt(blank) == SYSTEM_PROMPT
    assert resolve_system_prompt("  Find Japanese referents.  ") == \
        "Find Japanese referents.\n\n" + OUTPUT_STANDALONE


def test_stock_rules_come_from_the_prompt_file():
    """The built-in rules are the contents of prompts/footnote_scan_prompt.txt,
    not a literal in the code — editing that file is how they change."""
    import footnote_scan_core as core
    with open(core.SCAN_PROMPT_FILE, encoding="utf-8") as f:
        assert core.stock_scan_rules() == f.read().strip()
    assert SCAN_RULES == core.stock_scan_rules()


def test_an_edited_prompt_file_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    import footnote_scan_core as core
    path = tmp_path / "footnote_scan_prompt.txt"
    path.write_text("First rules.\n", encoding="utf-8")
    monkeypatch.setattr(core, "SCAN_PROMPT_FILE", str(path))
    monkeypatch.setattr(core, "_rules_cache", {"mtime": None, "text": ""})
    assert core.scan_rules() == "First rules."

    os.utime(path, None)  # same content, new mtime — still fine
    path.write_text("Second rules.\n", encoding="utf-8")
    os.utime(path, (0, 0))  # force a different mtime deterministically
    assert core.scan_rules() == "Second rules."

    # A file that goes missing keeps the last text that read cleanly, so a
    # half-finished save can never send the model an empty rule set.
    path.unlink()
    assert core.scan_rules() == "Second rules."


def test_a_missing_prompt_file_fails_loudly_but_not_at_import(tmp_path, monkeypatch):
    """No rules at all would come back confident junk. A book with its own
    prompt is unaffected — it never reads the file."""
    import footnote_scan_core as core
    monkeypatch.setattr(core, "SCAN_PROMPT_FILE", str(tmp_path / "gone.txt"))
    monkeypatch.setattr(core, "_rules_cache", {"mtime": None, "text": ""})
    assert core.stock_scan_rules() == ""
    with pytest.raises(core.ScanPromptError):
        core.resolve_system_prompt()
    assert core.resolve_system_prompt("Find X.") == "Find X.\n\n" + OUTPUT_STANDALONE
    # ...and the inline section degrades to "" rather than failing a chapter.
    assert core.inline_scan_section(None, {"id": 1, "source_language": "zh",
                                           "modules": {}}, None, "中文") == ""


def test_stock_prompt_is_rules_plus_output():
    """SCAN_RULES is the editable half; the output paragraph is appended from
    code. Assembled, the standalone prompt is what it always was."""
    assert SYSTEM_PROMPT == SCAN_RULES + "\n\n" + OUTPUT_STANDALONE
    assert "OUTPUT:" not in SCAN_RULES
    assert "ANCHORING" in SCAN_RULES


def test_a_books_own_output_paragraph_is_replaced_not_obeyed():
    """A prompt customised before the split still carries an output paragraph.
    It must not reach the model, or a book could redefine the shape the parser
    and the hallucination filter depend on."""
    stock_style = ('Find X.\n\nOUTPUT: respond with ONLY a JSON array; each element is\n'
                   '  {"term_zh": "..."}\nNo prose.')
    assert scan_rules(stock_style) == "Find X."
    # Book 79 rewrote it as a Markdown section.
    markdown_style = 'Find X.\n\n# OUTPUT\n\nRespond with ONLY a JSON array.\n\nNo fences.\n'
    assert scan_rules(markdown_style).startswith("Find X.")
    assert "JSON array" not in scan_rules(markdown_style)


def test_an_output_section_about_something_else_is_left_alone():
    """The strip is anchored on the JSON-array wording, so a book that uses the
    word OUTPUT for its own purposes keeps its text."""
    keep = 'Find X.\n\nOUTPUT: keep the tone formal and do not editorialise.'
    assert scan_rules(keep) == keep


def test_inline_output_targets_the_translation_response():
    inline = resolve_system_prompt(inline=True)
    assert inline.startswith(SCAN_RULES)
    assert '"footnote_candidates"' in inline
    assert "PRE-TRANSLATED ENTITIES" in inline
    assert OUTPUT_STANDALONE not in inline
    # A book's custom rules ride the inline channel too.
    assert resolve_system_prompt("Find Japanese referents.", inline=True) == \
        "Find Japanese referents.\n\n" + OUTPUT_INLINE


def test_module_schema_key_matches_core():
    """The module declares the setting; the core reads it back for the CLI.
    A drift between the two would silently ignore every stored prompt."""
    from modules.footnote_scan_module import FootnoteScanModule
    assert PROMPT_SETTING in FootnoteScanModule().default_settings()


def test_book_scan_prompt_roundtrip(db):
    book_id = db.create_book("Prompt Book")
    assert book_scan_prompt(db, book_id) == ""        # nothing stored yet
    assert db.set_module_settings(book_id, SCAN_MODULE_ID,
                                  {PROMPT_SETTING: "  Japanese too.  "})
    assert book_scan_prompt(db, book_id) == "Japanese too."


def test_book_scan_prompt_survives_a_broken_db(db):
    """A settings lookup that blows up must not take the scan down with it."""
    class Boom:
        def get_module_settings(self, *a, **kw):
            raise RuntimeError("no such table")
    assert book_scan_prompt(Boom(), 1) == ""


def test_module_settings_prompt_override(db, scan_jobs):
    book_id = db.create_book("Sega Book")
    assert db.update_book(book_id, modules={"footnote_scan": True})
    _scan_on_ingest(db, book_id, **{PROMPT_SETTING: "Chinese AND Japanese."})
    assert db.save_chapter(book_id, 1, "t", ["中文原文"], ["translated"])
    assert scan_jobs[0]["system_prompt"] == "Chinese AND Japanese."
    # An unset prompt enqueues "" — the worker then sends the stock prompt.
    plain = db.create_book("Plain Book")
    _scan_on_ingest(db, plain)
    assert db.save_chapter(plain, 1, "t", ["中文原文"], ["translated"])
    assert scan_jobs[1]["system_prompt"] == ""
