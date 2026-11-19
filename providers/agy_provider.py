"""
Antigravity CLI provider (`agy`).

Shells out to the local `agy` binary so translation can ride on the user's
Antigravity sign-in (Gemini 4 Neon / Hydrogen / Argon, Gemini 3.8 Flash, …)
without a Google API key — the Antigravity counterpart of CodexProvider and
ClaudeCodeProvider.

`agy` is an agent harness: by default every call carries ~9.6k tokens of its
own agent instructions and a large tool list (browser, shell, file edits, web
search…). That would make it an unfair and unsafe translator, so each call
runs a throwaway *custom agent* instead:

- The call's workspace holds `.agents/agents/t9-translate.md` with
  `excludeDefaultComponents: true` and `tools: []`, and the translation system
  prompt as the agent's body. Measured: a trivial call drops from ~9.6k to
  ~400 input tokens, i.e. only our prompt reaches the model.
- `--sandbox` and `--disable-slash-commands` on top.
- The user prompt goes in on stdin as one `stream-json` message
  (`{"event":"user","message":{"content":…}}`) — prompts routinely exceed the
  128 KB single-argument limit, so it can't go on the command line.

**Each call also gets its own throwaway agy home.** agy has no "don't save"
option: it stores every call as a conversation (~1 MB, the whole chapter),
indexes it for search, keeps previews of the first message in
`jetbox_summaries_proto.pb`, maps each workspace in
`cache/last_conversations.json` and writes a ~20 KB log — and it restores a
deleted summary row on a later run. So nothing is deleted piecemeal: the call
runs with HOME pointing at a fresh directory holding a *copy* of the sign-in
(`_LOGIN_FILES`, `_LOGIN_DIRS`), and the whole directory is removed afterwards.

Copies, never symlinks: agy refreshes its OAuth token with an in-place write
(Go's os.WriteFile), which through a link would truncate the real token and
let a concurrent call read it half-written. A sign-in file the call changed
is published back (`_publish_login`) only if it is non-empty (and still JSON
when it was JSON), under an exclusive lock shared by every T9 process, and
only if the real file is still what this call started from — a newer login
saved meanwhile by another call wins.

The real home is `AGY_HOME` (the Docker image sets /data/agy) or, outside the
image, `$HOME`; the sign-in scope file follows `XDG_CONFIG_HOME` there, as agy
itself does. A login kept in the system keyring (the desktop install) is
unaffected by the HOME switch.

Reasoning effort is part of the model id (`gemini-4-neon-medium`,
`…-high`, …), so there is no separate effort knob. `stream-json` output carries
real `text_delta`s, so streaming is genuine.
"""
import json
import logging
import os
try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
from collections import deque
from typing import Any, Dict, List, Optional, Union

from .base import (
    ClassifiedProviderError,
    ModelProvider,
    StreamingResponse,
    OverloadedError,
    looks_overloaded,
    SessionLimitError,
    looks_session_limited,
)
from .claude_code_provider import _Chunk, _drain_stderr

logger = logging.getLogger(__name__)

AGENT_NAME = "t9-translate"

_AGENT_HEADER = (
    "---\n"
    f"name: {AGENT_NAME}\n"
    "description: Plain model call for t9 translation. No tools.\n"
    "mainAgent: true\n"
    "excludeDefaultComponents: true\n"
    "tools: []\n"
    "---\n"
)

# What the sign-in consists of, relative to agy's home. All of it is copied
# into the call home; everything else agy writes (the conversation, indexes,
# caches, logs) stays there and is deleted with it.
_SCOPE_FILE = ".config/antigravity/credential-user-scope"
_TOKEN_FILE = ".gemini/antigravity-cli/antigravity-oauth-token"
_LOGIN_FILES = (
    _SCOPE_FILE,
    _TOKEN_FILE,
    ".gemini/antigravity-cli/settings.json",
    ".gemini/antigravity-cli/installation_id",
)
_LOGIN_DIRS = (".gemini/config",)
# Changes a call may publish back to the real home. Everything else a call
# changes (settings, project config) is discarded with the call home.
_PUBLISHED = (_TOKEN_FILE, _SCOPE_FILE)
_LOCK_FILE = ".gemini/antigravity-cli/.t9-login.lock"

