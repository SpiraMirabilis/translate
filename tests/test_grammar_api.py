"""Grammar/spell-check API tests: /api/grammar/{status,check,polish,dictionary}.

LanguageTool is mocked at the httpx layer (the module-level `httpx` import in
web.api.grammar); the polish provider is mocked via _config.get_client. The
dictionary endpoint runs against the real tmp SQLite DB.
"""
import json
from types import SimpleNamespace

import httpx
import pytest

import web.api.grammar as grammar_mod


@pytest.fixture
def grammar_client(web_app, admin_client):
    """admin_client with grammar enabled on the app's live config object."""
    grammar_mod._config.grammar_check_enabled = True
    grammar_mod._config.languagetool_url = "http://127.0.0.1:9"
    grammar_mod._config.grammar_language = "en-US"
    grammar_mod._config.polish_model = "test:polish"
    return admin_client


class FakeLTClient:
    """Stands in for httpx.Client; returns a canned /v2/check payload."""
    payload = {"matches": []}
    raise_connect = False

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, data=None):
        if FakeLTClient.raise_connect:
            raise httpx.ConnectError("refused")
        FakeLTClient.last_data = data
        return SimpleNamespace(status_code=200, json=lambda: FakeLTClient.payload,
                               text=json.dumps(FakeLTClient.payload))

    def get(self, url):
        if FakeLTClient.raise_connect:
            raise httpx.ConnectError("refused")
        return SimpleNamespace(status_code=200)


@pytest.fixture
def fake_lt(monkeypatch):
    FakeLTClient.payload = {"matches": []}
    FakeLTClient.raise_connect = False
    monkeypatch.setattr(grammar_mod.httpx, "Client", FakeLTClient)
    return FakeLTClient


def lt_match(offset, length, rule_id="MORFOLOGIK_RULE_EN_US", category="TYPOS",
             message="Possible spelling mistake found.", replacements=("their",)):
    return {
        "offset": offset, "length": length, "message": message,
        "shortMessage": "Spelling mistake",
        "replacements": [{"value": r} for r in replacements],
        "rule": {"id": rule_id, "category": {"id": category, "name": category.title()}},
    }


# ------------------------------------------------------------------
# /status and gating
# ------------------------------------------------------------------

def test_status_disabled_by_default(admin_client):
    grammar_mod._config.grammar_check_enabled = False
    res = admin_client.get("/api/grammar/status")
    assert res.status_code == 200
    assert res.json() == {"enabled": False, "languagetool_up": False}


def test_check_503_when_disabled(admin_client):
    grammar_mod._config.grammar_check_enabled = False
    res = admin_client.post("/api/grammar/check", json={"blocks": ["Hello."]})
    assert res.status_code == 503
    assert "disabled" in res.json()["detail"]


def test_check_503_when_lt_down(grammar_client, fake_lt):
    fake_lt.raise_connect = True
    res = grammar_client.post("/api/grammar/check", json={"blocks": ["Hello."]})
    assert res.status_code == 503
    assert "unreachable" in res.json()["detail"]


def test_requires_auth(web_app):
    from tests.api_client import SyncASGIClient
    res = SyncASGIClient(web_app).get("/api/grammar/status")
    assert res.status_code == 401


# ------------------------------------------------------------------
# /check: block-offset mapping + suppression
# ------------------------------------------------------------------

def test_check_maps_global_offsets_to_blocks(grammar_client, fake_lt):
    blocks = ["First block here.", "Second blok text."]
    # "blok" begins at global offset len(blocks[0]) + 2 + 7
    goff = len(blocks[0]) + 2 + 7
    fake_lt.payload = {"matches": [lt_match(goff, 4)]}
    res = grammar_client.post("/api/grammar/check", json={"blocks": blocks})
    assert res.status_code == 200
    body = res.json()
    assert len(body["matches"]) == 1
    m = body["matches"][0]
    assert (m["block"], m["offset"], m["length"]) == (1, 7, 4)
    assert m["type"] == "typo"
    assert m["replacements"] == ["their"]
    assert blocks[1][m["offset"]:m["offset"] + m["length"]] == "blok"


