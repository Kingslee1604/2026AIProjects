"""Fixtures for the engine conformance tests: Postgres, the worker process, and the client.

Run with:  CONF_ENGINE=custom uv run --group engine-spikes pytest spikes/engine/tests
"""

import asyncio
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlencode

import pytest
from sqlalchemy import Engine, text

from agentforge.db.engine import create_app_engine, create_sessionmaker, create_sync_engine
from agentforge.db.migrate import seed_defaults, upgrade
from agentforge.db.tenancy import TenantContext, tenant_session
from spikes.engine.harness import schema
from spikes.engine.harness.port import EngineConfig, RunEngine
from spikes.engine.harness.worker import load_engine

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

ROOT = Path(__file__).resolve().parents[3]  # agentforge/
RESULTS = ROOT / "spikes" / "engine" / "results"
ENGINE = os.environ.get("CONF_ENGINE", "stub")
PG_IMAGE = (
    "pgvector/pgvector:0.8.7-pg18-trixie"
    "@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0"
)
T = TypeVar("T")


@dataclass(frozen=True)
class Database:
    host: str
    port: int
    passwords: dict[str, str]

    def url(self, role: str, app_name: str | None = None) -> str:
        query = f"?{urlencode({'application_name': app_name})}" if app_name else ""
        return (
            f"postgresql://{role}:{self.passwords[role]}@{self.host}:{self.port}/agentforge{query}"
        )


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    from testcontainers.community.postgres import PostgresContainer

    passwords = {
        r: secrets.token_urlsafe(18)
        for r in ("agentforge_owner", "agentforge_app", "agentforge_engine")
    }
    container = (
        PostgresContainer(
            PG_IMAGE,
            username="agentforge_owner",
            password=passwords["agentforge_owner"],
            dbname="agentforge",
            driver=None,
        )
        .with_env("AGENTFORGE_APP_DB_PASSWORD", passwords["agentforge_app"])
        .with_env("AGENTFORGE_ENGINE_DB_PASSWORD", passwords["agentforge_engine"])
        .with_volume_mapping(
            str(ROOT / "deploy/postgres/initdb"), "/docker-entrypoint-initdb.d", "ro"
        )
        .with_command("postgres -c max_connections=300")
    )
    with container:
        db = Database(
            container.get_container_host_ip(), int(container.get_exposed_port(5432)), passwords
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                probe = create_sync_engine(db.url("agentforge_app"))
                with probe.connect() as c:
                    c.execute(text("SELECT 1"))
                probe.dispose()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        upgrade(db.url("agentforge_owner"))
        seed_defaults(db.url("agentforge_owner"))
        yield db


@pytest.fixture
def owner(database: Database) -> Iterator[Engine]:
    """Superuser connection for setup and for gate assertions (bypasses RLS)."""
    engine = create_sync_engine(database.url("agentforge_owner"))
    with engine.begin() as c:
        for statement in [*schema.STATEMENTS, *schema.RESET_ENGINE_SCHEMA]:
            c.execute(text(statement))
    yield engine
    engine.dispose()


def query(owner: Engine, sql: str, **params: Any) -> list[Any]:
    with owner.connect() as c:
        return list(c.execute(text(sql), params).all())


class Worker:
    """The engine's worker as a real OS process, so tests can SIGKILL it (H1, H2)."""

    def __init__(self, database: Database, *, latency: float, lease: float) -> None:
        self.env = {
            **os.environ,
            "CONF_APP_URL": database.url("agentforge_app", "conf-worker"),
            "CONF_ENGINE_URL": database.url("agentforge_engine", "conf-worker"),
            "CONF_LLM_LATENCY": str(latency),
            "CONF_LEASE_SECONDS": str(lease),
            "PYTHONUNBUFFERED": "1",
        }
        self.process: subprocess.Popen[str] | None = None
        self.kills = 0
        self.log = RESULTS / f"{ENGINE}-worker.log"

    def start(self) -> float:
        """Start and wait until ready; returns the startup time in seconds."""
        started = time.monotonic()
        RESULTS.mkdir(parents=True, exist_ok=True)
        log = self.log.open("a")
        self.process = subprocess.Popen(  # noqa: S603 - fixed module, test-only
            [sys.executable, "-m", "spikes.engine.harness.worker", ENGINE],
            cwd=ROOT,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if "worker ready" not in line:
            raise RuntimeError(f"worker did not start; see {self.log}")
        return time.monotonic() - started

    @property
    def pid(self) -> int:
        assert self.process is not None
        return self.process.pid

    def kill(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGKILL)
            self.process.wait()
            self.kills += 1

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.kill()

    def threads(self) -> int:
        status = Path(f"/proc/{self.pid}/status").read_text()
        return int(
            next(line.split()[1] for line in status.splitlines() if line.startswith("Threads:"))
        )


@dataclass
class Harness:
    """Client side: what the API process would do, plus helpers for the gate checks."""

    database: Database
    owner: Engine
    loop: asyncio.AbstractEventLoop
    engine: RunEngine
    sessions: Any
    org_ids: list[uuid.UUID]
    worker: Worker
    results: dict[str, Any] = field(default_factory=dict)

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        return self.loop.run_until_complete(coro)

    async def _create(self, org_id: uuid.UUID, items: int, parallelism: int) -> uuid.UUID:
        run_id = uuid.uuid4()
        async with tenant_session(self.sessions, TenantContext(org_id)) as db:
            await db.execute(
                text(
                    "INSERT INTO conformance.runs (id, org_id, item_count, max_parallelism) "
                    "VALUES (:r, :o, :n, :p)"
                ),
                {"r": run_id, "o": org_id, "n": items, "p": parallelism},
            )
        return run_id

    def create_run(
        self, *, items: int = 20, parallelism: int = 4, start: bool = True, org: int = 0
    ) -> tuple[uuid.UUID, uuid.UUID]:
        org_id = self.org_ids[org % len(self.org_ids)]
        run_id = self.run(self._create(org_id, items, parallelism))
        if start:
            self.run(self.engine.start(org_id, run_id))
        return org_id, run_id

    async def _set(self, org_id: uuid.UUID, run_id: uuid.UUID, sql: str) -> None:
        async with tenant_session(self.sessions, TenantContext(org_id)) as db:
            await db.execute(text(sql), {"r": run_id})

    def approve(self, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.run(
            self._set(
                org_id,
                run_id,
                "UPDATE conformance.runs SET review_decision = 'approved' "
                "WHERE id = :r AND review_decision IS NULL",
            )
        )
        self.run(self.engine.signal_review(org_id, run_id, "approved"))

    def retry_export(self, org_id: uuid.UUID, run_id: uuid.UUID) -> int:
        self.run(
            self._set(
                org_id,
                run_id,
                "UPDATE conformance.runs SET epoch = epoch + 1, status = 'running' WHERE id = :r",
            )
        )
        epoch = int(
            query(self.owner, "SELECT epoch FROM conformance.runs WHERE id = :r", r=run_id)[0][0]
        )
        self.run(self.engine.retry_export(org_id, run_id, epoch))
        return epoch

    def cancel(self, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.run(self.engine.cancel(org_id, run_id))

    def statuses(self, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        rows = query(
            self.owner, "SELECT id, status FROM conformance.runs WHERE id = ANY(:ids)", ids=run_ids
        )
        return {r.id: r.status for r in rows}

    def wait_for(self, predicate: Callable[[], bool], timeout: float, every: float = 0.25) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(every)
        return predicate()

    def drive(
        self,
        runs: list[tuple[uuid.UUID, uuid.UUID]],
        timeout: float,
        tick: Callable[[], None] | None = None,
    ) -> bool:
        """Approve runs as they reach review; True when all succeeded within the timeout."""
        deadline = time.monotonic() + timeout
        ids = [r for _, r in runs]
        org_of = {r: o for o, r in runs}
        while time.monotonic() < deadline:
            states = self.statuses(ids)
            for run_id, status in states.items():
                if status == "waiting_review":
                    self.approve(org_of[run_id], run_id)
            if all(s == "succeeded" for s in states.values()):
                return True
            if tick:
                tick()
            time.sleep(0.25)
        return False


def record(gate: str, passed: bool, **metrics: Any) -> None:
    """Append the gate outcome to spikes/engine/results/<engine>.jsonl (ADR-2 evidence)."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    line = {"engine": ENGINE, "gate": gate, "passed": passed, "at": time.time(), **metrics}
    with (RESULTS / f"{ENGINE}.jsonl").open("a") as f:
        f.write(json.dumps(line, default=str) + "\n")


def make_harness(
    database: Database, owner: Engine, *, latency: float, lease: float = 6.0
) -> Iterator[Harness]:
    org_ids = [uuid.uuid4(), uuid.uuid4()]
    with owner.begin() as c:
        for org_id in org_ids:
            c.execute(
                text("INSERT INTO app.organizations (id, name, slug) VALUES (:o, :n, :s)"),
                {"o": org_id, "n": f"Org {org_id.hex[:6]}", "s": f"org-{org_id.hex[:8]}"},
            )
    loop = asyncio.new_event_loop()
    app_engine = create_app_engine(database.url("agentforge_app", "conf-client"), pool_size=5)
    config = EngineConfig(
        app_url=database.url("agentforge_app", "conf-client"),
        engine_url=database.url("agentforge_engine", "conf-client"),
        worker_id="client",
        lease_seconds=lease,
    )
    engine = load_engine(ENGINE, config)
    loop.run_until_complete(engine.open())
    worker = Worker(database, latency=latency, lease=lease)
    worker.start()
    harness = Harness(
        database, owner, loop, engine, create_sessionmaker(app_engine), org_ids, worker
    )
    try:
        yield harness
    finally:
        worker.stop()
        loop.run_until_complete(engine.close())
        loop.run_until_complete(app_engine.dispose())
        loop.close()


@pytest.fixture
def harness(database: Database, owner: Engine) -> Iterator[Harness]:
    yield from make_harness(database, owner, latency=0.05)


@pytest.fixture
def slow_harness(database: Database, owner: Engine) -> Iterator[Harness]:
    """One-second model calls: cancellation timing (H5)."""
    yield from make_harness(database, owner, latency=1.0)
