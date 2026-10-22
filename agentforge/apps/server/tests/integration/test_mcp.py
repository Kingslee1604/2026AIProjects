"""The MCP server at /mcp (R1-43) and its security suite (R1-44, baseline S9), on real Postgres
with the fake model, through the official SDK client over Streamable HTTP (in process):

- exactly three tools, in a fixed order, all read-only (snapshot: a new or changed tool fails);
- cited search and answers (with progress notifications), and runs;
- both a 2026-07-28 client and a handshake-era (legacy) client;
- API key, scope, Origin, and rate-limit enforcement; audit with the client's name;
- another organization's key sees none of this organization's knowledge or runs.
"""

import functools
import json
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Implementation
from sqlalchemy import text

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.db.engine import create_sync_engine
from agentforge.runs.service import NewItem, RunService

from .conftest import Database, UserFactory, login, outsider, settings_for
from .test_knowledge_flow import POLICY_MD, drain, new_kb, services_of, upload
from .test_runs import RecordingEngine

T = TypeVar("T")
CLIENT = Implementation(name="agentforge-tests", version="1.0")
EXPECTED_TOOLS = ["search_knowledge", "answer_with_citations", "get_run"]


def make_app(database: Database, tmp_path: Path, **settings: object) -> FastAPI:
    return create_app(
        settings_for(database, model_provider="fake", blob_root=tmp_path / "b", **settings)
    )


@pytest.fixture
def app(database: Database, tmp_path: Path) -> FastAPI:
    return make_app(database, tmp_path)


class Org:
    """A signed-in admin with a knowledge base holding the test policy."""

    def __init__(self, client: TestClient, csrf: str, filename: str = "policy.md") -> None:
        self.client, self.csrf = client, csrf
        self.kb = new_kb(client, csrf)
        upload(client, csrf, self.kb, filename, POLICY_MD)
        assert drain(client) == 1

    def key(self, *scopes: str) -> str:
        response = self.client.post(
            "/v1/api-keys",
            json={"name": "mcp test", "scopes": list(scopes)},
            headers={"X-CSRF-Token": self.csrf},
        )
        assert response.status_code == 201, response.text
        return str(response.json()["key"])

    def mcp(
        self,
        key: str,
        work: Callable[[Client], Awaitable[T]],
        *,
        mode: str = "2026-07-28",
        headers: dict[str, str] | None = None,
    ) -> T:
        """Connect an SDK client to /mcp in this app's event loop and run `work` with it."""

        async def run() -> T:
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=self.client.app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {key}", **(headers or {})},
            ) as http:
                transport = streamable_http_client("http://testserver/mcp", http_client=http)
                async with Client(transport, mode=mode, client_info=CLIENT, cache=None) as client:  # type: ignore[arg-type]
                    return await work(client)

        return self.client.portal.call(run)  # type: ignore[union-attr]

    def raw(self, key: str | None, **headers: str) -> int:
        """A plain POST to /mcp (no SDK), to see the gate's status code."""
        auth = {"Authorization": f"Bearer {key}"} if key else {}
        response = self.client.post(
            "/mcp",
            content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **auth,
                **headers,
            },
        )
        return response.status_code

    def passes(self, key: str | None, **headers: str) -> bool:
        """The gate let the request through (whatever the SDK then answers)."""
        return self.raw(key, **headers) not in {401, 403, 429, 503}


@pytest.fixture
def org(app: FastAPI, make_user: UserFactory) -> Iterator[Org]:
    with TestClient(app) as client:
        yield Org(client, login(client, make_user(Role.ADMIN)))


