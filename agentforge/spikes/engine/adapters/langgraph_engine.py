"""Candidate 2 (R1-22): LangGraph with the Postgres checkpointer, no LangGraph Platform.

The graph: begin -> plan -> Send fan-out to `item` (max_concurrency=4 per invocation, H3) ->
review (interrupt) -> export -> finish. Checkpoints live in the `engine` schema.

LangGraph only executes a graph while something calls it, so this adapter adds what the
library does not provide: a job queue (engine.lg_jobs) with leases and heartbeats that decides
which worker invokes which run, re-invokes after a crash (resume from the last checkpoint), and
resumes an interrupted run when the review signal arrives. Cancel cancels the invocation.
"""

import asyncio
import contextlib
import operator
from typing import Annotated, Any, TypedDict
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agentforge.db.engine import create_app_engine, create_sessionmaker
from spikes.engine.harness.port import EngineConfig
from spikes.engine.harness.steps import RunGoneError, Steps

# --- the graph ------------------------------------------------------------------------------


class RunState(TypedDict, total=False):
    org: str
    run: str
    alive: bool
    count: int
    done: Annotated[list[int], operator.add]
    epoch: int


class ItemInput(TypedDict):
    org: str
    run: str
    idx: int


def build_graphs(steps: Steps) -> tuple[StateGraph[Any], StateGraph[Any]]:
    async def begin(state: RunState) -> RunState:
        try:
            alive = await steps.begin(UUID(state["org"]), UUID(state["run"]))
        except RunGoneError:
            alive = False
        return {"alive": alive}

    async def plan(state: RunState) -> RunState:
        return {"count": await steps.plan(UUID(state["org"]), UUID(state["run"]))}

    def fan_out(state: RunState) -> list[Send] | str:
        if not state.get("alive"):
            return END
        return [
            Send("item", {"org": state["org"], "run": state["run"], "idx": i})
            for i in range(state["count"])
        ]

    async def item(payload: ItemInput) -> RunState:
        idx = await steps.process_item(UUID(payload["org"]), UUID(payload["run"]), payload["idx"])
        return {"done": [idx]}

    async def review(state: RunState) -> RunState:
        org, run = UUID(state["org"]), UUID(state["run"])
        await steps.enter_review(org, run)
        interrupt("review")  # resumed with Command(resume=decision); the node re-runs from start
        await steps.leave_review(org, run)
        _, epoch, _, _ = await steps.run_info(org, run)
        return {"epoch": epoch}

    async def export(state: RunState) -> RunState:
        await steps.export(UUID(state["org"]), UUID(state["run"]), state["epoch"])
        return {}

    async def finish(state: RunState) -> RunState:
        await steps.finish(UUID(state["org"]), UUID(state["run"]))
        return {}

    run = StateGraph(RunState)
    run.add_node("begin", begin)
    run.add_node("plan", plan)
    run.add_node("item", item)
    run.add_node("review", review)
    run.add_node("export", export)
    run.add_node("finish", finish)
    run.add_edge(START, "begin")
    run.add_edge("begin", "plan")
    run.add_conditional_edges("plan", fan_out, ["item", END])
    run.add_edge("item", "review")
    run.add_edge("review", "export")
    run.add_edge("export", "finish")
    run.add_edge("finish", END)

    retry = StateGraph(RunState)
    retry.add_node("export", export)
    retry.add_node("finish", finish)
    retry.add_edge(START, "export")
    retry.add_edge("export", "finish")
    retry.add_edge("finish", END)
    return run, retry


