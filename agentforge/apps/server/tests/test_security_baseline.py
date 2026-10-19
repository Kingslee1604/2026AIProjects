"""Security baseline items checked without a database: S1 (local only), S6 (egress), S8 (limits).

The rest of the baseline is evidenced by integration and browser tests; the list with links is
docs/security/r1-baseline.md."""

import ast
import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from agentforge.api.limits import RateLimits, SlidingWindow
from agentforge.core.settings import Settings, bind_refusal

PROJECT = Path(__file__).resolve().parents[3]
SOURCE = PROJECT / "apps" / "server" / "src" / "agentforge"


# --- S1: services listen on 127.0.0.1 unless deliberately deployed ---------------------------


def settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_is_always_allowed(host: str) -> None:
    assert bind_refusal(settings(api_host=host)) is None


def test_a_public_address_is_refused_outside_production_and_containers() -> None:
    refusal = bind_refusal(settings(api_host="0.0.0.0"))  # noqa: S104 - the case under test
    assert refusal is not None
    assert "127.0.0.1" in refusal
    assert bind_refusal(settings(api_host="192.168.1.20", env="test")) is not None


def test_containers_and_configured_production_may_listen_publicly() -> None:
    assert bind_refusal(settings(api_host="0.0.0.0", in_container=True)) is None  # noqa: S104
    production = {"env": "production", "cookie_secure": True, "api_host": "10.0.0.5"}
    assert bind_refusal(settings(**production)) is not None  # no database configured
    configured = settings(**production, database_url=SecretStr("postgresql://a:b@db/x"))
    assert bind_refusal(configured) is None


def test_production_without_tls_does_not_start() -> None:
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        settings(env="production", cookie_secure=False)


def test_compose_publishes_every_port_on_loopback_only() -> None:
    published: list[str] = []
    lines = (PROJECT / "compose.yaml").read_text("utf-8").splitlines()
    for number, line in enumerate(lines):
        if line.strip() != "ports:":
            continue
        indent = len(line) - len(line.lstrip())
        for entry in lines[number + 1 :]:
            if not entry.strip() or len(entry) - len(entry.lstrip()) <= indent:
                break
            published.append(entry.strip().removeprefix("-").strip().strip("\"'"))
    assert published, "compose.yaml publishes no ports?"
    assert all(p.startswith("127.0.0.1:") for p in published), published


# --- S6: the only outbound network is the model endpoint --------------------------------------

NETWORK_PACKAGES = {"httpx", "httpx2", "requests", "aiohttp", "socket"}
NETWORK_MODULES = {"urllib.request", "http.client"}
# Each talks only to configured endpoints: the model servers, and the local health check.
ALLOWED_NETWORK_USERS = {
    "models/providers.py": "Ollama / OpenAI-compatible base URL from settings",
    "evaluation/report.py": "Ollama /api/tags (model digests for the report)",
    "core/health.py": "the API's own /healthz on 127.0.0.1 (container health check)",
}


def _network_imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return {n for n in names if n in NETWORK_MODULES or n.split(".")[0] in NETWORK_PACKAGES}


def test_only_the_model_gateway_opens_network_connections() -> None:
    users = {
        str(p.relative_to(SOURCE)).replace("\\", "/")
        for p in SOURCE.rglob("*.py")
        if _network_imports(p)
    }
    assert users == set(ALLOWED_NETWORK_USERS)


def test_no_agent_has_tools_that_reach_the_network() -> None:
    # R1 agents get evidence passed in; none declares a tool (architecture §10, S6).
    definitions = (SOURCE / "agents" / "definitions").glob("*.toml")
    for definition in definitions:
        assert not re.search(r"^\s*tools\s*=", definition.read_text("utf-8"), re.M), definition


# --- S8: request rate limits ---------------------------------------------------------------


def test_a_sliding_window_refuses_past_its_limit_and_says_when_to_retry() -> None:
    window = SlidingWindow(2, window=60)
    assert [window.allow("k"), window.allow("k"), window.allow("k")] == [True, True, False]
    assert 1 <= window.retry_after("k") <= 61
    assert window.allow("other")  # per caller


def test_each_kind_of_request_has_its_own_limit() -> None:
    limits = RateLimits(
        settings(rate_uploads_per_minute=1, rate_answers_per_minute=2, rate_runs_per_minute=1)
    )
    assert limits.refused("uploads", "me") is None
    assert limits.refused("uploads", "me") is not None
    assert limits.refused("answers", "me") is None  # another bucket
    assert limits.refused("runs", "you") is None
    assert limits.refused("runs", "you") is not None