def structured(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return dict(result.structured_content)


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    return " ".join(getattr(c, "text", "") for c in result.content)


def owner_sql(database: Database, sql: str, **params: object) -> list[Any]:
    engine = create_sync_engine(database.url("agentforge_owner"))
    try:
        with engine.begin() as connection:
            result = connection.execute(text(sql), params)
            return list(result.all()) if result.returns_rows else []
    finally:
        engine.dispose()


def create_run(org: Org, title: str, *questions: str) -> UUID:
    """A run in the admin's organization (recorded, not executed: no worker in these tests)."""
    runs = RunService(services_of(org.client).sessions, RecordingEngine())
    session = org.client.get("/v1/auth/session").json()
    return org.client.portal.call(  # type: ignore[union-attr]
        functools.partial(
            runs.create,
            UUID(session["org_id"]),
            UUID(session["user_id"]),
            template_key="questionnaire",
            title=title,
            input={},
            items=[NewItem(question=q) for q in questions],
        )
    )


# --- Tools (R1-43) ------------------------------------------------------------------------------


def test_exactly_three_read_only_tools_in_a_fixed_order(org: Org) -> None:
    """Snapshot (R1-44): adding a tool, renaming one, or one that is not read-only fails here."""
    key = org.key("mcp:use")

    async def tools(client: Client) -> Any:
        return await client.list_tools()

    listed = org.mcp(key, tools)
    snapshot = [
        (
            t.name,
            t.annotations.read_only_hint if t.annotations else None,
            t.annotations.destructive_hint if t.annotations else None,
            sorted(t.input_schema.get("properties", {})),
        )
        for t in listed.tools
    ]
    assert snapshot == [
        ("search_knowledge", True, False, ["knowledge_base_ids", "limit", "query"]),
        ("answer_with_citations", True, False, ["knowledge_base_ids", "question"]),
        ("get_run", True, False, ["run_id"]),
    ]
    assert listed.ttl_ms == 300_000  # the tool list may be cached for five minutes
    assert [t.name for t in org.mcp(key, tools).tools] == EXPECTED_TOOLS  # same order again


def test_search_returns_passages_with_citations(org: Org) -> None:
    key = org.key("mcp:use")

    async def search(client: Client) -> CallToolResult:
        return await client.call_tool("search_knowledge", {"query": "access log retention"})

    body = structured(org.mcp(key, search))
    top = body["passages"][0]
    assert top["document"] == "policy.md"
    assert top["pages"] == "p. 1"
    assert "ninety days" in top["content"]
    assert top["flagged"] is False


def test_answers_cite_their_sources_and_report_progress(org: Org) -> None:
    key = org.key("mcp:use")
    updates: list[tuple[float, float | None, str | None]] = []

    async def ask(client: Client) -> CallToolResult:
        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            updates.append((progress, total, message))

        return await client.call_tool(
            "answer_with_citations",
            {"question": "How long are access logs retained?", "knowledge_base_ids": [org.kb]},
            progress_callback=on_progress,
        )

    body = structured(org.mcp(key, ask))
    assert body["status"] == "answered"
    assert "ninety days" in body["answer"]
    [sentence] = body["sentences"]
    assert sentence["status"] == "verified"
    assert sentence["citations"][0]["document"] == "policy.md"
    assert "ninety days" in sentence["citations"][0]["quote"]
    assert updates[0][2] == "Searching the documents"
    assert updates[-1] == (3, 3, "Done")


def test_get_run_reports_status_and_answers(org: Org) -> None:
    run_id = create_run(org, "MCP run", "Q1?", "Q2?")
    key = org.key("mcp:use")

    async def get(client: Client) -> tuple[CallToolResult, CallToolResult]:
        found = await client.call_tool("get_run", {"run_id": str(run_id)})
        missing = await client.call_tool("get_run", {"run_id": "not-a-uuid"})
        return found, missing

    found, missing = org.mcp(key, get)
    body = structured(found)
    assert (body["title"], body["questions"], body["answered"]) == ("MCP run", 2, 0)
    assert [i["question"] for i in body["items"]] == ["Q1?", "Q2?"]
    assert body["pending_review"] is None  # no review yet
    assert "Run not found" in error_text(missing)


def test_a_legacy_client_is_served_too(org: Org) -> None:
    """A handshake-era (2025-11-25) client: initialize, then tools, statelessly."""
    key = org.key("mcp:use")

    async def legacy(client: Client) -> tuple[list[str], CallToolResult]:
        listed = await client.list_tools()
        found = await client.call_tool("search_knowledge", {"query": "encryption keys"})
        return [t.name for t in listed.tools], found

    names, found = org.mcp(key, legacy, mode="legacy")
    assert names == EXPECTED_TOOLS
    assert "twelve months" in structured(found)["passages"][0]["content"]


def test_tool_calls_are_audited_with_the_client_and_key(org: Org, database: Database) -> None:
    key = org.key("mcp:use")

    async def search(client: Client) -> CallToolResult:
        return await client.call_tool("search_knowledge", {"query": "logs"})

    structured(org.mcp(key, search))
    [row] = owner_sql(
        database,
        "SELECT outcome, target, details FROM app.audit_log WHERE action = 'mcp.tool_call' "
        "AND details->>'api_key' = :k ORDER BY at DESC LIMIT 1",
        k=next(k["id"] for k in org.client.get("/v1/api-keys").json() if k["prefix"] in key),
    )
    assert (row.outcome, row.target) == ("success", "mcp:search_knowledge")
    assert row.details["client"] == "agentforge-tests/1.0"
    assert row.details["protocol"] == "2026-07-28"


# --- Security suite (R1-44) ---------------------------------------------------------------------


def test_keys_without_mcp_use_or_without_a_key_are_refused(org: Org) -> None:
    assert org.raw(None) == 401
    assert org.raw("af_nosuchkey1_" + "x" * 43) == 401
    assert org.raw(org.key("api:read", "api:write")) == 403  # no mcp:use scope
    revoked = org.key("mcp:use")
    key_id = next(k["id"] for k in org.client.get("/v1/api-keys").json() if k["prefix"] in revoked)
    org.client.delete(f"/v1/api-keys/{key_id}", headers={"X-CSRF-Token": org.csrf})
    assert org.raw(revoked) == 401
    # A browser session is not enough: /mcp takes API keys only.
    assert org.client.post("/mcp", json={}).status_code == 401


def test_a_foreign_origin_is_refused(org: Org) -> None:
    key = org.key("mcp:use")
    assert org.raw(key, Origin="https://evil.example") == 403
    assert org.raw(key, Origin="http://127.0.0.1:3000") == 403  # not even localhost by default
    assert org.passes(key)  # programs send no Origin


def test_an_allowed_origin_passes(
    database: Database, tmp_path: Path, make_user: UserFactory
) -> None:
    app = make_app(database, tmp_path, mcp_allowed_origins=["https://inspector.example"])
    with TestClient(app) as client:
        allowed = Org(client, login(client, make_user(Role.ADMIN)))
        key = allowed.key("mcp:use")
        assert allowed.passes(key, Origin="https://inspector.example")
        assert allowed.raw(key, Origin="https://evil.example") == 403


def test_requests_and_answers_are_rate_limited_per_key(
    database: Database, tmp_path: Path, make_user: UserFactory
) -> None:
    app = make_app(database, tmp_path, mcp_requests_per_minute=3, mcp_answers_per_minute=1)
    with TestClient(app) as client:
        limited = Org(client, login(client, make_user(Role.ADMIN)))
        key, other = limited.key("mcp:use"), limited.key("mcp:use")
        assert [limited.passes(key) for _ in range(3)] == [True, True, True]
        assert limited.raw(key) == 429
        assert limited.passes(other)  # limits are per key

        async def two_answers(client: Client) -> list[CallToolResult]:
            arguments = {"question": "How long are access logs retained?"}
            return [await client.call_tool("answer_with_citations", arguments) for _ in range(2)]

        first, second = limited.mcp(limited.key("mcp:use"), two_answers)
        assert not first.is_error
        assert "Too many answers" in error_text(second)


def test_another_organization_sees_none_of_this_one(
    org: Org, app: FastAPI, database: Database
) -> None:
    run_id = create_run(org, "Private run", "Q?")
    with outsider(app, database) as (client, csrf):
        # Their own knowledge base: the same policy text under another name.
        other = Org(client, csrf, filename="their-policy.md")
        key = other.key("mcp:use")

        async def probe(c: Client) -> list[CallToolResult]:
            return [
                await c.call_tool(
                    "search_knowledge", {"query": "logs", "knowledge_base_ids": [org.kb]}
                ),
                await c.call_tool("search_knowledge", {"query": "logs"}),
                await c.call_tool(
                    "answer_with_citations", {"question": "Logs?", "knowledge_base_ids": [org.kb]}
                ),
                await c.call_tool("get_run", {"run_id": str(run_id)}),
            ]

        theirs_by_id, own_search, theirs_answer, theirs_run = other.mcp(key, probe)
        key_id = next(k["id"] for k in client.get("/v1/api-keys").json() if k["prefix"] in key)
    assert "Knowledge base not found" in error_text(theirs_by_id)
    assert "Knowledge base not found" in error_text(theirs_answer)
    assert "Run not found" in error_text(theirs_run)
    # Their own search finds only their document, never this organization's policy.md.
    passages = structured(own_search)["passages"]
    assert passages
    assert {p["document"] for p in passages} == {"their-policy.md"}
    # All four calls were audited in the caller's organization, none in this one's name.
    audited = owner_sql(
        database,
        "SELECT org_id = (SELECT org_id FROM app.knowledge_bases WHERE id = :k) AS here "
        "FROM app.audit_log WHERE action = 'mcp.tool_call' AND details->>'api_key' = :key",
        k=org.kb,
        key=key_id,
    )
    assert [row.here for row in audited] == [False] * 4
