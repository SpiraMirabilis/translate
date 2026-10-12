"""Research health/config checks without network connections."""
import io
import json
from types import SimpleNamespace

import pytest

import research_tools as research


@pytest.fixture(autouse=True)
def clear_cache():
    research._reachable_cache.clear()
    yield
    research._reachable_cache.clear()


@pytest.mark.parametrize("health,expected", [
    ({"ok": True, "read_only": True, "research_scope_version": 1}, True),
    ({"ok": True, "read_only": False, "research_scope_version": 1}, False),
    ({"ok": True, "read_only": True}, False),
    ({"ok": True, "read_only": True, "research_scope_version": 2}, False),
    ({"ok": False, "read_only": True, "research_scope_version": 1}, False),
    ([], False), (None, False),
])
def test_health_requires_readonly_scoped_server(monkeypatch, health, expected):
    calls = []

    def open_request(request, timeout):
        calls.append((request.full_url, timeout))
        response = io.BytesIO(json.dumps(health).encode())
        response.geturl = lambda: request.full_url
        return response

    monkeypatch.setattr(research, "build_opener", lambda handler: SimpleNamespace(open=open_request))
    for _ in range(2):
        if expected:
            assert research._mcp_reachable("http://127.0.0.1:8766/mcp") is True
        else:
            with pytest.raises(research.ResearchCompatibilityError, match="Restart the read-only"):
                research._mcp_reachable("http://127.0.0.1:8766/mcp")
    assert calls == [("http://127.0.0.1:8766/health", 0.5)]


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), OSError()])
def test_health_connectivity_errors_disable_tools(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(research, "build_opener", lambda handler: SimpleNamespace(open=fail))
    assert research._mcp_reachable("http://127.0.0.1:1/mcp") is False


@pytest.mark.parametrize("available", [False, True])
def test_health_cache_expires_and_rechecks_availability(monkeypatch, available):
    now = [100.0]
    probes = []
    monkeypatch.setattr(research.time, "monotonic", lambda: now[0])

    def open_request(request, timeout):
        probes.append(request.full_url)
        if not (available if len(probes) == 1 else not available):
            raise ConnectionError()
        response = io.BytesIO(json.dumps({
            "ok": True, "read_only": True,
            "research_scope_version": 1}).encode())
        response.geturl = lambda: request.full_url
        return response

    monkeypatch.setattr(research, "build_opener", lambda handler: SimpleNamespace(open=open_request))
    url = "http://127.0.0.1:8766/mcp"
    assert research._mcp_reachable(url) is available
    now[0] += 29
    assert research._mcp_reachable(url) is available
    assert len(probes) == 1
    now[0] += 2
    assert research._mcp_reachable(url) is not available
    assert len(probes) == 2


def test_probe_does_not_follow_redirects():
    assert research._NoRedirect().redirect_request(None, None, 302, "", {}, "http://other/") is None


@pytest.mark.parametrize("failure", ["missing_health", "invalid_json", "redirected"])
def test_old_or_invalid_health_contract_is_visible(monkeypatch, failure):
    from urllib.error import HTTPError

    def open_request(request, timeout):
        if failure == "missing_health":
            raise HTTPError(request.full_url, 404, "Not Found", {}, None)
        response = io.BytesIO(b"not json" if failure == "invalid_json" else b"{}")
        response.geturl = lambda: "http://other/health" if failure == "redirected" else request.full_url
        return response

    monkeypatch.setattr(research, "build_opener", lambda handler: SimpleNamespace(open=open_request))
    with pytest.raises(research.ResearchCompatibilityError, match="Restart the read-only"):
        research._mcp_reachable("http://localhost:8766/mcp")


@pytest.mark.parametrize("changes", [{"book_id": None}, {"book_id": False}, {"chapter_number": ""},
    {"chapter_number": 0}, {"chapter_number": 1.5}, {"url": "file:///secret"},
    {"url": "https://user:pass@example.org/mcp"}, {"url": "http://localhost/mcp?redirect=yes"}])
def test_invalid_config_is_not_exposed(changes):
    tools = {"book_id": 1, "chapter_number": 2, "url": "http://localhost:8766/mcp"}
    assert not research.valid_research_config({**tools, **changes})


def test_prompt_describes_current_fields_and_historic_notes():
    text = research.mcp_tools_section({"book_id": 1, "chapter_number": 2})
    assert "Gender and category reflect current truth" in text
    assert "including legacy notes" in text
    assert "Filter by category when useful" in text
    assert "t9_search_chapters" in text and "t9_get_book" in text


@pytest.mark.parametrize("provider_module", ["providers.codex_provider", "providers.claude_code_provider"])
@pytest.mark.parametrize("stream", [False, True])
def test_incompatible_service_is_visible_to_both_providers(monkeypatch, provider_module, stream):
    import importlib
    module = importlib.import_module(provider_module)
    provider_type = module.CodexProvider if "codex" in provider_module else module.ClaudeCodeProvider
    def fail(url):
        raise research.ResearchCompatibilityError("Restart the read-only MCP service after upgrading T9")
    monkeypatch.setattr(module, "_mcp_reachable", fail)
    with pytest.raises(research.ResearchCompatibilityError, match="Restart"):
        provider_type(bin_path="unused-test-cli").chat_completion([], model="test", stream=stream, mcp_tools={
            "book_id": 1, "chapter_number": 2, "url": "http://localhost:8766/mcp"})
