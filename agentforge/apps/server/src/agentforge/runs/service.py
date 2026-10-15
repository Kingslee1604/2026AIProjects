"""Run service on the RunEngine port: create, read, cancel, and follow runs."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.logging import get_logger
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.runs.events import RunEvent, emit, events_after
from agentforge.runs.port import RunEngine

log = get_logger("agentforge.runs")

FINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})


class NotFoundError(Exception):
    pass


class InvalidStateError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class NewItem:
    question: str
    section: str | None = None
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Run:
    id: UUID
    template_key: str
    title: str
    status: str
    input: dict[str, Any]
    budgets: dict[str, Any]
    usage: dict[str, Any]
    epoch: int
    error_class: str | None
    error: str | None
    item_count: int
    items_done: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class RunItem:
    id: UUID
    item_index: int
    source_ref: str | None
    section: str | None
    question: str
    status: str
    answer: str | None
    confidence: str | None
    reason: str | None
    claims: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    completed_at: datetime | None


_RUN_COLUMNS = (
    "r.id, r.template_key, r.title, r.status, r.input, r.budgets, r.usage, r.epoch, "
    "r.error_class, r.error, r.item_count, r.created_at, r.started_at, r.finished_at, "
    "(SELECT count(*) FROM app.run_items i WHERE i.run_id = r.id "
    "AND i.status IN ('answered', 'insufficient_evidence', 'failed', 'skipped')) AS items_done"
)


def _run(row: Any) -> Run:
    return Run(
        id=row.id,
        template_key=row.template_key,
        title=row.title,
        status=row.status,
        input=dict(row.input),
        budgets=dict(row.budgets),
        usage=dict(row.usage),
        epoch=row.epoch,
        error_class=row.error_class,
        error=row.error,
        item_count=row.item_count,
        items_done=row.items_done,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


class RunService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], engine: RunEngine) -> None:
        self._sessions = sessions
        self._engine = engine

    async def create(
        self,
        org_id: UUID,
        user_id: UUID | None,
        *,
        template_key: str,
        title: str,
        input: dict[str, Any],
        items: Sequence[NewItem] = (),
        budgets: dict[str, Any] | None = None,
    ) -> UUID:
        """Insert the run (and its items) and commit, then start it. If the process dies in
        between, the worker's start reconciler starts it (gate H7)."""
        run_id = uuid4()
        async with tenant_session(self._sessions, TenantContext(org_id, user_id=user_id)) as db:
            await db.execute(
                text(
                    "INSERT INTO app.runs (id, org_id, created_by, template_key, title, input, "
                    "budgets, item_count) VALUES (:id, :o, :u, :k, :t, CAST(:i AS jsonb), "
                    "CAST(:b AS jsonb), :n)"
                ),
                {
                    "id": run_id,
                    "o": org_id,
                    "u": user_id,
                    "k": template_key,
                    "t": title.strip()[:300],
                    "i": json.dumps(input),
                    "b": json.dumps(budgets or {}),
                    "n": len(items),
                },
            )
            if items:
                await db.execute(
                    text(
                        "INSERT INTO app.run_items (org_id, run_id, item_index, question, "
                        "section, source_ref) VALUES (:o, :r, :i, :q, :s, :ref)"
                    ),
                    [
                        {
                            "o": org_id,
                            "r": run_id,
                            "i": index,
                            "q": item.question,
                            "s": item.section,
                            "ref": item.source_ref,
                        }
                        for index, item in enumerate(items)
                    ],
                )
            await emit(db, org_id, run_id, "run_created", data={"items": len(items)})
        await self._engine.start(org_id, run_id, template_key)
        log.info("run_created", run_id=str(run_id), template=template_key, items=len(items))
        return run_id

    async def get(self, org_id: UUID, run_id: UUID) -> Run:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            row = (
                await db.execute(
                    text(f"SELECT {_RUN_COLUMNS} FROM app.runs r WHERE r.id = :r"),  # noqa: S608
                    {"r": run_id},
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("run not found")
        return _run(row)

    async def list_runs(
        self, org_id: UUID, *, limit: int = 50, status: str | None = None
    ) -> list[Run]:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            rows = await db.execute(
                text(
                    f"SELECT {_RUN_COLUMNS} FROM app.runs r "  # noqa: S608
                    "WHERE (CAST(:s AS text) IS NULL OR r.status = :s) "
                    "ORDER BY r.created_at DESC LIMIT :n"
                ),
                {"s": status, "n": limit},
            )
            return [_run(r) for r in rows]

    async def items(self, org_id: UUID, run_id: UUID) -> list[RunItem]:
        await self.get(org_id, run_id)
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            rows = await db.execute(
                text(
                    "SELECT id, item_index, source_ref, section, question, status, answer, "
                    "confidence, reason, claims, evidence, completed_at FROM app.run_items "
                    "WHERE run_id = :r ORDER BY item_index"
                ),
                {"r": run_id},
            )
            return [
                RunItem(
                    r.id,
                    r.item_index,
                    r.source_ref,
                    r.section,
                    r.question,
                    r.status,
                    r.answer,
                    r.confidence,
                    r.reason,
                    list(r.claims),
                    list(r.evidence),
                    r.completed_at,
                )
                for r in rows
            ]

    async def events(self, org_id: UUID, run_id: UUID, after_seq: int = 0) -> list[RunEvent]:
        await self.get(org_id, run_id)  # 404 for unknown and other tenants' runs
        return await events_after(self._sessions, org_id, run_id, after_seq)

    async def stale_queued(self, older_than_seconds: float) -> list[tuple[UUID, UUID, str]]:
        """(org_id, run_id, template_key) of runs created but never started, across tenants,
        through the narrow SECURITY DEFINER function (IDs only)."""
        async with self._sessions() as db, db.begin():
            rows = await db.execute(
                text(
                    "SELECT org_id, run_id, template_key FROM "
                    "app.stale_queued_runs(make_interval(secs => :s))"
                ),
                {"s": older_than_seconds},
            )
            return [(r.org_id, r.run_id, r.template_key) for r in rows]

    async def cancel(self, org_id: UUID, run_id: UUID, user_id: UUID | None) -> Run:
        run = await self.get(org_id, run_id)
        if run.status in FINAL_STATUSES:
            raise InvalidStateError(f"run is already {run.status}")
        await self._engine.cancel(org_id, run_id)
        async with tenant_session(self._sessions, TenantContext(org_id, user_id=user_id)) as db:
            moved = (
                await db.execute(
                    text(
                        "UPDATE app.runs SET status = 'cancelled', finished_at = now(), "
                        "updated_at = now() WHERE id = :r AND status IN "
                        "('queued', 'running', 'waiting_review') RETURNING id"
                    ),
                    {"r": run_id},
                )
            ).one_or_none()
            if moved is not None:
                await emit(db, org_id, run_id, "run_cancelled", data={"by": str(user_id)})
        log.info("run_cancelled", run_id=str(run_id))
        return await self.get(org_id, run_id)
