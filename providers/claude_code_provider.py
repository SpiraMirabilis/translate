"""
Claude Code CLI provider.

Shells out to the local `claude` binary in `-p` (print) mode so that
translation jobs can ride on the user's Claude Code session/auth without
needing a separate API key.

The translation system prompt is delivered via `--system-prompt-file`, which
both passes it to the model as a true system message AND suppresses Claude
Code's default system prompt (CLAUDE.md auto-discovery, working-directory
info, env info, tool definitions, etc.). Tools are disabled with `--tools ""`
since translation never calls them. Sessions are not persisted
(`--no-session-persistence`) so they don't show up in `claude --resume`.

Streaming uses `--output-format stream-json --verbose
--include-partial-messages` so the translation engine's progress bar receives
real incremental chunks.
"""
import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque
from urllib.parse import urlparse
from typing import Dict, List, Optional, Any, Union

from .base import (
    ModelProvider,
    StreamingResponse,
    OverloadedError,
    looks_overloaded,
    SessionLimitError,
    looks_session_limited,
)

logger = logging.getLogger(__name__)


_DEBUG_LOG_PATH = "/tmp/t9-claude-code-debug.log"


def _drain_stderr(proc: subprocess.Popen, sink: deque) -> threading.Thread:
    """Spawn a daemon thread that keeps the subprocess's stderr pipe drained,
    preventing deadlocks when the CLI emits more than ~64KB to stderr while
    we're blocked reading stdout. The last few KB are kept in `sink` so we
    can include them in error messages.

    When CLAUDE_CODE_DEBUG is set, every line is also appended to
    /tmp/t9-claude-code-debug.log with a timestamp + pid prefix so multiple
    concurrent calls don't interleave incomprehensibly.
    """
    debug = os.environ.get("CLAUDE_CODE_DEBUG")
    debug_fh = None
    if debug:
        try:
            debug_fh = open(_DEBUG_LOG_PATH, "a", encoding="utf-8")
            debug_fh.write(f"\n=== claude pid={proc.pid} starting ===\n")
            debug_fh.flush()
        except Exception:
            debug_fh = None

    def pump():
        import time as _time
        try:
            for line in proc.stderr:
                sink.append(line)
                if debug_fh is not None:
                    try:
                        debug_fh.write(f"[{_time.strftime('%H:%M:%S')} pid={proc.pid}] {line}")
                        debug_fh.flush()
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            if debug_fh is not None:
                try:
                    debug_fh.close()
                except Exception:
                    pass
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    return t


class _Chunk:
    """Internal stream chunk recognized by get_streaming_content / is_stream_complete."""
    __slots__ = ("text", "done")

    def __init__(self, text: Optional[str] = None, done: bool = False):
        self.text = text
        self.done = done


MCP_SERVER_NAME = "t9"
_REACHABLE_TTL = 30.0
_reachable_cache: Dict[str, tuple] = {}


def _mcp_reachable(url: str) -> bool:
    """Is the read-only MCP server listening? Cached briefly per URL.

    A dead server must not cost a translation: the CLI would log a failed MCP
    connection and the model would be told about tools it cannot call, so an
    unreachable server means the call goes out without tools.
    """
    now = time.monotonic()
    hit = _reachable_cache.get(url)
    if hit and now - hit[1] < _REACHABLE_TTL:
        return hit[0]
    ok = False
    try:
        u = urlparse(url)
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 80), timeout=0.5):
            ok = True
    except OSError:
        ok = False
    _reachable_cache[url] = (ok, now)
    return ok


