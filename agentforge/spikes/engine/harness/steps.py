"""The mini-flow's step library, identical for every engine (architecture §7.4).

Rules the engines rely on:
- Each step opens its own tenant transaction and writes content to `conformance.*` only.
- Steps take and return IDs and small integers, never content (gate H6).
- Steps are idempotent: a crash retry finds the committed work and does not redo it; in
  particular a completed item never calls the model again (gate H1).
- Each completed step emits exactly one run event, in the same transaction (gate H8).
"""

import asyncio
import os
import uuid
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.db.tenancy import TenantContext, tenant_session

# Written into every item result and artifact. Gate H6 searches engine-owned tables for it.
CONTENT_MARKER = "TENANT-CONTENT-7f3a9c"


class RunGoneError(Exception):
    """The run row does not exist (or belongs to another tenant)."""


async def _event(
    db: AsyncSession,
    org_id: UUID,
    run_id: UUID,
    type_: str,
    *,
    node: str | None = None,
    idx: int | None = None,
    epoch: int | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO conformance.run_events (org_id, run_id, type, node, idx, epoch) "
            "VALUES (:o, :r, :t, :n, :i, :e)"
        ),
        {"o": org_id, "r": run_id, "t": type_, "n": node, "i": idx, "e": epoch},
    )


class Steps:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, llm_latency: float) -> None:
        self._sessions = sessions
        self.llm_latency = llm_latency

    def _tx(self, org_id: UUID):
        return tenant_session(self._sessions, TenantContext(org_id))

    async def run_info(self, org_id: UUID, run_id: UUID) -> tuple[str, int, int, int]:
        """(status, epoch, item_count, max_parallelism)."""
        async with self._tx(org_id) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT status, epoch, item_count, max_parallelism "
                        "FROM conformance.runs WHERE id = :r"
                    ),
                    {"r": run_id},
                )
            ).one_or_none()
        if row is None:
            raise RunGoneError(str(run_id))
        return row.status, row.epoch, row.item_count, row.max_parallelism

    async def begin(self, org_id: UUID, run_id: UUID) -> bool:
        """queued -> running. False when the run must not proceed (cancelled or finished)."""
        async with self._tx(org_id) as db:
            row = (
                await db.execute(
                    text("SELECT status FROM conformance.runs WHERE id = :r FOR UPDATE"),
                    {"r": run_id},
                )
            ).one_or_none()
            if row is None:
                raise RunGoneError(str(run_id))
            if row.status == "queued":
                await db.execute(
                    text(
                        "UPDATE conformance.runs SET status = 'running', "
                        "updated_at = clock_timestamp() WHERE id = :r"
                    ),
                    {"r": run_id},
                )
                await _event(db, org_id, run_id, "run_started")
                return True
            return row.status in {"running", "waiting_review"}

    async def plan(self, org_id: UUID, run_id: UUID) -> int:
        """Create the run's items. Returns the item count."""
        async with self._tx(org_id) as db:
            count = (
                await db.execute(
                    text("SELECT item_count FROM conformance.runs WHERE id = :r FOR UPDATE"),
                    {"r": run_id},
                )
            ).scalar_one_or_none()
            if count is None:
                raise RunGoneError(str(run_id))
            inserted = (
                await db.execute(
                    text(
                        "INSERT INTO conformance.run_items (run_id, idx, org_id) "
                        "SELECT :r, i, :o FROM generate_series(0, :n - 1) AS i "
                        "ON CONFLICT DO NOTHING RETURNING idx"
                    ),
                    {"r": run_id, "o": org_id, "n": count},
                )
            ).all()
            if inserted:
                await _event(db, org_id, run_id, "plan_done", node="plan")
        return int(count)

    async def process_item(self, org_id: UUID, run_id: UUID, idx: int) -> int:
        """Call the (fake) model for one item and store its result. Returns idx."""
        async with self._tx(org_id) as db:
            status = (
                await db.execute(
                    text("SELECT status FROM conformance.run_items WHERE run_id = :r AND idx = :i"),
                    {"r": run_id, "i": idx},
                )
            ).scalar_one_or_none()
        if status is None:
            raise RunGoneError(f"{run_id}/{idx}")
        if status == "done":
            return idx  # crash retry after commit: never call the model again

        async with self._tx(org_id) as db:  # committed first: survives a kill mid-call
            call_id = (
                await db.execute(
                    text(
                        "INSERT INTO conformance.llm_log (org_id, run_id, idx, worker_pid) "
                        "VALUES (:o, :r, :i, :p) RETURNING id"
                    ),
                    {"o": org_id, "r": run_id, "i": idx, "p": os.getpid()},
                )
            ).scalar_one()
        await asyncio.sleep(self.llm_latency)
        async with self._tx(org_id) as db:
            await db.execute(
                text(
                    "UPDATE conformance.llm_log SET finished_at = clock_timestamp() WHERE id = :c"
                ),
                {"c": call_id},
            )
            done = (
                await db.execute(
                    text(
                        "UPDATE conformance.run_items SET status = 'done', result = :res, "
                        "llm_call_id = :c, completed_at = clock_timestamp() "
                        "WHERE run_id = :r AND idx = :i AND status <> 'done' RETURNING idx"
                    ),
                    {
                        "res": f"{CONTENT_MARKER} answer for item {idx}",
                        "c": call_id,
                        "r": run_id,
                        "i": idx,
                    },
                )
            ).one_or_none()
            if done is not None:
                await _event(db, org_id, run_id, "item_done", node="item", idx=idx)
        return idx

    async def items_remaining(self, org_id: UUID, run_id: UUID) -> int:
        async with self._tx(org_id) as db:
            return int(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM conformance.run_items "
                            "WHERE run_id = :r AND status <> 'done'"
                        ),
                        {"r": run_id},
                    )
                ).scalar_one()
            )

    async def enter_review(self, org_id: UUID, run_id: UUID) -> None:
        async with self._tx(org_id) as db:
            moved = (
                await db.execute(
                    text(
                        "UPDATE conformance.runs SET status = 'waiting_review', "
                        "updated_at = clock_timestamp() "
                        "WHERE id = :r AND status = 'running' AND review_decision IS NULL "
                        "RETURNING id"
                    ),
                    {"r": run_id},
                )
            ).one_or_none()
            if moved is not None:
                await _event(db, org_id, run_id, "review_waiting", node="review")

    async def review_decision(self, org_id: UUID, run_id: UUID) -> str | None:
        async with self._tx(org_id) as db:
            return (
                await db.execute(
                    text("SELECT review_decision FROM conformance.runs WHERE id = :r"),
                    {"r": run_id},
                )
            ).scalar_one_or_none()

    async def leave_review(self, org_id: UUID, run_id: UUID) -> None:
        async with self._tx(org_id) as db:
            moved = (
                await db.execute(
                    text(
                        "UPDATE conformance.runs SET status = 'running', "
                        "updated_at = clock_timestamp() "
                        "WHERE id = :r AND status = 'waiting_review' RETURNING id"
                    ),
                    {"r": run_id},
                )
            ).one_or_none()
            if moved is not None:
                await _event(db, org_id, run_id, "review_done", node="review")

    async def export(self, org_id: UUID, run_id: UUID, epoch: int) -> str:
        """The side effect: one artifact per (run, node, epoch), whatever retries happen."""
        artifact_id = uuid.uuid5(uuid.NAMESPACE_URL, f"export/{run_id}/{epoch}")
        async with self._tx(org_id) as db:
            created = (
                await db.execute(
                    text(
                        "INSERT INTO conformance.artifacts (id, org_id, run_id, node, epoch, "
                        "content) VALUES (:a, :o, :r, 'export', :e, :c) "
                        "ON CONFLICT (run_id, node, epoch) DO NOTHING RETURNING id"
                    ),
                    {
                        "a": artifact_id,
                        "o": org_id,
                        "r": run_id,
                        "e": epoch,
                        "c": f"{CONTENT_MARKER} export of run {run_id} epoch {epoch}",
                    },
                )
            ).one_or_none()
            await db.execute(
                text(
                    "INSERT INTO conformance.export_log (org_id, run_id, epoch, created) "
                    "VALUES (:o, :r, :e, :c)"
                ),
                {"o": org_id, "r": run_id, "e": epoch, "c": created is not None},
            )
            if created is not None:
                await _event(db, org_id, run_id, "export_done", node="export", epoch=epoch)
        return str(artifact_id)

    async def finish(self, org_id: UUID, run_id: UUID) -> None:
        async with self._tx(org_id) as db:
            moved = (
                await db.execute(
                    text(
                        "UPDATE conformance.runs SET status = 'succeeded', "
                        "updated_at = clock_timestamp() "
                        "WHERE id = :r AND status = 'running' RETURNING epoch"
                    ),
                    {"r": run_id},
                )
            ).one_or_none()
            if moved is not None:
                await _event(db, org_id, run_id, "run_succeeded", epoch=moved.epoch)

    async def mark_cancelled(self, org_id: UUID, run_id: UUID) -> None:
        async with self._tx(org_id) as db:
            moved = (
                await db.execute(
                    text(
                        "UPDATE conformance.runs SET status = 'cancelled', "
                        "cancelled_at = clock_timestamp(), updated_at = clock_timestamp() "
                        "WHERE id = :r AND status IN ('queued', 'running', 'waiting_review') "
                        "RETURNING id"
                    ),
                    {"r": run_id},
                )
            ).one_or_none()
            if moved is not None:
                await _event(db, org_id, run_id, "run_cancelled")

    async def stale_queued_runs(self, older_than_seconds: float) -> list[tuple[UUID, UUID]]:
        """(org_id, run_id) of runs created but never started (gate H7 reconciler)."""
        async with self._sessions() as db, db.begin():
            rows = await db.execute(
                text(
                    "SELECT org_id, run_id FROM conformance.stale_queued_runs(make_interval(secs => :s))"
                ),
                {"s": older_than_seconds},
            )
            return [(r.org_id, r.run_id) for r in rows]
