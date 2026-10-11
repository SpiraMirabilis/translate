"""CodexProvider against a stand-in `codex` executable (no network, no login).

The fake CLI records its argv and stdin, then prints the JSONL event stream
`codex exec --json` would, chosen by FAKE_CODEX_MODE.
"""
import json
import os
import stat
import sys

import pytest

from providers.base import OverloadedError, SessionLimitError
from providers.codex_provider import CodexProvider

FAKE = r'''#!PYTHON
import json, os, sys
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
prompt = sys.stdin.read()
argv = sys.argv[1:]
instr = None
for i, a in enumerate(argv):
    if a == "-c" and argv[i + 1].startswith("model_instructions_file="):
        instr_path = json.loads(argv[i + 1].split("=", 1)[1])
        instr = open(instr_path, encoding="utf-8").read()
with open(os.environ["FAKE_CODEX_LOG"], "w", encoding="utf-8") as f:
    json.dump({"argv": argv, "stdin": prompt, "instructions": instr}, f)

def ev(**e):
    print(json.dumps(e), flush=True)

ev(type="thread.started", thread_id="t1")
ev(type="item.completed", item={"id": "item_0", "type": "error",
   "message": "Codex is ignoring 1 unrecognized configuration setting."})
ev(type="turn.started")
if mode == "ok":
    ev(type="item.completed", item={"id": "item_1", "type": "agent_message", "text": "thinking aloud"})
    ev(type="item.completed", item={"id": "item_2", "type": "agent_message",
       "text": '```json\n{"title": "Chapter 1", "content": ["Zhang San entered the city gate."]}\n```'})
    ev(type="turn.completed", usage={"input_tokens": 4100, "cached_input_tokens": 3000, "output_tokens": 52})
elif mode == "limit":
    ev(type="error", message="You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
       "to purchase more credits or try again at 3:45 PM.")
    ev(type="turn.failed", error={"message": "You've hit your usage limit. Visit https://chatgpt.com/codex/"
       "settings/usage to purchase more credits or try again at 3:45 PM."})
    sys.exit(1)
elif mode == "credits":
    ev(type="turn.failed", error={"message": "Your workspace is out of credits. Add credits to continue using Codex."})
    sys.exit(1)
elif mode == "capacity":
    ev(type="turn.failed", error={"message": "exceeded retry limit, last status: 503 Service Unavailable"})
    sys.exit(1)
elif mode == "broken":
    ev(type="turn.failed", error={"message": "The model `nope` does not exist"})
    sys.exit(1)
elif mode == "crash":
    sys.stderr.write("panic: something went wrong\n")
    sys.exit(101)
'''.replace("PYTHON", sys.executable)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    path = tmp_path / "codex"
    path.write_text(FAKE)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "call.json"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    provider = CodexProvider(bin_path=str(path))

    def call(mode="ok", stream=False, json_mode=True):
        monkeypatch.setenv("FAKE_CODEX_MODE", mode)
        messages = [{"role": "system", "content": "SYSTEM: translate zh->en"},
                    {"role": "user", "content": "第一章\n张三走进了城门。"}]
        rf = {"type": "json_object"} if json_mode else None
        resp = provider.chat_completion(messages, model="gpt-6-luna", stream=stream, response_format=rf)
        if stream:
            chunks = list(resp)
            text = "".join(c for c in (provider.get_streaming_content(ch) for ch in chunks) if c)
            assert provider.is_stream_complete(chunks[-1])
            return text, None
        return provider.get_response_content(resp), resp

    call.log = lambda: json.loads(log.read_text())
    return call


def _sys_tempfiles():
    import tempfile
    d = tempfile.gettempdir()
    return {f for f in os.listdir(d) if f.startswith("t9-codex-sys-")}


def test_returns_last_agent_message_with_fences_stripped_and_usage(fake):
    text, resp = fake("ok")
    assert json.loads(text)["content"] == ["Zhang San entered the city gate."]
    assert resp["usage"] == {"prompt_tokens": 4100, "completion_tokens": 52, "total_tokens": 4152}


def test_streaming_yields_the_answer_then_done(fake):
    text, _ = fake("ok", stream=True)
    assert json.loads(text)["title"] == "Chapter 1"


def test_system_prompt_replaces_instructions_and_user_prompt_goes_to_stdin(fake):
    fake("ok")
    call = fake.log()
    assert call["instructions"] == "SYSTEM: translate zh->en"
    assert call["stdin"].startswith("第一章\n张三走进了城门。")
    assert "valid JSON only" in call["stdin"]
    argv = call["argv"]
    assert argv[:2] == ["exec", "--json"]
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("-m") + 1] == "gpt-6-luna"
    assert argv[-1] == "-"


def test_features_are_disabled_via_config_not_disable_flag(fake):
    # --disable <unknown> is a hard error in codex; -c features.X=false only warns.
    fake("ok")
    argv = fake.log()["argv"]
    assert "--disable" not in argv
    assert "features.shell_tool=false" in argv
    assert 'web_search="disabled"' in argv


def test_system_prompt_tempfile_is_removed(fake):
    before = _sys_tempfiles()
    fake("ok")
    fake("ok", stream=True)
    with pytest.raises(RuntimeError):
        fake("broken")
    assert _sys_tempfiles() <= before


def test_usage_limit_raises_session_limit_with_reset_text(fake):
    with pytest.raises(SessionLimitError) as exc:
        fake("limit")
    assert "try again at 3:45 PM" in exc.value.reset_text


def test_usage_limit_while_streaming(fake):
    with pytest.raises(SessionLimitError):
        fake("limit", stream=True)


def test_out_of_credits_pauses_instead_of_failing(fake):
    with pytest.raises(SessionLimitError):
        fake("credits")


def test_capacity_error_is_retryable_overload(fake):
    with pytest.raises(OverloadedError):
        fake("capacity")


def test_other_failures_are_plain_errors(fake):
    with pytest.raises(RuntimeError, match="does not exist"):
        fake("broken")


def test_crash_without_events_reports_stderr(fake):
    with pytest.raises(RuntimeError, match="panic: something went wrong"):
        fake("crash")


def test_missing_binary_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="codex CLI not found"):
        CodexProvider()


def test_factory_resolves_codex_and_alias():
    from providers.factory import ProviderFactory
    f = ProviderFactory()
    assert f._resolve_provider_name("cx") == "codex"
    assert f.config["providers"]["codex"]["class"] == "CodexProvider"
    assert "api_key_env" not in f.config["providers"]["codex"]
