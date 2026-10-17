"""Validate the standard deployment's MCP service without a Docker daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def test_mcp_startup_resolves_lifespan_settings():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-W", "error:Field 'lifespan'", "-c",
         "from mcp_server.server import build_server; "
         "[build_server(read_only=ro, stateless_http=http) "
         "for ro in (False, True) for http in (False, True)]"],
        cwd=root, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_mcp_stdio_initialization_and_readonly_inventory():
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-W", "error:Field 'lifespan'", "-m", "mcp_server", "--read-only"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env={**os.environ, "MCP_USAGE_LOG": "off"},
        )
        with anyio.fail_after(15):
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "t9"
                    tools = (await session.list_tools()).tools
                    assert tools and all(tool.annotations.readOnlyHint for tool in tools)
                    assert "t9_list_entities" in {tool.name for tool in tools}

    anyio.run(run)


@pytest.mark.parametrize("mounted_logs", [False, True])
def test_debug_prompts_use_writable_logs(tmp_path, mounted_logs):
    from db.core import DatabaseCore
    from translation_engine import TranslationEngine

    app = tmp_path / "app"
    app.mkdir()
    if mounted_logs:
        data_logs = tmp_path / "data" / "logs"
        data_logs.mkdir(parents=True)
        (app / "logs").symlink_to(data_logs, target_is_directory=True)
    config = SimpleNamespace(script_dir=str(app) + "/")
    manager = DatabaseCore()
    manager.config, manager.logger = config, Mock()
    engine = TranslationEngine(config, manager.logger, manager)
    if mounted_logs:
        app.chmod(0o555)
    try:
        for book_id, name in [(1, "prompt-book1.tmp"), (2, "prompt-book2.tmp"),
                              (None, "prompt.tmp")]:
            path = Path(engine._debug_prompt_path(book_id))
            assert path == app / "logs" / name
            manager.save_json_file(str(path), f"Prompt for {book_id}")
            assert json.loads(path.read_text()) == f"Prompt for {book_id}"
        manager.logger.error.assert_not_called()
    finally:
        app.chmod(0o755)


def test_debug_prompt_directory_errors_are_logged(tmp_path):
    from db.core import DatabaseCore

    (tmp_path / "logs").write_text("not a directory")
    manager = DatabaseCore()
    manager.config = SimpleNamespace(script_dir=str(tmp_path))
    manager.logger = Mock()
    manager.save_json_file("logs/prompt-book1.tmp", "prompt")
    manager.logger.error.assert_called_once()


@pytest.mark.parametrize("absolute", [False, True])
def test_entity_json_exports_preserve_existing_behavior(db, tmp_path, absolute):
    payload = {"characters": {"张三": {"translation": "Zhang San"}}}
    db.entities = payload
    path = tmp_path / "entities.json"
    path.write_text("old export")
    assert db.export_to_json(str(path) if absolute else path.name)
    assert json.loads(path.read_text()) == payload


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