# Account limits: the turn stops and waiting is the only fix — the
# subscription's daily/weekly quota, or a pay-as-you-go spend cap / prepaid
# balance. These park the queue (SessionLimitError), like Codex's usage limit.
# It takes BOTH an account-level noun and explicit exhaustion wording ("you
# have reached your weekly quota"), or one of the unambiguous pay-as-you-go
# phrases. "429 … per-minute request quota", "quota resets in 30 seconds" are
# short-term throttling (transient, below); "disk quota exceeded" and
# "billing is disabled for this project" are not limits at all and must fail
# with their message instead of parking the queue forever. Matched only
# against agy's error text, never model output.
_ACCOUNT_LIMIT_RE = re.compile(
    r"\b(?:daily|weekly|monthly|individual|account|plan|subscription|usage)\s+(?:quota|limit)",
    re.IGNORECASE,
)
_EXHAUSTED_RE = re.compile(
    r"\b(?:reached|exceeded|exhausted|hit|used up|run out|ran out|no (?:more|remaining))\b",
    re.IGNORECASE,
)
_PAYG_LIMIT_RE = re.compile(
    r"spend(?:ing)?[ -]cap (?:reached|exceeded|hit)|prepaid balance|out of credits|"
    r"insufficient (?:credits|balance)|credits? (?:balance )?(?:is )?(?:too low|exhausted|depleted)",
    re.IGNORECASE,
)
_SHORT_TERM_RE = re.compile(
    r"per[- ](?:minute|second|hour)|\brpm\b|\btpm\b|"
    r"\bin\s+\d+(?:\.\d+)?\s*(?:s|secs?|seconds?|ms|minutes?|mins?)\b",
    re.IGNORECASE,
)
_LIMIT_WORD_RE = re.compile(r"\b(?:quota|limit|resets?)\b", re.IGNORECASE)
_NOT_A_LIMIT_RE = re.compile(
    r"disk quota|file ?system quota|no space left|billing (?:is |has been )?(?:disabled|not enabled)|"
    r"enable billing|permission[_ ]denied",
    re.IGNORECASE,
)
# Transient saturation: agy already waits out short rate limits itself (up to
# ~4 minutes), so one reaching us is long or repeated; retry after the
# engine's overload interval. Deliberately no bare "unavailable": "model
# unavailable for your account" is permanent and must fail, not loop.
_TRANSIENT_RE = re.compile(
    r"overloaded|at capacity|high demand|rate[ -]limit|too many requests|\b429\b|"
    r"resource[_ ]exhausted|quota exceeded|service unavailable|temporarily unavailable|try again later|"
    r"deadline exceeded|\b50[0234]\b|internal (?:server )?error|connection (?:reset|refused)",
    re.IGNORECASE,
)


def _real_home() -> str:
    return os.environ.get("AGY_HOME") or os.path.expanduser("~")


def _login_source(real_home: str, rel: str) -> str:
    """Where a sign-in file lives in the real home. Outside the image agy
    resolves ~/.config through XDG_CONFIG_HOME, so the scope file does too;
    in the image (AGY_HOME set) the wrapper clears XDG, so it is HOME/.config."""
    if rel.startswith(".config/") and not os.environ.get("AGY_HOME"):
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            return os.path.join(xdg, rel[len(".config/"):])
    return os.path.join(real_home, rel)