def test_check_drops_separator_spanning_match(grammar_client, fake_lt):
    blocks = ["abc", "def"]
    fake_lt.payload = {"matches": [lt_match(2, 4)]}  # spans "c\n\nd"
    res = grammar_client.post("/api/grammar/check", json={"blocks": blocks})
    assert res.json()["matches"] == []


def test_check_suppresses_known_entity_spellings(grammar_client, fake_lt, db):
    db.add_entity("organizations", "军事科技", "Millitech", book_id=1)
    blocks = ["Millitech built the arm."]
    fake_lt.payload = {"matches": [lt_match(0, 9)]}  # flags "Millitech"
    res = grammar_client.post("/api/grammar/check", json={"blocks": blocks, "book_id": 1})
    body = res.json()
    assert body["matches"] == []
    assert body["filtered"] == 1
    # Without book_id there is no suppression.
    res2 = grammar_client.post("/api/grammar/check", json={"blocks": blocks})
    assert len(res2.json()["matches"]) == 1


def test_suppression_is_spelling_only_and_case_insensitive(grammar_client, fake_lt, db):
    db.add_entity("characters", "张三", "Millitech", book_id=2)
    blocks = ["millitech is working here."]
    grammar_match = lt_match(0, 9, rule_id="EN_A_VS_AN", category="GRAMMAR")
    typo_match = lt_match(0, 9)
    fake_lt.payload = {"matches": [grammar_match, typo_match]}
    res = grammar_client.post("/api/grammar/check", json={"blocks": blocks, "book_id": 2})
    body = res.json()
    # Grammar rule survives; lowercase typo hit on a known term is suppressed.
    assert len(body["matches"]) == 1
    assert body["matches"][0]["type"] == "grammar"
    assert body["filtered"] == 1


def test_check_413_on_oversize(grammar_client, fake_lt):
    res = grammar_client.post(
        "/api/grammar/check", json={"blocks": ["x" * (grammar_mod.MAX_CHECK_CHARS + 1)]})
    assert res.status_code == 413


def test_multiword_entity_words_are_known(grammar_client, fake_lt, db):
    db.add_entity("characters", "陆青云", "Lu Qingyun", book_id=3)
    blocks = ["Qingyun smiled."]
    fake_lt.payload = {"matches": [lt_match(0, 7)]}
    res = grammar_client.post("/api/grammar/check", json={"blocks": blocks, "book_id": 3})
    assert res.json()["filtered"] == 1


# ------------------------------------------------------------------
# /polish
# ------------------------------------------------------------------

class FakeProvider:
    def __init__(self, content, finish_reason="stop"):
        self._content = content
        self._finish = finish_reason
        self.calls = []

    def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self._content},
                             "finish_reason": self._finish}]}

    def get_response_content(self, response):
        return response["choices"][0]["message"]["content"]


def install_provider(monkeypatch, provider):
    monkeypatch.setattr(grammar_mod._config, "get_client",
                        lambda spec: (provider, "fake-model"), raising=False)


def test_polish_returns_validated_suggestions(grammar_client, monkeypatch):
    text = "He had went to the pavilion. She saw she saw the moon."
    content = json.dumps({"suggestions": [
        {"find": "had went", "replace": "had gone", "reason": "past participle"},
        {"find": "not in the text", "replace": "x", "reason": "hallucinated"},
        {"find": "saw", "replace": "saw", "reason": "no-op dropped"},
    ]})
    provider = FakeProvider(content)
    install_provider(monkeypatch, provider)
    res = grammar_client.post("/api/grammar/polish", json={"text": text})
    assert res.status_code == 200
    body = res.json()
    assert body["truncated"] is False
    assert len(body["suggestions"]) == 1
    s = body["suggestions"][0]
    assert s["find"] == "had went" and s["replace"] == "had gone"
    assert s["occurrences"] == 1
    # System prompt asks for JSON-only suggestions
    sys_msg = provider.calls[0]["messages"][0]
    assert sys_msg["role"] == "system" and "find" in sys_msg["content"]


