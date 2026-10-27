"""The optional scraper profile in compose.yaml, validated without a Docker daemon."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def _services(*profiles):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is not installed")
    root = Path(__file__).resolve().parents[1]
    args = [a for p in profiles for a in ("--profile", p)]
    result = subprocess.run(["docker", "compose", *args, "config", "--format", "json"],
                            cwd=root, capture_output=True, text=True, timeout=15)
    if result.returncode and "is not a docker command" in result.stderr:
        pytest.skip("Docker Compose is not installed")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


def test_scraper_services_are_opt_in():
    services = _services()
    assert "scraper" not in services and "t9-scraper-worker" not in services


def test_scraper_profile_runs_scraper_and_worker():
    services = _services("scraper")
    worker, scraper = services["t9-scraper-worker"], services["scraper"]
    # The worker shares T9's namespace: admin API on loopback, scraper by name.
    assert worker["image"] == services["t9"]["image"]
    assert worker["network_mode"] == "service:t9"
    assert worker["command"] == ["python", "scrape_worker.py"]
    assert worker["depends_on"]["t9"]["condition"] == "service_healthy"
    assert {v["target"] for v in worker["volumes"]} == {"/data"}
    # Reachable as http://scraper:8787 on T9's network; host ports loopback only.
    assert list(scraper["networks"]) == ["t9"]
    assert all(p["host_ip"] == "127.0.0.1" for p in scraper["ports"])
    assert [v["target"] for v in scraper["volumes"]] == ["/data"]
    assert scraper["volumes"][0]["source"].endswith("/data/scraper")


def test_scrape_worker_profile_runs_worker_alone():
    services = _services("scrape-worker")
    assert "t9-scraper-worker" in services and "scraper" not in services


def test_default_scraper_url_follows_docker(monkeypatch):
    import importlib
    import settings_store
    try:
        monkeypatch.setenv("T9_DOCKER", "1")
        assert importlib.reload(settings_store).DEFAULT_SCRAPER_URL == "http://scraper:8787"
        monkeypatch.delenv("T9_DOCKER")
        assert importlib.reload(settings_store).DEFAULT_SCRAPER_URL == "http://127.0.0.1:8787"
    finally:
        importlib.reload(settings_store)
