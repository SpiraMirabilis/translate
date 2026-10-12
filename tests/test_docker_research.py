"""Validate the standard deployment's MCP service without a Docker daemon."""
import json
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
    assert research["read_only"] is True
    assert "no-new-privileges:true" in research["security_opt"]
    assert research["depends_on"]["t9"]["condition"] == "service_healthy"
    assert {volume["target"] for volume in research["volumes"]} == {"/data"}
