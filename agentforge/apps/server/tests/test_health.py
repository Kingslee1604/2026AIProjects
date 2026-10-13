import os
import time
from pathlib import Path

import pytest

from agentforge.cli import main
from agentforge.core.health import api_is_healthy, worker_is_healthy
from agentforge.core.settings import Settings, get_settings


def settings_with_heartbeat(path: Path) -> Settings:
    return Settings(worker_heartbeat_path=path, worker_poll_seconds=1.0)


def test_worker_is_healthy_with_fresh_heartbeat(tmp_path: Path) -> None:
    heartbeat = tmp_path / "hb"
    heartbeat.write_text("x")
    assert worker_is_healthy(settings_with_heartbeat(heartbeat))


def test_worker_is_unhealthy_with_stale_heartbeat(tmp_path: Path) -> None:
    heartbeat = tmp_path / "hb"
    heartbeat.write_text("x")
    old = time.time() - 60
    os.utime(heartbeat, (old, old))
    assert not worker_is_healthy(settings_with_heartbeat(heartbeat))


def test_worker_is_unhealthy_without_heartbeat(tmp_path: Path) -> None:
    assert not worker_is_healthy(settings_with_heartbeat(tmp_path / "missing"))


def test_api_is_unhealthy_when_nothing_listens() -> None:
    assert not api_is_healthy(Settings(api_port=1), timeout=0.5)


def test_healthcheck_cli_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeat = tmp_path / "hb"
    monkeypatch.setenv("AGENTFORGE_WORKER_HEARTBEAT_PATH", str(heartbeat))
    get_settings.cache_clear()
    try:
        assert main(["healthcheck", "worker"]) == 1
        heartbeat.write_text("x")
        assert main(["healthcheck", "worker"]) == 0
    finally:
        get_settings.cache_clear()
