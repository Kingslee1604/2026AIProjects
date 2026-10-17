import io
import json
import re

import pytest
from fastapi.testclient import TestClient

from agentforge.api.app import create_app
from agentforge.core.logging import configure_logging
from agentforge.core.settings import Settings

TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(env="test")))


def test_healthz_returns_ok_with_trace_header(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert TRACE_ID.match(response.headers["X-Trace-Id"])


def test_meta_reports_version_and_env(client: TestClient) -> None:
    body = client.get("/v1/meta").json()
    assert body["name"] == "agentforge"
    assert body["env"] == "test"


def test_unknown_route_returns_problem_details_with_trace_id(client: TestClient) -> None:
    response = client.get("/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 404
    assert body["trace_id"] == response.headers["X-Trace-Id"]


def test_incoming_w3c_traceparent_is_continued(client: TestClient) -> None:
    incoming = "4bf92f3577b34da6a3ce929d0e0e4736"
    response = client.get("/healthz", headers={"traceparent": f"00-{incoming}-00f067aa0ba902b7-01"})
    assert response.headers["X-Trace-Id"] == incoming


def test_request_log_line_is_json_and_carries_the_trace_id(client: TestClient) -> None:
    stream = io.StringIO()
    configure_logging("INFO", "json", stream=stream)
    response = client.get("/healthz")
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    request_logs = [r for r in records if r.get("event") == "http_request"]
    assert len(request_logs) == 1
    entry = request_logs[0]
    assert entry["trace_id"] == response.headers["X-Trace-Id"]
    assert entry["status"] == 200
    assert entry["path"] == "/healthz"
    assert "timestamp" in entry