def mcp_tools_section(tools: Dict[str, Any]) -> str:
    """System-prompt addendum telling the model which book it is on and that it
    is welcome to look things up. The per-book prompt carries only the chapter
    number, so without this the model could not pass book_id."""
    book_id = tools.get("book_id")
    title = tools.get("book_title") or ""
    ch = tools.get("chapter_number")
    where = f"chapter {ch}" if ch else "the current chapter"
    lookups = max(1, int(tools.get("max_turns") or 8) - 1)
    notes_hint = (f"pass chapter={ch} so they read as of this chapter" if ch
                  else "pass the current chapter so they read as of it")
    try:
        n = int(ch)
    except (TypeError, ValueError):
        n = 0
    summaries_hint = (f' (chapters="<{n}" for everything so far, or a range such as '
                      f'"{max(1, n - 20)}-{n - 1}")' if n > 1 else "")
    return (
        "\n\nRESEARCH TOOLS (read-only):\n"
        f'You are translating book_id={book_id} ("{title}"), {where}. You have tools '
        "that look up this book's own records, and you are encouraged to use them "
        "whenever they would make the translation more accurate or more consistent "
        "with earlier chapters. Lookups are quick and cheap; a wrong or inconsistent "
        "rendering is not.\n"
        "- t9_search_entities / t9_list_entities: the full glossary. The PRE-TRANSLATED "
        "ENTITIES above list only terms matched in this text, so search here for a "
        "name, title, place, technique, rank or item that is not listed there.\n"
        "- t9_grep_book: how an earlier chapter rendered a recurring phrase, form of "
        "address, rank or title (field=\"both\" to see source and translation).\n"
        "- t9_get_chapter_summaries: short plot summaries of earlier chapters"
        f"{summaries_hint} — the fast way to find who a returning character is, "
        "where an earlier event happened, or which chapter to open with "
        "t9_get_chapter.\n"
        "- t9_get_chapter / t9_entity_context: what happened in an event the text "
        "refers back to, or how a term was used before.\n"
        f"- t9_notes_as_of: character and entity notes; {notes_hint}.\n"
        "Good reasons to look something up: a proper noun or term missing from the "
        "glossary above, a callback to an earlier scene, a recurring expression whose "
        "established wording you are unsure of, or ambiguity about who a pronoun or "
        "title refers to.\n"
        f"- Always pass book_id={book_id}. Do not read chapters after {where}.\n"
        f"- At most {lookups} lookups per response.\n"
        "- Make any lookups BEFORE writing your answer, and write nothing else: your "
        "final message must be the response in the required format and nothing more."
    )