# --- the job queue LangGraph needs around it --------------------------------------------------

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS engine.lg_jobs (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id       uuid NOT NULL,
        org_id       uuid NOT NULL,
        kind         text NOT NULL CHECK (kind IN ('run', 'resume', 'export')),
        epoch        int NOT NULL DEFAULT 1,
        status       text NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'done', 'cancelled')),
        attempts     int NOT NULL DEFAULT 0,
        lease_until  timestamptz,
        UNIQUE (run_id, kind, epoch)
    )
    """,
]


class LangGraphEngine:
    name = "langgraph"

    def __init__(self, config: EngineConfig) -> None:
        self._config = config
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._client_app: async_sessionmaker[AsyncSession] | None = None
        self._running: dict[int, tuple[UUID, asyncio.Task[None]]] = {}
        self._steps: Steps | None = None
        self.concurrency = 16

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        assert self._sessions is not None
        return self._sessions

    async def open(self) -> None:
        self._engine = create_app_engine(self._config.engine_url, pool_size=5)
        self._sessions = create_sessionmaker(self._engine)
        async with self.sessions() as db, db.begin():
            await db.execute(text("SELECT pg_advisory_xact_lock(4243)"))
            for statement in SCHEMA:
                await db.execute(text(statement))

    async def close(self) -> None:
        for _, task in self._running.values():
            task.cancel()
        if self._engine is not None:
            await self._engine.dispose()

    async def _enqueue(self, org_id: UUID, run_id: UUID, kind: str, epoch: int = 1) -> None:
        async with self.sessions() as db, db.begin():
            await db.execute(
                text(
                    "INSERT INTO engine.lg_jobs (run_id, org_id, kind, epoch) "
                    "VALUES (:r, :o, :k, :e) ON CONFLICT (run_id, kind, epoch) DO NOTHING"
                ),
                {"r": run_id, "o": org_id, "k": kind, "e": epoch},
            )

    async def start(self, org_id: UUID, run_id: UUID) -> None:
        await self._enqueue(org_id, run_id, "run")

    async def signal_review(self, org_id: UUID, run_id: UUID, decision: str) -> None:
        await self._enqueue(org_id, run_id, "resume")

    async def retry_export(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        await self._enqueue(org_id, run_id, "export", epoch)

    async def cancel(self, org_id: UUID, run_id: UUID) -> None:
        async with self.sessions() as db, db.begin():
            await db.execute(
                text(
                    "UPDATE engine.lg_jobs SET status = 'cancelled' WHERE run_id = :r AND status <> 'done'"
                ),
                {"r": run_id},
            )
        if self._client_app is None:
            self._client_app = create_sessionmaker(
                create_app_engine(self._config.app_url, pool_size=1)
            )
        await Steps(self._client_app, llm_latency=0).mark_cancelled(org_id, run_id)

    # --- worker ---------------------------------------------------------------------------------

    async def _claim(self) -> tuple[int, UUID, UUID, str, int] | None:
        async with self.sessions() as db, db.begin():
            # One invocation per run at a time: skip runs that have a live job elsewhere.
            job = (
                await db.execute(
                    text(
                        "UPDATE engine.lg_jobs SET status = 'running', attempts = attempts + 1, "
                        "lease_until = clock_timestamp() + make_interval(secs => :lease) "
                        "WHERE id = (SELECT j.id FROM engine.lg_jobs j WHERE (j.status = 'queued' "
                        "OR (j.status = 'running' AND j.lease_until < clock_timestamp())) "
                        "AND j.attempts < 20 AND NOT EXISTS (SELECT 1 FROM engine.lg_jobs x WHERE x.run_id = j.run_id "
                        "AND x.id <> j.id AND x.status = 'running' "
                        "AND x.lease_until >= clock_timestamp()) "
                        "ORDER BY j.id FOR UPDATE SKIP LOCKED LIMIT 1) "
                        "RETURNING id, run_id, org_id, kind, epoch"
                    ),
                    {"lease": self._config.lease_seconds},
                )
            ).one_or_none()
        return None if job is None else (job.id, job.run_id, job.org_id, job.kind, job.epoch)

    async def _heartbeat(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if self._running:
                async with self.sessions() as db, db.begin():
                    await db.execute(
                        text(
                            "UPDATE engine.lg_jobs SET lease_until = clock_timestamp() + "
                            "make_interval(secs => :lease) WHERE id = ANY(:ids) AND status = 'running'"
                        ),
                        {"lease": self._config.lease_seconds, "ids": list(self._running)},
                    )
                    cancelled = (
                        (
                            await db.execute(
                                text(
                                    "SELECT id FROM engine.lg_jobs WHERE id = ANY(:ids) AND status = 'cancelled'"
                                ),
                                {"ids": list(self._running)},
                            )
                        )
                        .scalars()
                        .all()
                    )
                for job_id in cancelled:  # cancel the in-process invocation (H5)
                    entry = self._running.get(job_id)
                    if entry:
                        entry[1].cancel()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), min(0.5, self._config.lease_seconds / 3))

    async def _execute(
        self, job: tuple[int, UUID, UUID, str, int], run_graph: Any, retry_graph: Any
    ) -> None:
        job_id, run_id, org_id, kind, epoch = job
        config: dict[str, Any] = {"max_concurrency": 4}
        # A checkpoint with nothing scheduled (a fresh thread, a finished graph, or a kill that
        # left a checkpoint LangGraph does not continue from, seen in gate H1) is restarted
        # from the beginning: every step is idempotent, so that is safe.
        try:
            if kind == "export":
                config["configurable"] = {"thread_id": f"{run_id}:export:{epoch}"}
                snapshot = await retry_graph.aget_state(config)
                fresh_export = {"org": str(org_id), "run": str(run_id), "epoch": epoch}
                payload: Any = None if snapshot.next else fresh_export
                await retry_graph.ainvoke(payload, config, durability="sync")
                wanted = {"succeeded", "cancelled", "failed"}
            else:
                config["configurable"] = {"thread_id": str(run_id)}
                fresh = {"org": str(org_id), "run": str(run_id), "done": []}
                # A resume job may find the run not yet parked (its run job was killed before
                # the review): drive it to the interrupt first, then resume.
                for _ in range(3):
                    snapshot = await run_graph.aget_state(config)
                    if snapshot.interrupts:
                        if kind == "resume":
                            await run_graph.ainvoke(
                                Command(resume="approved"), config, durability="sync"
                            )
                        break
                    payload = None if snapshot.next else fresh
                    await run_graph.ainvoke(payload, config, durability="sync")
                    if kind != "resume":
                        break
                wanted = (
                    {"succeeded", "cancelled", "failed"}
                    if kind == "resume"
                    else {"waiting_review", "succeeded", "cancelled", "failed"}
                )
            # "ainvoke returned" does not prove progress (gate H1): check our own run row.
            final = "done"
            assert self._steps is not None
            try:
                status, *_ = await self._steps.run_info(org_id, run_id)
            except RunGoneError:
                status = "failed"
            if status not in wanted:
                final = "queued"  # try again (claiming caps attempts)
        except asyncio.CancelledError:
            final = "cancelled"
        async with self.sessions() as db, db.begin():
            await db.execute(
                text(
                    "UPDATE engine.lg_jobs SET status = :s, lease_until = NULL WHERE id = :j AND status = 'running'"
                ),
                {"s": final, "j": job_id},
            )

    async def run_worker(self, steps: Steps, stop: asyncio.Event) -> None:
        self._steps = steps
        url = self._config.engine_url + ("&" if "?" in self._config.engine_url else "?")
        url += "options=-csearch_path%3Dengine"
        async with AsyncConnectionPool(
            url,
            min_size=2,
            max_size=6,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        ) as pool:
            saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
            await saver.setup()
            run_builder, retry_builder = build_graphs(steps)
            run_graph = run_builder.compile(checkpointer=saver)
            retry_graph = retry_builder.compile(checkpointer=saver)
            heartbeat = asyncio.create_task(self._heartbeat(stop))
            while not stop.is_set():
                claimed = False
                while len(self._running) < self.concurrency:
                    job = await self._claim()
                    if job is None:
                        break
                    claimed = True
                    task = asyncio.create_task(self._execute(job, run_graph, retry_graph))
                    self._running[job[0]] = (job[1], task)
                    task.add_done_callback(lambda _t, j=job[0]: self._running.pop(j, None))
                if not claimed:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), 0.1)
            await heartbeat
