"""Tests for JobManager.on_progress pause/resume activity logging.

Both the Anthropic API and Claude Code providers raise OverloadedError into
the engine's shared retry loop, which emits phase="overloaded" progress
events; session limits emit phase="session_limit". Each pause must produce
exactly one activity-log line (and one on resume) so a 529 wait is visible
in the UI instead of looking like a hung job.
"""
from web.services.job_manager import JobManager


class FakeDB:
    def __init__(self):
        self.entries = []

    def add_activity_log(self, **kwargs):
        self.entries.append(kwargs)
        return {"id": len(self.entries), **kwargs}


def _jm():
    jm = JobManager()
    jm.db_manager = FakeDB()
    jm.status = "running"
    jm.book_id = 57
    jm.chapter_number = 64
    return jm


def test_overloaded_pause_logs_once_and_sets_waiting():
    jm = _jm()
    jm.on_progress({"phase": "overloaded", "wait_seconds": 300})
    assert jm.status == "waiting"
    entries = jm.db_manager.entries
    assert len(entries) == 1
    assert entries[0]["type"] == "warning"
    assert "overloaded (529)" in entries[0]["message"]
    assert "~5 min" in entries[0]["message"]
    assert entries[0]["book_id"] == 57 and entries[0]["chapter"] == 64

    # Engine re-emits the phase on each retry loop — no log spam
    jm.on_progress({"phase": "overloaded", "wait_seconds": 300})
    assert len(jm.db_manager.entries) == 1


def test_overload_resume_logs_recovery():
    jm = _jm()
    jm.on_progress({"phase": "overloaded", "wait_seconds": 300})
    jm.on_progress({"phase": "chunk", "chunk": 1})
    assert jm.status == "running"
    assert len(jm.db_manager.entries) == 2
    assert jm.db_manager.entries[1]["type"] == "info"
    assert "recovered from overload" in jm.db_manager.entries[1]["message"]


def test_session_limit_messages_unchanged():
    jm = _jm()
    jm.on_progress({"phase": "session_limit", "wait_seconds": 1800})
    assert "session limit" in jm.db_manager.entries[0]["message"]
    jm.on_progress({"phase": "chunk", "chunk": 1})
    assert "Session limit reset" in jm.db_manager.entries[1]["message"]


def test_second_overload_after_recovery_logs_again():
    jm = _jm()
    jm.on_progress({"phase": "overloaded", "wait_seconds": 300})
    jm.on_progress({"phase": "chunk", "chunk": 1})
    jm.on_progress({"phase": "overloaded", "wait_seconds": 300})
    warnings = [e for e in jm.db_manager.entries if e["type"] == "warning"]
    assert len(warnings) == 2
