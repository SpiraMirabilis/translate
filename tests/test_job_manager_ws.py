"""Tests for JobManager's multi-socket WebSocket registry + replay buffer."""
import asyncio

from web.services.job_manager import JobManager


class FakeWS:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_json(self, message):
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(message)


def test_add_and_remove_websocket():
    jm = JobManager()
    ws1, ws2 = FakeWS(), FakeWS()
    loop = asyncio.new_event_loop()
    try:
        jm.add_websocket(ws1, loop)
        jm.add_websocket(ws2, loop)
        assert jm.websockets == {ws1, ws2}
        jm.remove_websocket(ws1)
        assert jm.websockets == {ws2}
        jm.remove_websocket(ws1)  # idempotent — no-op
        assert jm.websockets == {ws2}
    finally:
        loop.close()


def test_broadcast_reaches_all_sockets():
    jm = JobManager()
    ws1, ws2 = FakeWS(), FakeWS()
    loop = asyncio.new_event_loop()
    try:
        jm.add_websocket(ws1, loop)
        jm.add_websocket(ws2, loop)
        loop.run_until_complete(jm.send_message_async({"type": "translation_complete"}))
        assert [m["type"] for m in ws1.sent] == ["translation_complete"]
        assert [m["type"] for m in ws2.sent] == ["translation_complete"]
    finally:
        loop.close()


def test_dead_socket_dropped_without_silencing_live_one():
    jm = JobManager()
    dead, live = FakeWS(fail=True), FakeWS()
    loop = asyncio.new_event_loop()
    try:
        jm.add_websocket(dead, loop)
        jm.add_websocket(live, loop)
        loop.run_until_complete(jm.send_message_async({"type": "error", "message": "x"}))
        assert dead not in jm.websockets
        assert live in jm.websockets
        assert len(live.sent) == 1
    finally:
        loop.close()


def test_messages_buffered_with_no_socket_and_replayed():
    jm = JobManager()
    # No socket attached — must not raise, must buffer
    jm.send_message_sync({"type": "translation_complete", "result": {"ok": 1}})
    loop = asyncio.new_event_loop()
    try:
        ws = FakeWS()
        backlog = jm.add_websocket(ws, loop)
        assert [m["type"] for m in backlog] == ["translation_complete"]
        assert all("seq" in m for m in backlog)
    finally:
        loop.close()


def test_activity_log_and_progress_not_replayed():
    jm = JobManager()
    jm.send_message_sync({"type": "activity_log", "entry": {}})
    jm.send_message_sync({"type": "progress", "phase": "chunk"})
    jm.send_message_sync({"type": "json_fix_needed", "payload": {}})
    loop = asyncio.new_event_loop()
    try:
        backlog = jm.add_websocket(FakeWS(), loop)
        assert [m["type"] for m in backlog] == ["json_fix_needed"]
    finally:
        loop.close()


def test_replay_buffer_bounded():
    jm = JobManager()
    for i in range(150):
        jm.send_message_sync({"type": "error", "i": i})
    loop = asyncio.new_event_loop()
    try:
        backlog = jm.add_websocket(FakeWS(), loop)
        assert len(backlog) == 100
        assert backlog[-1]["i"] == 149  # newest kept
        # seq strictly increasing
        seqs = [m["seq"] for m in backlog]
        assert seqs == sorted(seqs)
    finally:
        loop.close()


def test_resolved_prompts_pruned_from_replay():
    jm = JobManager()
    jm.send_message_sync({"type": "entity_review_needed", "entities": {}})
    jm.send_message_sync({"type": "json_fix_needed", "payload": {}})
    jm.submit_review({"entities": {}})
    jm.submit_json_fix({"action": "retry"})
    loop = asyncio.new_event_loop()
    try:
        backlog = jm.add_websocket(FakeWS(), loop)
        assert backlog == []
    finally:
        loop.close()


def test_reset_does_not_orphan_sockets():
    jm = JobManager()
    ws = FakeWS()
    loop = asyncio.new_event_loop()
    try:
        jm.add_websocket(ws, loop)
        jm.reset()
        assert ws in jm.websockets
        assert jm.loop is loop
    finally:
        loop.close()


def test_send_message_sync_from_thread_broadcasts():
    """send_message_sync bridges a worker thread into the event loop."""
    import threading

    jm = JobManager()
    ws = FakeWS()

    async def scenario():
        jm.add_websocket(ws, asyncio.get_running_loop())
        t = threading.Thread(
            target=jm.send_message_sync, args=({"type": "translation_complete"},))
        t.start()
        # Yield the loop so run_coroutine_threadsafe's task can run
        for _ in range(50):
            await asyncio.sleep(0.01)
            if ws.sent:
                break
        t.join(timeout=5)

    asyncio.run(scenario())
    assert [m["type"] for m in ws.sent] == ["translation_complete"]
