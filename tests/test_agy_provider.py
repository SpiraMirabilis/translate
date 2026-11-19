"""AgyProvider against a stand-in `agy` executable (no network, no login).

The fake CLI records its argv, cwd, HOME, the custom agent file and its stdin,
writes the per-call state a real agy writes (a conversation, a log, a cache)
under its HOME, then prints the stream-json events `agy -p` would, chosen by
FAKE_AGY_MODE.
"""
import json
import os
import stat
import sys
import time

import pytest

from providers.agy_provider import AGENT_NAME, AgyProvider
from providers.base import ClassifiedProviderError, OverloadedError, SessionLimitError
from providers.factory import ProviderFactory

FAKE = r'''#!PYTHON
import json, os, sys, time
mode = os.environ.get("FAKE_AGY_MODE", "ok")
if mode == "early":
    sys.stderr.write("agy: fatal: sign-in expired, run agy to sign in again\n")
    sys.exit(2)
if mode == "noread":
    time.sleep(30)
stdin = sys.stdin.read()
home = os.environ["HOME"]
agent_file = os.path.join(os.getcwd(), ".agents", "agents", "t9-translate.md")
tok = os.path.join(home, ".gemini", "antigravity-cli", "antigravity-oauth-token")
with open(os.environ["FAKE_AGY_LOG"], "w", encoding="utf-8") as f:
    json.dump({"argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd(), "home": home,
               "call_home": os.environ.get("AGY_CALL_HOME"),
               "xdg": os.environ.get("XDG_CONFIG_HOME"),
               "token_is_link": os.path.islink(tok),
               "token": open(tok).read() if os.path.exists(tok) else None,
               "scope": (open(os.path.join(home, ".config", "antigravity", "credential-user-scope")).read()
                         if os.path.exists(os.path.join(home, ".config", "antigravity", "credential-user-scope")) else None),
               "agent": open(agent_file, encoding="utf-8").read()}, f)
# What a real agy leaves behind for every call.
st = os.path.join(home, ".gemini", "antigravity-cli")
os.makedirs(os.path.join(st, "conversations"), exist_ok=True)
os.makedirs(os.path.join(st, "log"), exist_ok=True)
open(os.path.join(st, "conversations", "c1.db"), "w").write("the whole chapter")
open(os.path.join(st, "log", "cli.log"), "w").write("log")
open(os.path.join(st, "jetbox_summaries_proto.pb"), "w").write("preview of the chapter")
real_tok = os.environ.get("FAKE_REAL_TOKEN")
if mode == "refresh":            # refresh written by rename
    os.unlink(tok)
    open(tok, "w").write('{"t": 2}')
elif mode == "refresh_inplace":  # refresh the way agy 1.5.12 does it (os.WriteFile)
    with open(tok, "w") as f:
        f.write('{"t": 2}')
    with open(os.environ["FAKE_AGY_LOG"] + ".during", "w") as f:
        f.write(open(real_tok).read())
elif mode == "refresh_empty":
    open(tok, "w").close()
elif mode == "refresh_truncated":
    open(tok, "w").write('{"t": ')
elif mode == "refresh_race":     # another call saves a newer login meanwhile
    open(real_tok, "w").write('{"t": "other"}')
    open(tok, "w").write('{"t": 2}')

def ev(**e):
    print(json.dumps(e), flush=True)

ev(event="init", conversation_id="c1", init={"model": "m", "agent": "t9-translate", "tools": ["run_command"]})
ev(event="step_update", step_update={"step_index": 0, "state": "DONE", "step_type": "user_input"})
def delta(t):
    ev(event="step_update", step_update={"step_index": 1, "state": "ACTIVE",
       "step_type": "agent_response", "text_delta": t})
def result(text, **extra):
    ev(event="result", result=dict({"status": "SUCCESS", "response": text,
       "usage": {"input_tokens": 120, "output_tokens": 30}}, **extra))
if mode == "tool":
    ev(event="step_update", step_update={"step_index": 1, "state": "ACTIVE", "step_type": "run_command"})
elif mode.startswith("error:"):
    ev(event="result", result={"status": "ERROR", "response": "", "error": mode[6:], "finished_reason": "error"})
elif mode == "partial":
    delta('{"content":')
    result('{"content":["Translation"]}')
elif mode == "diverge":
    delta('{"content":["Something else"]}')
    result('{"content":["Translation"]}')
elif mode == "slow":
    delta('{"content":')
    time.sleep(60)
else:
    for part in ['{"content":', '["Translation"]}']:
        delta(part)
    result('{"content":["Translation"]}')
'''


