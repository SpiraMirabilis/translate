"""The engine closes a provider stream it abandons, before retrying or letting a
cancellation propagate. A CLI provider's stream owns a running process (codex,
claude, agy); left open, it keeps running beside its replacement."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from providers.base import StreamingResponse
from translation_engine import TranslationCancelled, TranslationEngine


class _Proc:
    """Stands in for the subprocess a CLI provider's generator owns."""
    def __init__(self):
        self.reaped = False


def _cli_stream(proc, n):
    """Shaped like ClaudeCodeProvider's: the generator reaps its process in a
    `finally`, which only runs when the stream is closed or exhausted."""
    try:
        for _ in range(n):
            yield SimpleNamespace(text="x" * 50, done=False)
    finally:
        proc.reaped = True


class _Provider:
    provider_name = "Fake CLI"
    supports_mcp_tools = False

    def __init__(self):
        self.procs = []

    def chat_completion(self, **kwargs):
        proc = _Proc()
        self.procs.append(proc)
        # The shared wrapper, exactly as claude_code_provider returns it.
        return StreamingResponse(_cli_stream(proc, 200))

    def get_streaming_content(self, chunk):
        return chunk.text

    def is_stream_complete(self, chunk):
        return chunk.done


def _engine(provider):
    cfg = MagicMock()
    cfg.translation_model = "fake:model"
    cfg.get_client.return_value = (provider, "model")
    cfg.get_max_chars.return_value = 100000
    cfg.repetition_guard = False
    db = MagicMock()
    db.get_book.return_value = {"id": 1, "source_language": "zh", "title": "T"}
    db.get_book_prompt_template.return_value = None
    db.get_entities_snapshot.return_value = {}
    db.get_book_categories.return_value = ["characters"]
    db.get_book_gendered_categories.return_value = ["characters"]
    db.get_token_ratio.return_value = 1.0
    eng = TranslationEngine(cfg, MagicMock(), db)
    eng.generate_system_prompt = lambda *a, **k: "SYSTEM"
    eng.apply_historic_notes = lambda *a, **k: False
    eng._inline_footnote_section = lambda *a, **k: ""
    eng._mcp_tools_kwargs = lambda *a, **k: {}
    return eng


def test_cancelled_stream_is_closed():
    provider = _Provider()
    eng = _engine(provider)
    calls = {"n": 0}

    def should_cancel():
        calls["n"] += 1
        return calls["n"] > 3  # passes the pre-chunk checks, trips mid-stream

    with pytest.raises(TranslationCancelled):
        eng.translate_chapter(["你好"] * 3, book_id=1, chapter_number=1, stream=True,
                              should_cancel=should_cancel)
    assert provider.procs, "the fake provider was never called"
    assert all(p.reaped for p in provider.procs), "abandoned stream left its process running"


class _RetryProvider(_Provider):
    """Records, at each new call, whether every earlier stream was reaped."""
    def __init__(self):
        super().__init__()
        self.prior_reaped_at_call = []

    def chat_completion(self, **kwargs):
        self.prior_reaped_at_call.append(all(p.reaped for p in self.procs))
        return super().chat_completion(**kwargs)


def test_retry_starts_only_after_the_abandoned_stream_is_closed():
    """The repetition guard breaks out of a stream mid-way and retries. The old
    stream must be closed before the retry's call, not whenever it is
    garbage-collected."""
    provider = _RetryProvider()
    eng = _engine(provider)
    eng.config.repetition_guard = True
    eng._detect_repetition = lambda text: True
    with pytest.raises(Exception):
        eng.translate_chapter(["你好"] * 3, book_id=1, chapter_number=1, stream=True)
    assert len(provider.prior_reaped_at_call) >= 2, "the guard never retried"
    assert all(provider.prior_reaped_at_call), "a retry started while the previous CLI was still running"


def test_classified_provider_failure_is_not_treated_as_a_limit():
    """A provider that already classified its failure (agy: "weekly limit …
    resets in 30 seconds" is a throttle it reported as such, or a plain
    failure) must not be re-sniffed into a queue-parking session limit."""
    from providers.base import ClassifiedProviderError

    class _Failing(_Provider):
        def chat_completion(self, **kwargs):
            raise ClassifiedProviderError("agy CLI failed: You've hit your weekly limit · resets 10:40pm (UTC)")

    eng = _engine(_Failing())

    def parked(*a, **k):
        raise AssertionError("parked the queue on a classified failure")
    eng._sleep_for_session_limit = parked
    eng._sleep_for_overload = parked
    import translation_engine as te
    orig_sleep = te.time.sleep
    te.time.sleep = lambda s: None
    try:
        with pytest.raises(Exception) as exc:
            eng.translate_chapter(["你好"] * 3, book_id=1, chapter_number=1, stream=True)
    finally:
        te.time.sleep = orig_sleep
    assert "parked the queue" not in str(exc.value)
