"""jev_client: the TypeSafe System One HTTP client. Never hits the live API."""
import pytest

import jev_client


class FakeResp:
    def __init__(self, status, data=None, headers=None, text=""):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self.text = text or str(data)

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


ANSWER = {"answers": {"q": {"type": "choice", "choice": "story", "confidence": 0.97,
                            "probabilities": {"story": 0.98, "filler": 0.02}}},
          "usage": {"input_tokens": 10, "output_tokens": 1}}
CRITERIA = {"story": "narrative", "filler": "author's note"}


@pytest.fixture
def posts(monkeypatch):
    """Script ``requests.post`` responses; records each call's kwargs."""
    calls, queue = [], []

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return queue.pop(0)

    monkeypatch.setenv("TYPESAFE_KEY", "sk-test")
    monkeypatch.setattr(jev_client.requests, "post", fake_post)
    monkeypatch.setattr(jev_client.time, "sleep", lambda s: calls.append({"slept": s}))
    return calls, queue


def test_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_KEY", raising=False)
    assert jev_client.is_configured() is False
    with pytest.raises(jev_client.JevError):
        jev_client.system_one("x", {})


def test_choice_parses_answer_and_sends_bearer(posts):
    calls, queue = posts
    queue.append(FakeResp(200, ANSWER))
    ans = jev_client.choice("some text", "Story or note?", CRITERIA)
    assert ans == {"choice": "story", "confidence": 0.97,
                   "probabilities": {"story": 0.98, "filler": 0.02}}
    call = calls[0]
    assert call["url"] == jev_client.API_URL
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["json"]["model"] == "jev-latest"
    assert call["json"]["questions"]["q"]["type"] == "choice"


def test_model_follows_setting(posts, monkeypatch):
    calls, queue = posts
    monkeypatch.setenv("JEV_MODEL", "jev-1.13.0")
    queue.append(FakeResp(200, ANSWER))
    jev_client.choice("x", "?", CRITERIA)
    assert calls[0]["json"]["model"] == "jev-1.13.0"


def test_retries_overloaded_then_succeeds(posts):
    calls, queue = posts
    queue += [FakeResp(529, text="overloaded"), FakeResp(200, ANSWER)]
    assert jev_client.choice("x", "?", CRITERIA)["choice"] == "story"
    assert {"slept": 1.0} in calls


def test_honours_retry_after_capped(posts):
    calls, queue = posts
    queue += [FakeResp(429, headers={"retry-after": "3"}, text="slow down"),
              FakeResp(429, headers={"retry-after": "600"}, text="slow down"),
              FakeResp(200, ANSWER)]
    jev_client.choice("x", "?", CRITERIA)
    slept = [c["slept"] for c in calls if "slept" in c]
    assert slept == [3.0, jev_client.MAX_RETRY_WAIT_SECONDS]


def test_gives_up_after_max_retries(posts):
    calls, queue = posts
    queue += [FakeResp(529, text="overloaded")] * (jev_client.MAX_RETRIES + 1)
    with pytest.raises(jev_client.JevError, match="529"):
        jev_client.choice("x", "?", CRITERIA)


@pytest.mark.parametrize("status", [401, 422, 500])
def test_non_retryable_errors_raise(posts, status):
    calls, queue = posts
    queue.append(FakeResp(status, text="nope"))
    with pytest.raises(jev_client.JevError, match=str(status)):
        jev_client.choice("x", "?", CRITERIA)
    assert not any("slept" in c for c in calls)


def test_unknown_choice_is_rejected(posts):
    calls, queue = posts
    bad = {"answers": {"q": {"type": "choice", "choice": "other", "confidence": 1.0}}}
    queue.append(FakeResp(200, bad))
    with pytest.raises(jev_client.JevError):
        jev_client.choice("x", "?", CRITERIA)