class ClaudeCodeProvider(ModelProvider):
    """Provider that invokes the local `claude` CLI in print mode."""

    # The engine passes `mcp_tools` only to providers that say they take it.
    supports_mcp_tools = True

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        super().__init__(api_key or "", base_url, **kwargs)

        self.bin_path = (
            kwargs.get("bin_path")
            or os.environ.get("CLAUDE_CODE_BIN")
            or shutil.which("claude")
        )
        if not self.bin_path:
            raise RuntimeError(
                "claude CLI not found. Install Claude Code or set CLAUDE_CODE_BIN."
            )

        self.timeout = kwargs.get("timeout", 1800)

    def _split_messages(self, messages: List[Dict[str, Any]]) -> tuple:
        """Split messages into (system_prompt, user_prompt) strings."""
        sys_parts, user_parts = [], []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    item.get("text", "") for item in content
                    if item.get("type") == "text"
                )
            if msg.get("role") == "system":
                sys_parts.append(content)
            else:
                user_parts.append(content)
        return "\n\n".join(sys_parts), "\n\n".join(user_parts)

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 8192,
        response_format: Optional[Dict[str, str]] = None,
        stream: bool = False,
        thinking_effort: Optional[str] = None,  # absorbed; see base (no-op here)
        **kwargs,
    ) -> Union[Dict[str, Any], StreamingResponse]:
        json_mode = bool(response_format and response_format.get("type") == "json_object")
        mcp_tools = kwargs.pop("mcp_tools", None)
        if mcp_tools and not _mcp_reachable(mcp_tools.get("url", "")):
            logger.warning("claude CLI: MCP server %s unreachable — translating without tools",
                           mcp_tools.get("url"))
            mcp_tools = None

        system_prompt, user_prompt = self._split_messages(messages)
        if mcp_tools:
            system_prompt += mcp_tools_section(mcp_tools)
        if json_mode:
            user_prompt += (
                "\n\nIMPORTANT: You must respond with valid JSON only. "
                "Do not include any text before or after the JSON object. "
                "Do not wrap the JSON in markdown code fences."
            )

        # Translation is a straightforward task — extended thinking would
        # burn minutes of latency for no quality gain, and our streaming
        # only forwards text_delta events (not thinking_delta), so a
        # high-effort run looks like an indefinite hang to the user.
        # Honors CLAUDE_CODE_EFFORT to override (low|medium|high|xhigh|max).
        effort = os.environ.get("CLAUDE_CODE_EFFORT", "medium")

        cmd = [
            self.bin_path, "-p",
            "--model", model,
            "--no-session-persistence",
            "--tools", "",
            "--effort", effort,
            # Disable MCP entirely. `--tools ""` blocks built-in tools but
            # not MCP servers — without this the CLI loads every MCP server
            # registered in the user's claude.ai account (Gmail, Calendar,
            # Drive, etc.) on every call, slowing startup and exposing the
            # translation to unrelated tools in the session init payload.
            "--strict-mcp-config",
            "--mcp-config", self._mcp_config(mcp_tools),
        ]
        if mcp_tools:
            # --tools "" keeps the built-ins off; the t9 server's tools are
            # allowed wholesale (it is read-only), and the turn cap bounds how
            # many lookups one chunk can make.
            cmd += ["--allowedTools", f"mcp__{MCP_SERVER_NAME}",
                    "--max-turns", str(int(mcp_tools.get("max_turns") or 8))]
        if os.environ.get("CLAUDE_CODE_DEBUG"):
            # --debug writes to its own log destination, not stderr; use
            # --debug-file so we actually capture the output.
            cmd += ["--debug-file", "/tmp/t9-claude-code-debug.log"]

        sys_path = None
        if system_prompt:
            fd, sys_path = tempfile.mkstemp(suffix=".txt", prefix="t9-cc-sys-", text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(system_prompt)
            except Exception:
                os.unlink(sys_path)
                raise
            cmd += ["--system-prompt-file", sys_path]

        logger.info(
            "claude CLI call: model=%s stream=%s json_mode=%s sys_chars=%d user_chars=%d timeout=%ss mcp=%s",
            model, stream, json_mode, len(system_prompt), len(user_prompt), self.timeout,
            "on" if mcp_tools else "off",
        )

        self._sweep_orphan_session_files()

        handed_off = False  # sys_path ownership passed to the stream iterator
        try:
            if stream:
                cmd += [
                    "--output-format", "stream-json",
                    "--verbose",
                    "--include-partial-messages",
                ]
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                # Drain stderr in a thread to prevent pipe-buffer deadlock.
                # `--verbose` (required by stream-json) can emit substantial
                # logs; if we never read them, claude blocks on the stderr
                # write while we're blocked reading stdout.
                stderr_buf: deque = deque(maxlen=200)
                _drain_stderr(proc, stderr_buf)
                try:
                    proc.stdin.write(user_prompt)
                    proc.stdin.close()
                except BrokenPipeError as e:
                    proc.kill()
                    self._unlink(sys_path)
                    raise RuntimeError(f"claude CLI stdin closed unexpectedly: {e}")
                # Ownership of sys_path passes to the iterator's finally block.
                handed_off = True
                return StreamingResponse(self._stream_iter(proc, sys_path, stderr_buf,
                                                           tools=bool(mcp_tools)))

            try:
                try:
                    result = subprocess.run(
                        cmd,
                        input=user_prompt,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                    )
                except subprocess.TimeoutExpired as e:
                    captured = (e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
                    raise RuntimeError(
                        f"claude CLI timed out after {self.timeout}s. "
                        f"Last stderr: {captured.strip()[-2000:] or '(empty)'}"
                    )
            finally:
                self._unlink(sys_path)

            # A usage-limit notice (session or weekly) can arrive on stdout
            # (typically exit 0) or stderr; surface it as a SessionLimitError
            # so the engine pauses until the reset time rather than failing
            # the chunk.
            if looks_session_limited(result.stdout) or looks_session_limited(result.stderr):
                notice = result.stdout.strip() or result.stderr.strip()
                raise SessionLimitError(notice[:300], reset_text=notice)

            if result.returncode != 0:
                stderr_tail = result.stderr.strip()[-2000:]
                # A 529 may exit non-zero with the notice on stderr (or stdout).
                if looks_overloaded(result.stderr, strict=False) or looks_overloaded(result.stdout, strict=False):
                    raise OverloadedError(stderr_tail or result.stdout.strip()[:200] or "529 Overloaded")
                raise RuntimeError(
                    f"claude CLI failed (exit {result.returncode}): {stderr_tail}"
                )
            content = result.stdout
            # When the API is saturated the CLI prints "API Error: 529
            # Overloaded" to stdout and exits 0; surface it as a retryable
            # overload rather than letting it fail downstream JSON parsing.
            if looks_overloaded(content):
                raise OverloadedError(content.strip()[:200])
            if json_mode:
                content = self._strip_markdown_fences(content)
            self._schedule_orphan_cleanup()
            return self._wrap_response(content, model)
        except Exception:
            # If anything blew up before we handed sys_path to the iterator,
            # clean up — including a Popen that failed to start in stream mode
            # (which previously leaked the system-prompt tmpfile).
            if not handed_off:
                self._unlink(sys_path)
            raise

    @staticmethod
    def _mcp_config(mcp_tools: Optional[Dict[str, Any]]) -> str:
        """--mcp-config JSON: no servers at all, or just the read-only t9 one.

        The X-T9-* headers land in the server's usage log, so each lookup
        records the book and chapter it was made for.
        """
        if not mcp_tools:
            return '{"mcpServers":{}}'
        headers = {"X-T9-Caller": "translation"}
        if mcp_tools.get("book_id") is not None:
            headers["X-T9-Book"] = str(mcp_tools["book_id"])
        if mcp_tools.get("chapter_number") is not None:
            headers["X-T9-Chapter"] = str(mcp_tools["chapter_number"])
        return json.dumps({"mcpServers": {MCP_SERVER_NAME: {
            "type": "http", "url": mcp_tools["url"], "headers": headers}}})

    def _stream_iter(self, proc: subprocess.Popen, sys_path: Optional[str],
                     stderr_buf: Optional[deque] = None, tools: bool = False):
        got_partial = False
        # With tools a call spans several assistant messages, and one that ends
        # in a tool call may open with prose ("Let me check the glossary") that
        # must not reach the JSON. So in tools mode a message's text is held
        # until it is known to be the answer: it opens with "{" or a fence, or
        # the message ends without calling a tool. A message that turns out to
        # be a tool call has its held text dropped.
        held: List[str] = []
        live = not tools
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")

                if etype == "stream_event":
                    inner = event.get("event", {})
                    itype = inner.get("type")
                    if tools and itype == "message_start":
                        held, live = [], False
                    elif tools and itype == "content_block_start" and \
                            (inner.get("content_block") or {}).get("type") == "tool_use":
                        held, live = [], False
                    elif tools and itype == "message_delta":
                        stop = (inner.get("delta") or {}).get("stop_reason")
                        if stop == "tool_use":
                            held = []
                        elif held:
                            got_partial = True
                            yield _Chunk(text="".join(held))
                            held, live = [], True
                    elif itype == "content_block_delta":
                        delta = inner.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text")
                            if text and live:
                                got_partial = True
                                yield _Chunk(text=text)
                            elif text:
                                held.append(text)
                                head = "".join(held).lstrip()
                                if head.startswith(("{", "`")) or len(head) > 4000:
                                    got_partial = True
                                    yield _Chunk(text="".join(held))
                                    held, live = [], True
                elif etype == "assistant" and not got_partial:
                    msg = event.get("message", {})
                    blocks = msg.get("content", [])
                    if tools and isinstance(blocks, list) and any(
                            b.get("type") == "tool_use" for b in blocks):
                        continue  # a lookup turn, not the answer
                    if isinstance(blocks, list):
                        text = "".join(
                            b.get("text", "") for b in blocks
                            if b.get("type") == "text"
                        )
                        if text:
                            # A session-limit notice also arrives as a single
                            # non-streamed assistant message; pause on it rather
                            # than emitting it as translated text.
                            if looks_session_limited(text):
                                self._unlink(sys_path)
                                sys_path = None
                                raise SessionLimitError(text.strip()[:300], reset_text=text)
                            # A 529 arrives as a single non-streamed assistant
                            # message ("API Error: 529 Overloaded"); surface it
                            # as a retryable overload, not as response text.
                            if looks_overloaded(text):
                                self._unlink(sys_path)
                                sys_path = None
                                raise OverloadedError(text.strip()[:200])
                            yield _Chunk(text=text)
                elif etype == "result":
                    if held:  # tools mode: the answer's tail was still held
                        got_partial = True
                        yield _Chunk(text="".join(held))
                        held = []
                    # The CLI is done with the system-prompt file by now;
                    # unlink eagerly because the consumer typically breaks out
                    # of the loop on `done=True`, which would leave this
                    # generator suspended and its `finally` unrun until GC.
                    self._unlink(sys_path)
                    sys_path = None
                    result_text = event.get("result", "")
                    if event.get("is_error") and looks_session_limited(result_text):
                        raise SessionLimitError(str(result_text).strip()[:300], reset_text=str(result_text))
                    if event.get("is_error") and looks_overloaded(result_text, strict=False):
                        raise OverloadedError(str(result_text).strip()[:200])
                    yield _Chunk(done=True)
                    break
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            self._unlink(sys_path)
            self._schedule_orphan_cleanup()

        if proc.returncode not in (0, None):
            stderr = "".join(stderr_buf) if stderr_buf else ""
            if looks_session_limited(stderr):
                raise SessionLimitError(stderr.strip()[-300:], reset_text=stderr)
            if looks_overloaded(stderr, strict=False):
                raise OverloadedError(stderr.strip()[-200:] or "529 Overloaded")
            raise RuntimeError(
                f"claude CLI failed (exit {proc.returncode}): {stderr.strip()[-2000:]}"
            )

    @staticmethod
    def _unlink(path: Optional[str]) -> None:
        if not path:
            return
        try:
            os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _sweep_orphan_session_files() -> None:
        """Delete ai-title-only session files Claude Code writes as a
        background side effect of each --print call.

        Even with --no-session-persistence, the CLI fires off a background
        title-generation prompt whose result is written to
        ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl as a single ai-title
        line. They have no resume value but accumulate by the thousand and
        push real conversations out of `claude --resume`.

        Safety: only deletes files whose every line is an ai-title event,
        are <1KB, and haven't been modified in the last 30s (so an
        interactive session that just happens to have written its first
        line isn't caught).
        """
        if os.environ.get("CLAUDE_CODE_KEEP_SESSIONS"):
            return
        cwd = os.getcwd()
        encoded = "-" + cwd.lstrip("/").replace("/", "-")
        proj_dir = os.path.expanduser(f"~/.claude/projects/{encoded}")
        if not os.path.isdir(proj_dir):
            return
        cutoff = __import__("time").time() - 30
        try:
            entries = list(os.scandir(proj_dir))
        except OSError:
            return
        for entry in entries:
            if not entry.name.endswith(".jsonl"):
                continue
            try:
                st = entry.stat()
                if st.st_size > 1024 or st.st_mtime > cutoff:
                    continue
                with open(entry.path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            saw_line = False
            only_titles = True
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                saw_line = True
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    only_titles = False
                    break
                if obj.get("type") != "ai-title":
                    only_titles = False
                    break
            if saw_line and only_titles:
                try:
                    os.unlink(entry.path)
                except OSError:
                    pass

    def _schedule_orphan_cleanup(self) -> None:
        """Sweep ~35s after a call completes (gives Claude Code's background
        title-generation enough time to write its file plus the 30s mtime
        cutoff used by the sweep itself)."""
        if os.environ.get("CLAUDE_CODE_KEEP_SESSIONS"):
            return
        t = threading.Timer(35.0, self._sweep_orphan_session_files)
        t.daemon = True
        t.start()

    def _wrap_response(self, content: str, model: str) -> Dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {"content": content, "role": "assistant"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "model": model,
        }

    def get_response_content(self, response: Dict[str, Any]) -> str:
        return response["choices"][0]["message"]["content"]

    def get_streaming_content(self, chunk: Any) -> Optional[str]:
        if isinstance(chunk, _Chunk):
            return chunk.text
        return None

    def is_stream_complete(self, chunk: Any) -> bool:
        return isinstance(chunk, _Chunk) and chunk.done

    @property
    def provider_name(self) -> str:
        return "Claude Code CLI"

    @property
    def supported_features(self) -> List[str]:
        return ["streaming", "system_messages", "json_mode_via_prompt"]
