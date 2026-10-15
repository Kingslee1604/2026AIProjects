"""Runs, run events, and the live stream (R1-30) on real Postgres, with a recording engine
(the DBOS workflows arrive in R1-33; DBOS itself is covered by the engine conformance suite)."""

import asyncio
import dataclasses
import functools
import json
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.auth.users import create_user
from agentforge.db.engine import create_app_engine, create_sessionmaker, create_sync_engine
from agentforge.runs.service import NewItem, RunService

from .conftest import PASSWORD, Database, UserFactory, login, settings_for
from .test_knowledge_flow import services_of


class RecordingEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID]] = []
        self.fail_start = False

    async def start(self, org_id: UUID, run_id: UUID, template_key: str) -> None:
        if self.fail_start:
            raise ConnectionError("engine unreachable")
        self.calls.append(("start", run_id))

    async def cancel(self, org_id: UUID, run_id: UUID) -> None:
        self.calls.append(("cancel", run_id))

    async def signal_review(self, org_id: UUID, run_id: UUID) -> None:
        self.calls.append(("signal_review", run_id))

    async def retry_export(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        self.calls.append(("retry_export", run_id))

    async def retry_item(self, org_id: UUID, run_id: UUID, item_index: int, epoch: int) -> None:
        self.calls.append(("retry_item", run_id))


@dataclasses.dataclass
class Ctx:
    client: TestClient
    csrf: str
    engine: RecordingEngine
    runs: RunService
    org_id: UUID
    user_id: UUID
    owner_url: str

    def create(self, items: int = 3, **kwargs: Any) -> UUID:
        call = functools.partial(
            self.runs.create,
            self.org_id,
            self.user_id,
            template_key="questionnaire",
            title="Northwind security questionnaire",
            input={"knowledge_base_ids": []},
            items=[NewItem(question=f"Question {i}?", section="General") for i in range(items)],
            **kwargs,
        )
        return self.client.portal.call(call)  # type: ignore[union-attr]

    def owner_sql(self, sql: str, **params: Any) -> list[Any]:
        engine = create_sync_engine(self.owner_url)
        try:
            with engine.begin() as connection:
                result = connection.execute(text(sql), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            engine.dispose()

    def add_events(self, run_id: UUID, count: int) -> None:
        for i in range(count):
            self.owner_sql(
                "INSERT INTO app.run_events (org_id, run_id, type, item_index) "
                "VALUES (:o, :r, 'item_done', :i)",
                o=self.org_id,
                r=run_id,
                i=i,
            )

    def finish(self, run_id: UUID) -> None:
        self.owner_sql("UPDATE app.runs SET status = 'succeeded' WHERE id = :r", r=run_id)


@pytest.fixture
def app(database: Database, tmp_path: Path) -> FastAPI:
    return create_app(settings_for(database, model_provider="fake", blob_root=tmp_path / "b"))


@pytest.fixture
def ctx(app: FastAPI, database: Database, make_user: UserFactory) -> Iterator[Ctx]:
    with TestClient(app) as client:
        csrf = login(client, make_user(Role.MEMBER))
        session = client.get("/v1/auth/session").json()
        svc = services_of(client)
        engine = RecordingEngine()
        runs = RunService(svc.sessions, engine)
        app.state.services = dataclasses.replace(svc, runs=runs, run_engine=engine)
        yield Ctx(
            client,
            csrf,
            engine,
            runs,
            UUID(session["org_id"]),
            UUID(session["user_id"]),
            database.url("agentforge_owner"),
        )


def read_stream(
    client: TestClient, run_id: UUID, last_event_id: int | None = None
) -> tuple[list[int], str | None]:
    """Event seqs received, and the final status from the `end` event."""
    headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
    seqs: list[int] = []
    event_type = None
    with client.stream("GET", f"/v1/runs/{run_id}/stream", headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event: "):
                event_type = line.removeprefix("event: ")
            elif line.startswith("id: "):
                seqs.append(int(line.removeprefix("id: ")))
            elif line.startswith("data: ") and event_type == "end":
                return seqs, json.loads(line.removeprefix("data: "))["status"]
    return seqs, None


def all_seqs(ctx: Ctx, run_id: UUID) -> list[int]:
    rows = ctx.owner_sql("SELECT seq FROM app.run_events WHERE run_id = :r ORDER BY seq", r=run_id)
    return [r.seq for r in rows]


def test_create_list_and_read_runs(ctx: Ctx) -> None:
    run_id = ctx.create(items=3)
    assert ctx.engine.calls == [("start", run_id)]
    [listed] = [r for r in ctx.client.get("/v1/runs").json() if r["id"] == str(run_id)]
    assert listed["status"] == "queued"
    assert listed["item_count"] == 3
    assert listed["items_done"] == 0
    detail = ctx.client.get(f"/v1/runs/{run_id}").json()
    assert detail["title"] == "Northwind security questionnaire"
    items = ctx.client.get(f"/v1/runs/{run_id}/items").json()
    assert [i["question"] for i in items] == ["Question 0?", "Question 1?", "Question 2?"]
    assert {i["status"] for i in items} == {"pending"}
    events = ctx.client.get(f"/v1/runs/{run_id}/events").json()
    assert [e["type"] for e in events] == ["run_created"]
    assert ctx.client.get("/v1/runs", params={"status": "succeeded"}).json() == [] or all(
        r["status"] == "succeeded"
        for r in ctx.client.get("/v1/runs", params={"status": "succeeded"}).json()
    )


def test_stream_catches_up_from_last_event_id_then_ends(ctx: Ctx) -> None:
    run_id = ctx.create()
    ctx.add_events(run_id, 5)
    ctx.finish(run_id)
    seqs = all_seqs(ctx, run_id)
    received, status = read_stream(ctx.client, run_id, last_event_id=seqs[2])
    assert received == seqs[3:]
    assert status == "succeeded"
    everything, _ = read_stream(ctx.client, run_id)
    assert everything == seqs


def test_stream_delivers_live_events_without_gaps_or_duplicates(ctx: Ctx) -> None:
    run_id = ctx.create()

    def produce() -> None:
        time.sleep(0.5)
        for _ in range(3):
            ctx.add_events(run_id, 4)
            time.sleep(0.2)
        ctx.finish(run_id)

    producer = threading.Thread(target=produce)
    producer.start()
    started = time.monotonic()
    received, status = read_stream(ctx.client, run_id)
    producer.join()
    assert status == "succeeded"
    assert received == all_seqs(ctx, run_id)  # every event, in order, exactly once
    assert len(received) == 13  # run_created + 12
    assert time.monotonic() - started < 15, "live events were not pushed promptly"


def test_reconnecting_with_last_event_id_resumes_exactly(ctx: Ctx) -> None:
    """A client that saw events up to seq k reconnects with Last-Event-ID: k while the run is
    still producing: it gets exactly the events after k, live ones included, once each.

    (Starlette's TestClient buffers a response until it ends, so a mid-stream drop cannot be
    simulated here; the browser's own drop-and-reconnect is a Playwright test in R1-38.)"""
    run_id = ctx.create()
    ctx.add_events(run_id, 6)
    seen = all_seqs(ctx, run_id)[:4]  # what the client had received before the drop

    def produce() -> None:
        time.sleep(0.5)
        ctx.add_events(run_id, 3)
        ctx.finish(run_id)

    producer = threading.Thread(target=produce)
    producer.start()
    rest, status = read_stream(ctx.client, run_id, last_event_id=seen[-1])
    producer.join()
    assert status == "succeeded"
    assert seen + rest == all_seqs(ctx, run_id)


def test_notifications_carry_ids_only(ctx: Ctx, database: Database) -> None:
    run_id = ctx.create()

    async def listen() -> list[str]:
        async with await psycopg.AsyncConnection.connect(
            database.url("agentforge_app"), autocommit=True
        ) as connection:
            await connection.execute("LISTEN run_events")
            await asyncio.to_thread(ctx.add_events, run_id, 1)
            payloads: list[str] = []
            async for notification in connection.notifies(timeout=5, stop_after=1):
                payloads.append(notification.payload)
            return payloads

    [payload] = asyncio.run(listen())
    run_part, seq_part = payload.split(":")
    assert run_part == str(run_id)
    assert int(seq_part) == all_seqs(ctx, run_id)[-1]


def test_cancel(ctx: Ctx) -> None:
    run_id = ctx.create()
    headers = {"X-CSRF-Token": ctx.csrf}
    response = ctx.client.post(f"/v1/runs/{run_id}/cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert ("cancel", run_id) in ctx.engine.calls
    assert [e["type"] for e in ctx.client.get(f"/v1/runs/{run_id}/events").json()][-1] == (
        "run_cancelled"
    )
    assert ctx.client.post(f"/v1/runs/{run_id}/cancel", headers=headers).status_code == 409
    assert ctx.client.post(f"/v1/runs/{run_id}/cancel").status_code == 403  # no CSRF token


def test_run_events_are_append_only(ctx: Ctx, database: Database) -> None:
    run_id = ctx.create()

    async def tamper() -> None:
        engine = create_app_engine(database.url("agentforge_app"), pool_size=1)
        try:
            async with create_sessionmaker(engine)() as db, db.begin():
                await db.execute(
                    text("SELECT set_config('app.org_id', :o, true)"), {"o": str(ctx.org_id)}
                )
                await db.execute(
                    text("UPDATE app.run_events SET type = 'forged' WHERE run_id = :r"),
                    {"r": run_id},
                )
        finally:
            await engine.dispose()

    with pytest.raises(DBAPIError, match="permission denied"):
        asyncio.run(tamper())


def test_a_lost_start_is_found_by_the_reconciler_query(ctx: Ctx) -> None:
    ctx.engine.fail_start = True
    with pytest.raises(ConnectionError):
        ctx.create()
    [lost] = ctx.owner_sql(
        "SELECT id FROM app.runs WHERE org_id = :o AND status = 'queued' "
        "ORDER BY created_at DESC LIMIT 1",
        o=ctx.org_id,
    )
    assert ctx.client.portal.call(ctx.runs.stale_queued, 60.0) == [] or all(  # type: ignore[union-attr]
        r[1] != lost.id
        for r in ctx.client.portal.call(ctx.runs.stale_queued, 60.0)  # type: ignore[union-attr]
    )
    ctx.owner_sql(
        "UPDATE app.runs SET created_at = now() - interval '5 minutes' WHERE id = :r", r=lost.id
    )
    found = ctx.client.portal.call(ctx.runs.stale_queued, 60.0)  # type: ignore[union-attr]
    assert (ctx.org_id, lost.id, "questionnaire") in found


def test_other_organizations_cannot_see_runs(ctx: Ctx, database: Database, app: FastAPI) -> None:
    run_id = ctx.create()
    other_org = uuid.uuid4()
    ctx.owner_sql(
        "INSERT INTO app.organizations (id, name, slug) VALUES (:o, 'Other', :s)",
        o=other_org,
        s=f"other-{other_org.hex[:8]}",
    )
    email = f"outsider-{uuid.uuid4().hex[:8]}@example.test"

    async def make_outsider() -> None:
        engine = create_app_engine(database.url("agentforge_app"), pool_size=1)
        await create_user(
            create_sessionmaker(engine),
            org_id=other_org,
            email=email,
            password=PASSWORD,
            role=Role.ADMIN,
        )
        await engine.dispose()

    asyncio.run(make_outsider())
    with TestClient(app) as outsider:
        csrf = login(outsider, email)
        assert outsider.get("/v1/runs").json() == []
        for path in ("", "/items", "/events", "/stream"):
            assert outsider.get(f"/v1/runs/{run_id}{path}").status_code == 404, path
        assert (
            outsider.post(f"/v1/runs/{run_id}/cancel", headers={"X-CSRF-Token": csrf}).status_code
            == 404
        )
