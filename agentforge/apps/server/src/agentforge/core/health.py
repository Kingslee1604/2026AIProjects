"""Liveness probes used by container health checks (`agentforge healthcheck api|worker`)."""

import time
import urllib.error
import urllib.request

from agentforge.core.settings import Settings

# A worker is healthy while its heartbeat is fresh: at least 10 s, or 10 poll intervals.
MIN_HEARTBEAT_AGE_SECONDS = 10.0


def api_is_healthy(settings: Settings, timeout: float = 3.0) -> bool:
    url = f"http://127.0.0.1:{settings.api_port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def worker_is_healthy(settings: Settings, now: float | None = None) -> bool:
    max_age = max(MIN_HEARTBEAT_AGE_SECONDS, settings.worker_poll_seconds * 10)
    try:
        age = (now or time.time()) - settings.worker_heartbeat_path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age <= max_age