@pytest.fixture
def fake_agy(tmp_path, monkeypatch):
    path = tmp_path / "agy"
    path.write_text(FAKE.replace("#!PYTHON", "#!" + sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("FAKE_AGY_MODE", raising=False)
    # The real (persistent) agy home with a file-based sign-in, as in Docker.
    real = tmp_path / "realhome"
    (real / ".config" / "antigravity").mkdir(parents=True)
    (real / ".config" / "antigravity" / "credential-user-scope").write_text("scope")
    (real / ".gemini" / "config").mkdir(parents=True)
    (real / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (real / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").write_text('{"t": 1}')
    (real / ".gemini" / "antigravity-cli" / "settings.json").write_text("{}")
    monkeypatch.setenv("AGY_HOME", str(real))
    monkeypatch.setenv("FAKE_REAL_TOKEN", str(real / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"))
    return str(path), log, real


MESSAGES = [{"role": "system", "content": "# Rules\nTranslate.\n\n# More\nKeep names."},
            {"role": "user", "content": "Translate the following into English: \n你好"}]


def _rec(log):
    return json.loads(log.read_text(encoding="utf-8"))


def _tree(path):
    return sorted(os.path.relpath(os.path.join(d, f), path)
                  for d, _, files in os.walk(path) for f in files)


def test_runs_no_tools_agent(fake_agy):
    bin_path, log, _ = fake_agy
    p = AgyProvider(bin_path=bin_path)
    resp = p.chat_completion(MESSAGES, model="gemini-4-neon-medium")
    assert p.get_response_content(resp) == '{"content":["Translation"]}'
    assert resp["usage"] == {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
    rec = _rec(log)
    argv = rec["argv"]
    assert argv[argv.index("--agent") + 1] == AGENT_NAME
    assert argv[argv.index("--model") + 1] == "gemini-4-neon-medium"
    assert "--sandbox" in argv and "--disable-slash-commands" in argv
    # The system prompt is the agent body, verbatim, with built-ins excluded.
    assert "excludeDefaultComponents: true" in rec["agent"] and "tools: []" in rec["agent"]
    assert rec["agent"].endswith("# Rules\nTranslate.\n\n# More\nKeep names.\n")
    # The user prompt goes in on stdin as one stream-json message.
    assert json.loads(rec["stdin"]) == {"event": "user", "message": {"content": MESSAGES[1]["content"]}}


def test_each_call_gets_a_throwaway_home_with_only_the_sign_in(fake_agy):
    bin_path, log, real = fake_agy
    before = _tree(real)
    AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
    rec = _rec(log)
    assert rec["home"] != str(real) and rec["call_home"] == rec["home"]
    assert rec["xdg"] is None, "XDG_CONFIG_HOME would point agy outside the call home"
    # A private copy, never a link into the real home.
    assert not rec["token_is_link"] and rec["token"] == '{"t": 1}'
    assert rec["scope"] == "scope"
    # Everything agy wrote for the call is gone with the call dir…
    assert not os.path.exists(rec["home"]) and not os.path.exists(rec["cwd"])
    # …and nothing reached the real home but the sign-in lock.
    assert [f for f in _tree(real) if not f.endswith(".t9-login.lock")] == before


def _token(real):
    return real / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"


@pytest.mark.parametrize("mode", ["refresh", "refresh_inplace"])
def test_refreshed_sign_in_is_published(fake_agy, monkeypatch, mode):
    bin_path, log, real = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", mode)
    AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
    tok = _token(real)
    assert tok.read_text() == '{"t": 2}'
    assert stat.S_IMODE(tok.stat().st_mode) == 0o600
    assert not tok.is_symlink()
    assert not [f for f in os.listdir(tok.parent) if f.startswith(".t9-sync-")]
    if mode == "refresh_inplace":
        # agy's in-place write hit the call's copy, not the real token.
        assert open(str(log) + ".during").read() == '{"t": 1}'


@pytest.mark.parametrize("mode", ["refresh_empty", "refresh_truncated"])
def test_invalid_refresh_is_not_published(fake_agy, monkeypatch, mode):
    bin_path, _, real = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", mode)
    AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
    assert _token(real).read_text() == '{"t": 1}'


def test_newer_login_saved_meanwhile_wins(fake_agy, monkeypatch):
    bin_path, _, real = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "refresh_race")
    AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
    assert _token(real).read_text() == '{"t": "other"}'


def test_scope_file_follows_xdg_outside_docker(fake_agy, tmp_path, monkeypatch):
    bin_path, log, _ = fake_agy
    home = tmp_path / "desktophome"
    home.mkdir()
    xdg = tmp_path / "xdgconf"
    (xdg / "antigravity").mkdir(parents=True)
    (xdg / "antigravity" / "credential-user-scope").write_text("xdg-scope")
    monkeypatch.delenv("AGY_HOME")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
    rec = _rec(log)
    assert rec["scope"] == "xdg-scope"
    assert rec["xdg"] is None  # the child resolves ~/.config inside its call home


def test_missing_sign_in_files_are_skipped(fake_agy, tmp_path, monkeypatch):
    """A desktop install keeps its sign-in in the system keyring: no token file
    to link, and the call must still run."""
    bin_path, log, _ = fake_agy
    empty = tmp_path / "keyringhome"
    empty.mkdir()
    monkeypatch.setenv("AGY_HOME", str(empty))
    AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
    assert _rec(log)["token"] is None
    assert os.listdir(empty) == []


def test_streaming_yields_deltas_then_done(fake_agy):
    bin_path, _, _ = fake_agy
    p = AgyProvider(bin_path=bin_path)
    chunks = list(p.chat_completion(MESSAGES, model="m", stream=True))
    texts = [p.get_streaming_content(c) for c in chunks if p.get_streaming_content(c)]
    assert texts == ['{"content":', '["Translation"]}'], "should stream the deltas, not one blob"
    assert p.is_stream_complete(chunks[-1])


def test_stream_emits_tail_missing_from_deltas(fake_agy, monkeypatch):
    bin_path, _, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "partial")
    p = AgyProvider(bin_path=bin_path)
    text = "".join(p.get_streaming_content(c) or "" for c in p.chat_completion(MESSAGES, model="m", stream=True))
    assert text == '{"content":["Translation"]}'


def test_stream_that_disagrees_with_final_fails(fake_agy, monkeypatch):
    bin_path, _, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "diverge")
    p = AgyProvider(bin_path=bin_path)
    with pytest.raises(RuntimeError, match="does not match"):
        list(p.chat_completion(MESSAGES, model="m", stream=True))


def test_closing_a_stream_early_kills_and_cleans_up(fake_agy, monkeypatch):
    bin_path, log, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "slow")
    p = AgyProvider(bin_path=bin_path)
    s = p.chat_completion(MESSAGES, model="m", stream=True)
    next(s)  # first delta; the CLI is now sleeping
    t0 = time.time()
    s.close()
    assert time.time() - t0 < 5, "abort must not wait for a graceful shutdown"
    assert not os.path.exists(_rec(log)["home"])


def test_closing_an_unread_stream_cleans_up(fake_agy, monkeypatch):
    bin_path, _, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "slow")
    import tempfile
    before = {d for d in os.listdir(tempfile.gettempdir()) if d.startswith("t9-agy-")}
    s = AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m", stream=True)
    s.close()
    after = {d for d in os.listdir(tempfile.gettempdir()) if d.startswith("t9-agy-")}
    assert after <= before


def test_unexpected_tool_step_is_refused(fake_agy, monkeypatch):
    bin_path, _, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "tool")
    with pytest.raises(RuntimeError, match="unexpected step"):
        AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")


@pytest.mark.parametrize("error, expected", [
    ("You have reached your weekly quota. It resets on Monday.", SessionLimitError),
    ("Spend cap reached for this project", SessionLimitError),
    ("Your prepaid balance is too low to continue", SessionLimitError),
    ("429 Too Many Requests: rate limit exceeded", OverloadedError),
    ("429 Too Many Requests: per-minute request quota exceeded", OverloadedError),
    ("RESOURCE_EXHAUSTED: quota exceeded", OverloadedError),
    ("You have exhausted your daily quota", SessionLimitError),
    ("write failed: disk quota exceeded", ClassifiedProviderError),
    ("429: quota resets in 30 seconds", OverloadedError),
    ("403 PERMISSION_DENIED: Billing is disabled for this project", ClassifiedProviderError),
    ("Your weekly limit resets Monday", ClassifiedProviderError),  # no exhaustion wording: not a limit notice
    ("503 Service Unavailable", OverloadedError),
    ("model gemini-4-neon-medium is unavailable for your account", ClassifiedProviderError),
    ("invalid model name", ClassifiedProviderError),
    ("You have hit your weekly limit. Resets in 30 seconds.", OverloadedError),
    ("Quota resets in 30 seconds", OverloadedError),
])
def test_error_mapping(fake_agy, monkeypatch, error, expected):
    bin_path, _, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "error:" + error)
    with pytest.raises(expected) as exc:
        AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
    assert type(exc.value) is expected