def _own_like(fd: int, ref_path: str) -> None:
    """When running as root, give `fd` the owner of `ref_path` (the file it
    replaces, or its directory). Unprivileged processes create files as
    themselves already, which is the sign-in's owner."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    st = os.stat(ref_path)
    os.fchown(fd, st.st_uid, st.st_gid)


def _try_lock(fd: int, seconds: float = 2.0, shared: bool = False) -> bool:
    """flock (exclusive, or shared), waiting at most `seconds`: an interactive
    sign-in can hold the lock for minutes and a translation must never block
    on it."""
    import time
    op = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    deadline = time.monotonic() + seconds
    while True:
        try:
            fcntl.flock(fd, op | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


def _open_lock(real_home: str) -> int:
    """Open the sign-in lock, creating it if needed, without following a
    link. Run as root, only a lock this call just created is handed to the
    sign-in owner: chowning an existing inode could reach any file linked
    there."""
    path = os.path.join(real_home, _LOCK_FILE)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        fd = os.open(path, flags)
        created = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        if created:
            _own_like(fd, os.path.dirname(path))
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _valid_login_update(new: bytes, old: Optional[bytes]) -> bool:
    """A refreshed sign-in file must be non-empty, and stay JSON if it was."""
    if not new or not new.strip():
        return False
    if old is not None:
        try:
            json.loads(old)
        except ValueError:
            return True
        try:
            json.loads(new)
        except ValueError:
            return False
    return True


class _AgyStream(StreamingResponse):
    def __init__(self, iterator, cleanup):
        super().__init__(iterator)
        self._cleanup = cleanup
        self.closed = False

    def __next__(self):
        if self.closed:
            raise StopIteration
        return super().__next__()

    def close(self):
        self.closed = True
        self._cleanup(graceful=False)
        try:
            self.response_iterator.close()
        except ValueError:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class AgyProvider(ModelProvider):
    """Provider that invokes the local `agy` CLI in headless print mode."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        super().__init__(api_key or "", base_url, **kwargs)
        self.bin_path = kwargs.get("bin_path") or os.environ.get("AGY_BIN") or shutil.which("agy")
        if not self.bin_path:
            raise ClassifiedProviderError("agy CLI not found. Install Antigravity CLI or set AGY_BIN.")
        self.timeout = kwargs.get("timeout", 1800)

    @staticmethod
    def _split_messages(messages: List[Dict[str, Any]]) -> tuple:
        sys_parts, user_parts = [], []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(i.get("text", "") for i in content if i.get("type") == "text")
            (sys_parts if msg.get("role") == "system" else user_parts).append(content)
        return "\n\n".join(sys_parts), "\n\n".join(user_parts)

    # ------------------------------------------------------------ call dirs

    @staticmethod
    def _make_call_dir(system_prompt: str, real_home: str) -> tuple:
        """<root>/ws holds the no-tools agent; <root>/home is agy's HOME for
        this call, holding a private copy of the sign-in. Returns (root,
        snapshot): snapshot maps each published sign-in file to the bytes the
        call started from (None when absent). On failure <root> is removed
        here; once this returns, the caller owns it."""
        root = tempfile.mkdtemp(prefix="t9-agy-")
        snapshot: Dict[str, Optional[bytes]] = {}
        try:
            agents = os.path.join(root, "ws", ".agents", "agents")
            os.makedirs(agents)
            body = system_prompt if system_prompt.lstrip().startswith("#") else "# Instructions\n" + system_prompt
            with open(os.path.join(agents, f"{AGENT_NAME}.md"), "w", encoding="utf-8") as f:
                f.write(_AGENT_HEADER + body + "\n")
            home = os.path.join(root, "home")
            os.makedirs(os.path.join(home, ".gemini", "antigravity-cli"))
            lock_fd = None
            if fcntl is not None and os.path.isdir(os.path.dirname(os.path.join(real_home, _LOCK_FILE))):
                try:
                    lock_fd = _open_lock(real_home)
                except OSError as e:
                    logger.debug("agy: copying the sign-in without the lock: %s", e)
            if lock_fd is not None and not _try_lock(lock_fd, shared=True):
                os.close(lock_fd)
                # A direct `agy` run (the sign-in, `agy models`) is writing the
                # login; a copy taken now could be half a token.
                raise OverloadedError("agy sign-in is in use by another agy run; retrying later")
            try:
                AgyProvider._copy_login(real_home, home, snapshot)
            finally:
                if lock_fd is not None:
                    os.close(lock_fd)
            return root, snapshot
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise

    @staticmethod
    def _copy_login(real_home: str, home: str, snapshot: Dict[str, Optional[bytes]]) -> None:
        """Copy the sign-in into the call home, recording the published files'
        starting bytes in `snapshot`."""
        for rel in _LOGIN_FILES:
            data = _read(_login_source(real_home, rel))
            if rel in _PUBLISHED:
                snapshot[rel] = data
            if data is None:
                continue
            dst = os.path.join(home, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        for rel in _LOGIN_DIRS:
            src = os.path.join(real_home, rel)
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.copytree(src, os.path.join(home, rel), symlinks=True,
                                ignore=lambda d, names: [n for n in names
                                                         if os.path.islink(os.path.join(d, n))])

    @staticmethod
    def _publish_login(root: str, real_home: str, snapshot: Dict[str, Optional[bytes]]) -> None:
        """Publish sign-in files this call changed (a token refresh) back to
        the real home: validated, under an exclusive lock shared by every T9
        process, and only when the real file still holds what the call
        started from — if another call already saved a newer login, it wins."""
        changed = {}
        for rel in _PUBLISHED:
            new = _read(os.path.join(root, "home", rel))
            old = snapshot.get(rel)
            if new is None or new == old:
                continue
            if not _valid_login_update(new, old):
                logger.warning("agy: ignoring an invalid refreshed sign-in file %s", rel)
                continue
            changed[rel] = new
        if not changed:
            return
        try:
            lock_fd = _open_lock(real_home)
        except OSError as e:
            logger.warning("agy: could not lock the sign-in to save a refresh: %s", e)
            return
        try:
            if fcntl is not None and not _try_lock(lock_fd):
                # Someone holds the login — another T9 call publishing, or an
                # interactive `agy` (sign-in) through the Docker wrapper. Their
                # write wins; the refresh this call made is simply not kept.
                logger.info("agy: sign-in is busy; not saving this call's token refresh")
                return
            for rel, new in changed.items():
                dest = _login_source(real_home, rel)
                if _read(dest) != snapshot.get(rel):
                    logger.info("agy: %s changed since this call started; keeping the newer one", rel)
                    continue
                tmp = None
                try:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".t9-sync-")
                    try:
                        os.fchmod(fd, 0o600)
                        # Run as root (docker compose exec t9 python …), the file
                        # must still belong to the sign-in's owner, who runs agy.
                        _own_like(fd, dest if os.path.exists(dest) else os.path.dirname(dest))
                        out = os.fdopen(fd, "wb")
                    except BaseException:
                        os.close(fd)
                        raise
                    with out:
                        out.write(new)
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(tmp, dest)
                    tmp = None
                    logger.info("agy: saved refreshed sign-in file %s", rel)
                except OSError as e:
                    logger.warning("agy: could not save refreshed sign-in file %s: %s", rel, e)
                finally:
                    if tmp is not None:
                        try:
                            os.unlink(tmp)
                        except OSError:
                            pass
        finally:
            os.close(lock_fd)  # releases the flock

    # ------------------------------------------------------------ the call

    def _build_cmd(self, model: str) -> List[str]:
        return [
            self.bin_path,
            "--agent", AGENT_NAME,
            "--model", model,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--disable-slash-commands",
            "--sandbox",
            "-p=",  # prompt comes from stdin (stream-json)
        ]

    @staticmethod
    def _child_env(home: str) -> Dict[str, str]:
        env = dict(os.environ)
        env["HOME"] = home
        env["AGY_CALL_HOME"] = home  # honoured by the Docker wrapper
        env["AGY_CALL_TMP"] = tempfile.gettempdir()  # where mkdtemp put it
        for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            env.pop(var, None)  # agy must resolve ~/.config inside the call home
        return env

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 8192,
        response_format: Optional[Dict[str, str]] = None,
        stream: bool = False,
        thinking_effort: Optional[str] = None,  # absorbed; effort is in the model id
        **kwargs,
    ) -> Union[Dict[str, Any], StreamingResponse]:
        kwargs.pop("mcp_tools", None)  # research tools not supported here
        json_mode = bool(response_format and response_format.get("type") == "json_object")
        system_prompt, user_prompt = self._split_messages(messages)
        if json_mode:
            user_prompt += (
                "\n\nIMPORTANT: You must respond with valid JSON only. "
                "Do not include any text before or after the JSON object. "
                "Do not wrap the JSON in markdown code fences."
            )
        real_home = _real_home()
        root, snapshot = self._make_call_dir(system_prompt, real_home)
        logger.info("agy CLI call: model=%s stream=%s json_mode=%s sys_chars=%d user_chars=%d",
                    model, stream, json_mode, len(system_prompt), len(user_prompt))

        handed_off = False
        proc = None
        err_thread = None
        lock = threading.Lock()
        done = {"cleaned": False}
        timer = None
        timed_out = threading.Event()

        def on_timeout():
            # Only signal: the reader sees EOF, notices the flag and cleans up
            # on its own thread. Closing pipes here would pull stdout out from
            # under a blocked read.
            timed_out.set()
            if proc is not None:
                self._signal_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))

        def cleanup(graceful: bool = False):
            with lock:
                if done["cleaned"]:
                    return
                done["cleaned"] = True
            if timer is not None:
                timer.cancel()
            try:
                if proc is not None:
                    self._stop_process(proc, graceful)
                    if err_thread is not None:
                        err_thread.join(timeout=2)  # stderr fully drained
                    for pipe in (proc.stdin, proc.stdout, proc.stderr):
                        try:
                            if pipe:
                                pipe.close()
                        except (OSError, ValueError):
                            pass
            finally:
                try:
                    self._publish_login(root, real_home, snapshot)
                finally:
                    shutil.rmtree(root, ignore_errors=True)

        try:
            proc = subprocess.Popen(
                self._build_cmd(model), cwd=os.path.join(root, "ws"),
                env=self._child_env(os.path.join(root, "home")),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, start_new_session=os.name == "posix",
            )
            stderr_buf: deque = deque(maxlen=200)
            err_thread = _drain_stderr(proc, stderr_buf)
            timer = threading.Timer(self.timeout, on_timeout)
            timer.daemon = True
            timer.start()
            try:
                proc.stdin.write(json.dumps({"event": "user", "message": {"content": user_prompt}},
                                            ensure_ascii=False) + "\n")
                proc.stdin.close()
            except BrokenPipeError:
                # agy exited before reading the prompt; its own diagnostic is
                # the useful part, not the pipe error. cleanup() reaps the
                # child and drains stderr before the buffer is read.
                cleanup()
                if timed_out.is_set():
                    raise ClassifiedProviderError(
                        f"agy CLI timed out after {self.timeout}s without reading the prompt")
                self._raise_failure("".join(stderr_buf).strip()[-2000:]
                                    or f"agy CLI exited {proc.returncode} before reading the prompt")

            events = self._events(proc, stderr_buf, cleanup, timed_out, self.timeout)
            handed_off = True
            if stream:
                return _AgyStream(self._stream_iter(events), cleanup)
            text, usage = "", {}
            for kind, payload in events:
                if kind == "final":
                    text, usage = payload
            if json_mode:
                text = self._strip_markdown_fences(text)
            return self._wrap_response(text, model, usage)
        finally:
            if not handed_off:
                cleanup()

    def _events(self, proc, stderr_buf, cleanup, timed_out=None, timeout=None):
        """Yield ("delta", text) while the answer streams, then
        ("final", (full_text, usage)). Raises on a failed turn. Every exit
        path runs `cleanup` (graceful only after a successful result)."""
        final = None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = ev.get("event")
                if kind == "step_update":
                    step = ev.get("step_update") or {}
                    stype = step.get("step_type")
                    if stype == "agent_response":
                        if step.get("text_delta"):
                            yield ("delta", step["text_delta"])
                    elif stype not in (None, "user_input"):
                        # No tools are declared; anything else means the agent
                        # definition didn't take and we'd be running an agent.
                        raise ClassifiedProviderError(f"agy CLI ran an unexpected step: {stype}")
                elif kind == "result":
                    res = ev.get("result") or {}
                    if res.get("status") != "SUCCESS" or res.get("finished_reason"):
                        self._raise_failure(res.get("error") or res.get("finished_reason") or "agy turn failed")
                    final = (res.get("response") or "", res.get("usage") or {})
                    break
        finally:
            cleanup(graceful=final is not None)
        if final is None and timed_out is not None and timed_out.is_set():
            raise ClassifiedProviderError(f"agy CLI timed out after {timeout}s without a response")
        if final is None:
            self._raise_failure("".join(stderr_buf).strip()[-2000:]
                                or f"agy CLI exited {proc.returncode} without a response")
        yield ("final", final)

    @staticmethod
    def _signal_group(proc, sig):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, sig)
            elif proc.poll() is None:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass

    @classmethod
    def _stop_process(cls, proc, graceful: bool):
        """Graceful: after a successful result agy may still be finishing
        (title generation, saving a refreshed sign-in), so give it a few
        seconds. Otherwise (abort, timeout, error) signal at once. Either way
        the whole process group is killed and the child is always reaped."""
        def signal_group(sig):
            cls._signal_group(proc, sig)

        if proc.poll() is None and graceful:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if proc.poll() is None:
            signal_group(signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        # Children may outlive the leader; the group is ours alone.
        signal_group(getattr(signal, "SIGKILL", signal.SIGTERM))
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("agy CLI pid %s did not exit after SIGKILL", proc.pid)

    @staticmethod
    def _raise_failure(message: str):
        message = message or ""
        if _NOT_A_LIMIT_RE.search(message):
            raise ClassifiedProviderError(f"agy CLI failed: {message.strip()[-2000:]}")
        account = looks_session_limited(message) or _PAYG_LIMIT_RE.search(message) or (
            _ACCOUNT_LIMIT_RE.search(message) and _EXHAUSTED_RE.search(message))
        short = _SHORT_TERM_RE.search(message)
        if account and not short:
            raise SessionLimitError(message.strip()[:300] or "agy account limit reached",
                                    reset_text=message)
        # A limit that lifts within seconds or minutes ("weekly limit … resets
        # in 30 seconds", "quota resets in 30 seconds") is a throttle: wait it
        # out instead of spending the chunk's retries.
        throttled = account or (short and _LIMIT_WORD_RE.search(message))
        if throttled or looks_overloaded(message, strict=False) or _TRANSIENT_RE.search(message):
            raise OverloadedError(message.strip()[:200] or "Antigravity service unavailable")
        raise ClassifiedProviderError(f"agy CLI failed: {message.strip()[-2000:]}")

    @staticmethod
    def _stream_iter(events):
        """Stream the deltas, then reconcile them with the final response:
        a missing tail is emitted; text that disagrees fails the call rather
        than passing off a partial answer as complete."""
        streamed = ""
        for kind, payload in events:
            if kind == "delta":
                streamed += payload
                yield _Chunk(text=payload)
                continue
            final = payload[0] or ""
            if final.startswith(streamed):
                if final[len(streamed):]:
                    yield _Chunk(text=final[len(streamed):])
            elif final.rstrip() != streamed.rstrip():
                raise ClassifiedProviderError("agy CLI: streamed text does not match its final response")
        yield _Chunk(done=True)

    def _wrap_response(self, content, model, usage):
        prompt = int(usage.get("input_tokens") or 0)
        completion = int(usage.get("output_tokens") or 0)
        return {
            "choices": [{"message": {"content": content, "role": "assistant"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                      "total_tokens": prompt + completion},
            "model": model,
        }

    def get_response_content(self, response):
        return response["choices"][0]["message"]["content"]

    def get_streaming_content(self, chunk):
        return chunk.text if isinstance(chunk, _Chunk) else None

    def is_stream_complete(self, chunk):
        return isinstance(chunk, _Chunk) and chunk.done

    @property
    def provider_name(self) -> str:
        # Deliberately not "Gemini …": generate_system_prompt treats a provider
        # named Gemini as having native structured output and drops the JSON
        # example. agy has no response schema, so it needs the standard prompt.
        return "Antigravity CLI"

    @property
    def supported_features(self) -> List[str]:
        return ["streaming", "system_messages", "json_mode_via_prompt"]
