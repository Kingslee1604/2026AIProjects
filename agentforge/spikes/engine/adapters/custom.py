"""Candidate 3 (R1-23): a custom engine on Postgres.

engine.jobs holds one row per step execution (IDs and status only). Workers claim jobs with
FOR UPDATE SKIP LOCKED and a lease; a heartbeat extends leases while a step runs, so a killed
worker's jobs become claimable again after the lease expires. Completing a job and enqueuing
its successors happen in one transaction. The review step parks the run (no job is leased and
no coroutine waits); the signal enqueues the export. Cancel flips a flag that claiming checks.
"""

import asyncio
import contextlib
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agentforge.db.engine import create_app_engine, create_sessionmaker
from spikes.engine.harness.port import EngineConfig
from spikes.engine.harness.steps import RunGoneError, Steps

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS engine.runs (
        run_id           uuid PRIMARY KEY,
        org_id           uuid NOT NULL,
        max_parallelism  int NOT NULL DEFAULT 4,
        cancelled        bool NOT NULL DEFAULT false
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS engine.jobs (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        run_id       uuid NOT NULL REFERENCES engine.runs (run_id) ON DELETE CASCADE,
        org_id       uuid NOT NULL,
        kind         text NOT NULL CHECK (kind IN ('plan', 'item', 'review', 'export', 'finish')),
        idx          int NOT NULL DEFAULT -1,
        epoch        int NOT NULL DEFAULT 1,
        status       text NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'parked', 'done', 'failed')),
        attempts     int NOT NULL DEFAULT 0,
        lease_until  timestamptz,
        created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (run_id, kind, idx, epoch)
    )
    """,
    "CREATE INDEX IF NOT EXISTS jobs_ready ON engine.jobs (id) WHERE status IN ('queued', 'running')",
]

# A job is claimable when queued, or running with an expired lease (its worker died). Item
# jobs also need a free slot: the run row is locked while counting, so two workers can never
# both take the last slot.
_CANDIDATES = text(
    """
    SELECT j.id, j.run_id, j.kind FROM engine.jobs j JOIN engine.runs r USING (run_id)
    WHERE NOT r.cancelled AND j.attempts < 20
      AND (j.status = 'queued' OR (j.status = 'running' AND j.lease_until < clock_timestamp()))
      AND (j.kind <> 'item' OR (
        SELECT count(*) FROM engine.jobs x WHERE x.run_id = j.run_id AND x.kind = 'item'
        AND x.status = 'running' AND x.lease_until >= clock_timestamp()) < r.max_parallelism)
    ORDER BY j.id LIMIT 50
    """
)


class CustomEngine:
    name = "custom"

    def __init__(self, config: EngineConfig) -> None:
        self._config = config
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._steps: Steps | None = None
        self._running: dict[int, asyncio.Task[None]] = {}
        self._client_app: async_sessionmaker[AsyncSession] | None = None
        self.concurrency = 16  # jobs per worker process

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        assert self._sessions is not None
        return self._sessions

    async def open(self) -> None:
        self._engine = create_app_engine(self._config.engine_url, pool_size=5)
        self._sessions = create_sessionmaker(self._engine)
        async with self.sessions() as db, db.begin():
            await db.execute(text("SELECT pg_advisory_xact_lock(4242)"))  # one creator at a time
            for statement in SCHEMA:
                await db.execute(text(statement))

    async def close(self) -> None:
        for task in self._running.values():
            task.cancel()
        if self._engine is not None:
            await self._engine.dispose()

    # --- client side ---------------------------------------------------------------------------

    async def start(self, org_id: UUID, run_id: UUID) -> None:
        async with self.sessions() as db, db.begin():
            await db.execute(
                text(
                    "INSERT INTO engine.runs (run_id, org_id) VALUES (:r, :o) ON CONFLICT DO NOTHING"
                ),
                {"r": run_id, "o": org_id},
            )
            await self._enqueue(db, org_id, run_id, "plan")

    async def cancel(self, org_id: UUID, run_id: UUID) -> None:
        async with self.sessions() as db, db.begin():
            await db.execute(
                text("UPDATE engine.runs SET cancelled = true WHERE run_id = :r"), {"r": run_id}
            )
        await Steps(self._app_sessions(), llm_latency=0).mark_cancelled(org_id, run_id)

    async def signal_review(self, org_id: UUID, run_id: UUID, decision: str) -> None:
        async with self.sessions() as db, db.begin():
            parked = (
                await db.execute(
                    text(
                        "UPDATE engine.jobs SET status = 'done' WHERE run_id = :r "
                        "AND kind = 'review' AND status = 'parked' RETURNING epoch"
                    ),
                    {"r": run_id},
                )
            ).one_or_none()
            if parked is not None:
                await self._enqueue(db, org_id, run_id, "export", epoch=parked.epoch)

    async def retry_export(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        async with self.sessions() as db, db.begin():
            await self._enqueue(db, org_id, run_id, "export", epoch=epoch)

    def _app_sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._steps is not None:
            return self._steps._sessions
        if self._client_app is None:
            self._client_app = create_sessionmaker(
                create_app_engine(self._config.app_url, pool_size=1)
            )
        return self._client_app

    @staticmethod
    async def _enqueue(
        db: AsyncSession, org_id: UUID, run_id: UUID, kind: str, *, idx: int = -1, epoch: int = 1
    ) -> None:
        await db.execute(
            text(
                "INSERT INTO engine.jobs (run_id, org_id, kind, idx, epoch) "
                "SELECT :r, :o, :k, :i, :e WHERE EXISTS "
                "(SELECT 1 FROM engine.runs WHERE run_id = :r AND NOT cancelled) "
                "ON CONFLICT (run_id, kind, idx, epoch) DO NOTHING"
            ),
            {"r": run_id, "o": org_id, "k": kind, "i": idx, "e": epoch},
        )

    # --- worker side ---------------------------------------------------------------------------

    async def _claim(self) -> tuple[int, UUID, UUID, str, int, int] | None:
        async with self.sessions() as db, db.begin():
            for candidate in (await db.execute(_CANDIDATES)).all():
                if candidate.kind == "item":
                    limit = (
                        await db.execute(
                            text(
                                "SELECT max_parallelism FROM engine.runs WHERE run_id = :r "
                                "FOR UPDATE"
                            ),
                            {"r": candidate.run_id},
                        )
                    ).scalar_one()
                    busy = (
                        await db.execute(
                            text(
                                "SELECT count(*) FROM engine.jobs WHERE run_id = :r "
                                "AND kind = 'item' AND status = 'running' "
                                "AND lease_until >= clock_timestamp()"
                            ),
                            {"r": candidate.run_id},
                        )
                    ).scalar_one()
                    if busy >= limit:
                        continue
                job = (
                    await db.execute(
                        text(
                            "UPDATE engine.jobs SET status = 'running', attempts = attempts + 1, "
                            "lease_until = clock_timestamp() + make_interval(secs => :lease) "
                            "WHERE id = (SELECT id FROM engine.jobs WHERE id = :j AND "
                            "(status = 'queued' OR (status = 'running' AND "
                            "lease_until < clock_timestamp())) FOR UPDATE SKIP LOCKED) "
                            "RETURNING id, run_id, org_id, kind, idx, epoch"
                        ),
                        {"j": candidate.id, "lease": self._config.lease_seconds},
                    )
                ).one_or_none()
                if job is not None:
                    return job.id, job.run_id, job.org_id, job.kind, job.idx, job.epoch
        return None

    async def _heartbeat(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if self._running:
                async with self.sessions() as db, db.begin():
                    await db.execute(
                        text(
                            "UPDATE engine.jobs SET lease_until = clock_timestamp() + "
                            "make_interval(secs => :lease) WHERE id = ANY(:ids) AND status = 'running'"
                        ),
                        {"lease": self._config.lease_seconds, "ids": list(self._running)},
                    )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self._config.lease_seconds / 3)

    async def _execute(self, job: tuple[int, UUID, UUID, str, int, int]) -> None:
        job_id, run_id, org_id, kind, idx, epoch = job
        steps = self._steps
        assert steps is not None
        successors: list[tuple[str, int]] = []
        final = "done"
        try:
            if kind == "plan":
                if not await steps.begin(org_id, run_id):
                    final = "failed"
                else:
                    count = await steps.plan(org_id, run_id)
                    successors = [("item", i) for i in range(count)]
            elif kind == "item":
                await steps.process_item(org_id, run_id, idx)
                if await steps.items_remaining(org_id, run_id) == 0:
                    successors = [("review", -1)]
            elif kind == "review":
                await steps.enter_review(org_id, run_id)
                final = "parked"  # no lease, no coroutine: resumed by signal_review
            elif kind == "export":
                await steps.leave_review(org_id, run_id)
                await steps.export(org_id, run_id, epoch)
                successors = [("finish", -1)]
            elif kind == "finish":
                await steps.finish(org_id, run_id)
        except RunGoneError:
            final = "failed"
        async with self.sessions() as db, db.begin():
            await db.execute(
                text("UPDATE engine.jobs SET status = :s, lease_until = NULL WHERE id = :j"),
                {"s": final, "j": job_id},
            )
            for successor, successor_idx in successors:
                await self._enqueue(db, org_id, run_id, successor, idx=successor_idx, epoch=epoch)

    async def run_worker(self, steps: Steps, stop: asyncio.Event) -> None:
        self._steps = steps
        heartbeat = asyncio.create_task(self._heartbeat(stop))
        while not stop.is_set():
            claimed = False
            while len(self._running) < self.concurrency:
                job = await self._claim()
                if job is None:
                    break
                claimed = True
                task = asyncio.create_task(self._execute(job))
                self._running[job[0]] = task
                task.add_done_callback(lambda _t, j=job[0]: self._running.pop(j, None))
            if not claimed:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), 0.1)
            else:
                await asyncio.sleep(0)
        await heartbeat