def test_polish_injects_canonical_terms(grammar_client, monkeypatch, db):
    db.add_entity("characters", "张羽", "Zhang Yu", book_id=7)
    provider = FakeProvider(json.dumps({"suggestions": []}))
    install_provider(monkeypatch, provider)
    res = grammar_client.post("/api/grammar/polish",
                              json={"text": "Zhang Yu waited.", "book_id": 7})
    assert res.status_code == 200
    assert "Zhang Yu" in provider.calls[0]["messages"][0]["content"]


def test_polish_salvages_truncated_json(grammar_client, monkeypatch):
    text = "alpha beta gamma delta"
    cut = ('{"suggestions": [{"find": "alpha", "replace": "Alpha", "reason": "caps"},'
           ' {"find": "beta", "repl')
    install_provider(monkeypatch, FakeProvider(cut, finish_reason="length"))
    res = grammar_client.post("/api/grammar/polish", json={"text": text})
    assert res.status_code == 200
    body = res.json()
    assert body["truncated"] is True
    assert len(body["suggestions"]) == 1
    assert body["suggestions"][0]["find"] == "alpha"


def test_polish_fenced_json_ok(grammar_client, monkeypatch):
    content = '```json\n{"suggestions": [{"find": "teh", "replace": "the", "reason": "typo"}]}\n```'
    install_provider(monkeypatch, FakeProvider(content))
    res = grammar_client.post("/api/grammar/polish", json={"text": "teh cat"})
    assert len(res.json()["suggestions"]) == 1


def test_polish_invalid_json_502(grammar_client, monkeypatch):
    install_provider(monkeypatch, FakeProvider("I refuse to answer in JSON."))
    res = grammar_client.post("/api/grammar/polish", json={"text": "some text"})
    assert res.status_code == 502


def test_polish_400_and_413(grammar_client, monkeypatch):
    install_provider(monkeypatch, FakeProvider("{}"))
    assert grammar_client.post("/api/grammar/polish", json={"text": "  "}).status_code == 400
    big = "y" * (grammar_mod.MAX_POLISH_CHARS + 1)
    assert grammar_client.post("/api/grammar/polish", json={"text": big}).status_code == 413


# ------------------------------------------------------------------
# /dictionary
# ------------------------------------------------------------------

def test_dictionary_add_book_scoped_and_idempotent(grammar_client, db):
    res = grammar_client.post("/api/grammar/dictionary",
                              json={"word": "Millitech", "book_id": 5})
    assert res.status_code == 200
    res2 = grammar_client.post("/api/grammar/dictionary",
                               json={"word": "Millitech", "book_id": 5})
    assert res2.status_code == 200
    with db._conn(dict_rows=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT category, translation, book_id FROM entities "
                    "WHERE untranslated = ?", ("Millitech",))
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["category"] == "dictionary"
    assert rows[0]["translation"] == "Millitech"
    assert rows[0]["book_id"] == 5


def test_dictionary_add_global(grammar_client, db):
    res = grammar_client.post("/api/grammar/dictionary",
                              json={"word": "Arasaka", "book_id": None})
    assert res.status_code == 200
    with db._conn(dict_rows=True) as conn:
        cur = conn.cursor()
        cur.execute("SELECT book_id FROM entities WHERE untranslated = ?", ("Arasaka",))
        rows = cur.fetchall()
    assert len(rows) == 1 and rows[0]["book_id"] is None


def test_dictionary_validation(grammar_client):
    assert grammar_client.post("/api/grammar/dictionary",
                               json={"word": "  "}).status_code == 400
    assert grammar_client.post("/api/grammar/dictionary",
                               json={"word": "two words"}).status_code == 400
    assert grammar_client.post("/api/grammar/dictionary",
                               json={"word": "x" * 101}).status_code == 400
    # Hyphens and apostrophes are allowed
    assert grammar_client.post("/api/grammar/dictionary",
                               json={"word": "Night-City"}).status_code == 200