def test_exit_before_reading_prompt_keeps_the_diagnostic(fake_agy, monkeypatch):
    bin_path, _, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "early")
    big = [MESSAGES[0], {"role": "user", "content": "字" * 400_000}]  # > pipe buffer
    with pytest.raises(RuntimeError, match="sign-in expired"):
        AgyProvider(bin_path=bin_path).chat_completion(big, model="m")


def test_provider_name_keeps_standard_prompt():
    # generate_system_prompt drops the JSON example for providers named Gemini.
    assert "Gemini" not in AgyProvider.provider_name.fget(None)


def test_factory_registers_agy(fake_agy):
    bin_path, _, _ = fake_agy
    assert isinstance(ProviderFactory().create_provider("ag", bin_path=bin_path), AgyProvider)


def test_timeout_is_reported_as_a_timeout(fake_agy, monkeypatch):
    """The timer only signals the CLI; the reader reports it. It used to close
    stdout under the reader, which surfaced as 'I/O operation on closed file'."""
    bin_path, log, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "slow")
    p = AgyProvider(bin_path=bin_path, timeout=1)
    s = p.chat_completion(MESSAGES, model="m", stream=True)
    next(s)
    with pytest.raises(RuntimeError, match="timed out after 1s"):
        list(s)
    assert not os.path.exists(_rec(log)["home"])


