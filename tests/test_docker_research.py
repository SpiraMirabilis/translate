"""Validate the standard deployment's MCP service without a Docker daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_research_compose_reuses_server_on_private_loopback():
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is not installed")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["docker", "compose", "config", "--format", "json"],
                            cwd=root, capture_output=True, text=True, timeout=15)
    if result.returncode and "is not a docker command" in result.stderr:
        pytest.skip("Docker Compose is not installed")
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    research = services["t9-research"]
    assert not research.get("profiles")
    assert research["image"] == services["t9"]["image"]
    assert research["network_mode"] == "service:t9"
    assert not research.get("ports") and not research.get("networks")
    assert research["command"] == ["python", "-m", "mcp_server", "--read-only",
                                   "--transport", "streamable-http", "--host", "127.0.0.1", "--port", "8766"]
    assert research["environment"]["MCP_USAGE_LOG"] == "/data/logs/mcp_usage.log"
    assert research["read_only"] is True
    assert "no-new-privileges:true" in research["security_opt"]
    assert research["depends_on"]["t9"]["condition"] == "service_healthy"
    assert {volume["target"] for volume in research["volumes"]} == {"/data"}


@pytest.mark.skipif(not os.environ.get("T9_DOCKER_RUNTIME_IMAGE"),
                    reason="set T9_DOCKER_RUNTIME_IMAGE for the isolated container smoke test")
def test_readonly_container_writes_and_persists_real_tool_usage(tmp_path):
    """Run the actual MCP service twice, sharing only disposable state.

    No host ports, host credentials, or application database are used. Docker
    access is supplied by the caller; this test never changes daemon permissions.
    """
    image = os.environ["T9_DOCKER_RUNTIME_IMAGE"]
    data = tmp_path / "data"
    data.mkdir()

    def docker(*args, timeout=60):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    root = Path(__file__).resolve().parents[1]
    model = json.loads(subprocess.check_output(
        ["docker", "compose", "config", "--format", "json"], cwd=root, text=True))
    research = model["services"]["t9-research"]
    flags = ["--read-only", "--network", "none", "--tmpfs", "/tmp",
             "--security-opt", "no-new-privileges:true", "--mount", f"type=bind,src={data},dst=/data",
             "-e", f"MCP_USAGE_LOG={research['environment']['MCP_USAGE_LOG']}"]
    seed = """
import logging
from types import SimpleNamespace
from db import DatabaseManager
config = SimpleNamespace(db_backend='sqlite', script_dir='/data/', translation_model='test:model',
                         trad_to_simp=False, spaces_enabled=False, debug=False)
db = DatabaseManager(config, logging.getLogger('smoke'))
book = db.create_book('Docker Research Smoke')
db.add_to_queue(book, ['synthetic source'], title='Chapter 1', chapter_number=1)
db.add_entity('characters', '测试', 'Test Name', book_id=book, origin_chapter=1, gender='female')
"""
    docker("run", "--rm", *flags, image, "python", "-c", seed)
    probe = """
import json, time, urllib.request
url = 'http://127.0.0.1:8766'
for attempt in range(60):
    try:
        with urllib.request.urlopen(url + '/health', timeout=2) as response:
            health = json.load(response)
        assert health == {'ok': True, 'read_only': True, 'research_scope_version': 1}
        break
    except OSError:
        time.sleep(0.5)
else:
    raise RuntimeError('MCP service did not become healthy')
headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json',
           'MCP-Protocol-Version': '2025-03-26', 'X-T9-Caller': 'translation',
           'X-T9-Book': '1', 'X-T9-Chapter': '1'}
payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
    'name': 't9_search_entities', 'arguments': {'book_id': 1, 'pattern': '测试'}}}
request = urllib.request.Request(url + '/mcp', headers=headers, data=json.dumps(payload).encode())
with urllib.request.urlopen(request, timeout=15) as response:
    result = json.load(response)['result']
assert not result.get('isError'), result
text = ''.join(item.get('text', '') for item in result['content'])
assert 'Test Name' in text and 'female' in text and 'characters' in text, text
"""
    for expected_count in (1, 2):
        cid = docker("run", "-d", *flags, image, *research["command"])
        try:
            info = json.loads(docker("inspect", cid))[0]
            assert info["HostConfig"]["ReadonlyRootfs"]
            assert not info["HostConfig"].get("PortBindings")
            assert "no-new-privileges:true" in info["HostConfig"]["SecurityOpt"]
            docker("exec", cid, "python", "-c", probe)
            # An application-root write must still fail.
            docker("exec", cid, "python", "-c",
                   "from pathlib import Path\ntry:\n Path('/app/isolation-check').write_text('x')\n"
                   "except OSError as exc:\n assert exc.errno == 30, exc\n"
                   "else:\n raise AssertionError('container root is writable')")
        finally:
            docker("stop", "--time", "10", cid)
            docker("rm", cid)
        # Check on the host after removing the container, then repeat with a new
        # container to prove append behavior and persistence across recreation.
        log = data / "logs" / "mcp_usage.log"
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(rows) == expected_count
        assert all(row["ok"] and row["mode"] == "readonly"
                   and row["caller"] == "translation" and row["book"] == 1
                   and row["chapter"] == 1 and row["tool"] == "t9_search_entities" for row in rows)
