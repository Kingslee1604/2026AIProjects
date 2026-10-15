"""Run service on the RunEngine port: create, read, cancel, and follow runs."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge import audit
from agentforge.core.logging import get_logger
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.knowledge.service import Actor
from agentforge.qa.service import ModelCall
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
    plan: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Artifact:
    id: UUID
    run_id: UUID
    node_id: str
    epoch: int
    filename: str
    media_type: str
    size_bytes: int
    created_at: datetime
    blob_key: str


@dataclass(frozen=True, slots=True)
class StepRecord:
    id: UUID
    node_id: str
    item_index: int | None
    epoch: int
    attempt: int
    status: str
    error_class: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class Trace:
    """A run's step attempts, and its budget use so far against its limits."""

    steps: list[StepRecord]
    llm_calls: int
    tokens: int
    seconds: float
    max_llm_calls: int
    max_seconds: float


@dataclass(frozen=True, slots=True)
class Limits:
    """Default run budgets (a run's own `budgets` override them)."""

    max_llm_calls: int = 3000
    max_seconds: float = 6 * 3600


_ARTIFACT_COLUMNS = (
    "id, run_id, node_id, epoch, filename, media_type, size_bytes, created_at, blob_key"
)

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
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        engine: RunEngine,
        limits: Limits | None = None,
    ) -> None:
        self._sessions = sessions
        self._engine = engine
        self._limits = limits or Limits()

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
                    "confidence, reason, claims, evidence, completed_at, plan FROM app.run_items "
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
                    dict(r.plan),
                )
                for r in rows
            ]

    async def events(self, org_id: UUID, run_id: UUID, after_seq: int = 0) -> list[RunEvent]:
        await self.get(org_id, run_id)  # 404 for unknown and other tenants' runs
        return await events_after(self._sessions, org_id, run_id, after_seq)

    async def trace(self, org_id: UUID, run_id: UUID) -> Trace:
        """Every step attempt (oldest first) and the budget used so far (model calls, tokens,
        and wall-clock time, counted as the run's budget check counts them)."""
        run = await self.get(org_id, run_id)
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            rows = await db.execute(
                text(
                    "SELECT id, node_id, item_index, epoch, attempt, status, error_class, error, "
                    "started_at, finished_at FROM app.step_records WHERE run_id = :r "
                    "ORDER BY started_at, id"
                ),
                {"r": run_id},
            )
            steps = [StepRecord(**r._mapping) for r in rows]  # pyright: ignore[reportPrivateUsage]
            used = (
                await db.execute(
                    text(
                        "SELECT count(*) AS n, coalesce(sum(coalesce(prompt_tokens, 0) "
                        "+ coalesce(completion_tokens, 0)), 0) AS tokens, "
                        "(SELECT extract(epoch FROM coalesce(finished_at, now()) - started_at) "
                        "FROM app.runs WHERE id = :r) AS seconds "
                        "FROM app.llm_calls WHERE run_id = :r AND purpose <> 'retrieve'"
                    ),
                    {"r": run_id},
                )
            ).one()
        return Trace(
            steps=steps,
            llm_calls=int(used.n),
            tokens=int(used.tokens),
            seconds=round(float(used.seconds or 0.0), 1),
            max_llm_calls=int(run.budgets.get("max_llm_calls", self._limits.max_llm_calls)),
            max_seconds=float(run.budgets.get("max_seconds", self._limits.max_seconds)),
        )

    async def step_calls(self, org_id: UUID, run_id: UUID, step_id: UUID) -> list[ModelCall]:
        """The model calls one step attempt made: prompts, responses, tokens, latency."""
        await self.get(org_id, run_id)
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            rows = await db.execute(
                text(
                    "SELECT id, purpose, model, status, attempt, latency_ms, prompt_tokens, "
                    "completion_tokens, error_class, request, response, created_at "
                    "FROM app.llm_calls WHERE run_id = :r AND step_id = :s "
                    "ORDER BY created_at, attempt"
                ),
                {"r": run_id, "s": step_id},
            )
            return [
                ModelCall(
                    id=r.id,
                    purpose=r.purpose,
                    model=r.model,
                    status=r.status,
                    attempt=r.attempt,
                    latency_ms=r.latency_ms,
                    prompt_tokens=r.prompt_tokens,
                    completion_tokens=r.completion_tokens,
                    error_class=r.error_class,
                    request=list(r.request),
                    response=r.response,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def artifacts(self, org_id: UUID, run_id: UUID) -> list[Artifact]:
        """Files the run produced, newest version first."""
        await self.get(org_id, run_id)
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            rows = await db.execute(
                text(
                    f"SELECT {_ARTIFACT_COLUMNS} FROM app.artifacts "  # noqa: S608
                    "WHERE run_id = :r ORDER BY epoch DESC, created_at DESC"
                ),
                {"r": run_id},
            )
            return [Artifact(**r._mapping) for r in rows]  # pyright: ignore[reportPrivateUsage]

    async def artifact_for_download(
        self, org_id: UUID, run_id: UUID, artifact_id: UUID, actor: Actor
    ) -> Artifact:
        """One artifact of one run, read under the caller's tenant (another organization's
        artifact is simply not found), with the download audited."""
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            row = (
                await db.execute(
                    text(
                        f"SELECT {_ARTIFACT_COLUMNS} FROM app.artifacts "  # noqa: S608
                        "WHERE id = :a AND run_id = :r"
                    ),
                    {"a": artifact_id, "r": run_id},
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("artifact not found")
        found = Artifact(**row._mapping)  # pyright: ignore[reportPrivateUsage]
        await audit.record(
            self._sessions,
            audit.AuditEvent(
                "artifact.download",
                "success",
                org_id=org_id,
                actor_user_id=actor.user_id,
                actor_email=actor.email,
                target=f"run:{run_id}#artifact:{artifact_id}",
                ip=actor.ip,
                details={"epoch": found.epoch},
            ),
        )
        return found

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

    # --- questionnaire runs (R1-33) ------------------------------------------------------------

    async def start_questionnaire(
        self,
        org_id: UUID,
        user_id: UUID | None,
        *,
        questionnaire_id: UUID,
        items: Sequence[NewItem],
        knowledge_base_ids: Sequence[UUID],
        title: str,
        budgets: dict[str, Any] | None = None,
    ) -> UUID:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            found = (
                await db.execute(
                    text("SELECT count(*) FROM app.knowledge_bases WHERE id = ANY(:ids)"),
                    {"ids": list(knowledge_base_ids)},
                )
            ).scalar_one()
        if found != len(set(knowledge_base_ids)):
            raise NotFoundError("knowledge base not found")
        return await self.create(
            org_id,
            user_id,
            template_key="questionnaire",
            title=title,
            input={
                "questionnaire_id": str(questionnaire_id),
                "knowledge_base_ids": [str(k) for k in dict.fromkeys(knowledge_base_ids)],
            },
            items=items,
            budgets=budgets,
        )

    async def _bump_epoch(
        self,
        org_id: UUID,
        run_id: UUID,
        allowed: set[str],
        *,
        reopen: bool,
        event: str,
        data: dict[str, Any],
    ) -> int:
        async with tenant_session(self._sessions, TenantContext(org_id)) as db:
            row = (
                await db.execute(
                    text("SELECT status, epoch FROM app.runs WHERE id = :r FOR UPDATE"),
                    {"r": run_id},
                )
            ).one_or_none()
            if row is None:
                raise NotFoundError("run not found")
            if row.status not in allowed:
                raise InvalidStateError(f"run is {row.status}")
            epoch = int(row.epoch) + 1
            await db.execute(
                text(
                    "UPDATE app.runs SET epoch = :e, updated_at = now(), finished_at = CASE WHEN "
                    ":reopen THEN NULL ELSE finished_at END, status = CASE WHEN :reopen THEN "
                    "'running' ELSE status END WHERE id = :r"
                ),
                {"e": epoch, "reopen": reopen, "r": run_id},
            )
            await emit(db, org_id, run_id, event, epoch=epoch, data=data)
        return epoch

    async def retry_item(self, org_id: UUID, run_id: UUID, item_index: int) -> int:
        """Answer one item again while the run waits for review (a new epoch)."""
        items = {i.item_index: i for i in await self.items(org_id, run_id)}
        item = items.get(item_index)
        if item is None:
            raise NotFoundError("item not found")
        if item.status in {"skipped", "pending", "answering"}:
            raise InvalidStateError(f"item is {item.status}")
        epoch = await self._bump_epoch(
            org_id,
            run_id,
            {"waiting_review"},
            reopen=False,
            event="item_retry_requested",
            data={"item_index": item_index},
        )
        await self._engine.retry_item(org_id, run_id, item_index, epoch)
        return epoch

    async def retry_export(self, org_id: UUID, run_id: UUID) -> int:
        """Export again (a new epoch, so a new artifact; the earlier one is kept)."""
        epoch = await self._bump_epoch(
            org_id, run_id, {"succeeded"}, reopen=True, event="export_retry_requested", data={}
        )
        await self._engine.retry_export(org_id, run_id, epoch)
        return epoch

    async def signal_review(self, org_id: UUID, run_id: UUID) -> None:
        """Tell a run waiting for review to continue to the export."""
        run = await self.get(org_id, run_id)
        if run.status != "waiting_review":
            raise InvalidStateError(f"run is {run.status}")
        await self._engine.signal_review(org_id, run_id)