def test_refresh_is_not_saved_while_the_sign_in_is_locked(fake_agy, monkeypatch):
    """An interactive `agy` (the Docker wrapper) holds the lock; a call must
    neither block on it nor save over the sign-in."""
    import fcntl
    bin_path, _, real = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "refresh")
    lock = real / ".gemini" / "antigravity-cli" / ".t9-login.lock"
    with open(lock, "w") as held:
        # Shared: the call's own copy (shared) gets through, its save
        # (exclusive) does not.
        fcntl.flock(held, fcntl.LOCK_SH)
        t0 = time.time()
        AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
        assert time.time() - t0 < 10
    assert _token(real).read_text() == '{"t": 1}'


def test_root_publish_keeps_the_sign_in_owner(tmp_path, monkeypatch):
    """Run as root (docker compose exec t9 python …), a saved token and the
    lock file must belong to the sign-in's owner, who runs agy."""
    import providers.agy_provider as ap
    real = tmp_path / "home"
    tok = real / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    tok.parent.mkdir(parents=True)
    tok.write_text('{"t": 1}')
    root = tmp_path / "call"
    (root / "home" / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (root / "home" / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").write_text('{"t": 2}')
    chowned = []
    monkeypatch.setattr(ap.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ap.os, "fchown", lambda fd, uid, gid: chowned.append((uid, gid)))
    owner = (tok.stat().st_uid, tok.stat().st_gid)
    AgyProvider._publish_login(str(root), str(real), {ap._TOKEN_FILE: b'{"t": 1}', ap._SCOPE_FILE: None})
    assert tok.read_text() == '{"t": 2}'
    assert len(chowned) == 2 and set(chowned) == {owner}  # the lock and the token


def test_call_waits_out_a_sign_in_in_progress(fake_agy, monkeypatch):
    """A direct `agy` run holds the lock exclusively while it rewrites the
    token; a call must not copy a half-written one. It reports a retryable
    busy error instead, and never starts agy."""
    import fcntl
    bin_path, log, real = fake_agy
    lock = real / ".gemini" / "antigravity-cli" / ".t9-login.lock"
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        t0 = time.time()
        with pytest.raises(OverloadedError, match="sign-in is in use"):
            AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
        assert time.time() - t0 < 10
    assert not os.path.exists(log)


def test_symlinked_lock_is_never_followed(fake_agy, tmp_path, monkeypatch):
    """A lock path redirected elsewhere is refused (O_NOFOLLOW): the refresh is
    not saved and nothing is done to the link's target."""
    import providers.agy_provider as ap
    bin_path, _, real = fake_agy
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    lock = real / ".gemini" / "antigravity-cli" / ".t9-login.lock"
    lock.symlink_to(target)
    chowned = []
    monkeypatch.setattr(ap.os, "fchown", lambda fd, uid, gid: chowned.append(fd))
    monkeypatch.setenv("FAKE_AGY_MODE", "refresh")
    AgyProvider(bin_path=bin_path).chat_completion(MESSAGES, model="m")
    assert _token(real).read_text() == '{"t": 1}'
    assert target.read_text() == "keep" and not chowned


def test_root_never_chowns_an_existing_lock(tmp_path, monkeypatch):
    """Only a lock this call created is handed over; an existing inode could
    be a hard link to anything."""
    import providers.agy_provider as ap
    real = tmp_path / "home"
    tok = real / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    tok.parent.mkdir(parents=True)
    tok.write_text('{"t": 1}')
    (tok.parent / ".t9-login.lock").write_text("")
    root = tmp_path / "call"
    (root / "home" / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (root / "home" / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").write_text('{"t": 2}')
    chowned = []
    monkeypatch.setattr(ap.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ap.os, "fchown", lambda fd, uid, gid: chowned.append((uid, gid)))
    AgyProvider._publish_login(str(root), str(real), {ap._TOKEN_FILE: b'{"t": 1}', ap._SCOPE_FILE: None})
    assert tok.read_text() == '{"t": 2}'
    assert len(chowned) == 1  # the staged token only


def test_failed_chown_closes_the_lock(tmp_path, monkeypatch):
    import providers.agy_provider as ap
    real = tmp_path / "home"
    (real / ".gemini" / "antigravity-cli").mkdir(parents=True)
    closed, real_close = [], os.close
    monkeypatch.setattr(ap.os, "geteuid", lambda: 0)

    def boom(fd, uid, gid):
        raise PermissionError("no")
    monkeypatch.setattr(ap.os, "fchown", boom)
    monkeypatch.setattr(ap.os, "close", lambda fd: (closed.append(fd), real_close(fd)))
    with pytest.raises(PermissionError):
        ap._open_lock(str(real))
    assert len(closed) == 1


def test_timeout_while_sending_the_prompt_is_a_timeout(fake_agy, monkeypatch):
    bin_path, _, _ = fake_agy
    monkeypatch.setenv("FAKE_AGY_MODE", "noread")
    big = [MESSAGES[0], {"role": "user", "content": "字" * 400_000}]  # > pipe buffer
    with pytest.raises(ClassifiedProviderError, match="timed out after 1s"):
        AgyProvider(bin_path=bin_path, timeout=1).chat_completion(big, model="m")
