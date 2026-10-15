"""Review service: decisions are recorded per item, every decision is audited, and completing a
batch signals the run engine exactly once (one conditional UPDATE decides who completes it)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge import audit
from agentforge.core.logging import get_logger
from agentforge.db.tenancy import TenantContext, tenant_session
from agentforge.runs.events import emit
from agentforge.runs.service import RunService

log = get_logger("agentforge.reviews")

Decision = Literal["approved", "edited", "rejected"]
MAX_ANSWER = 4000
MAX_REASON = 1000


class NotFoundError(Exception):
    pass


class ReviewError(ValueError):
    """The request cannot be applied; the message is safe to show to the user."""


@dataclass(frozen=True, slots=True)
class Reviewer:
    user_id: UUID
    email: str
    ip: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewItem:
    item_index: int
    auto_accept_candidate: bool
    decision: str | None
    edited_answer: str | None
    reason: str | None
    decided_by: UUID | None
    decided_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReviewBatch:
    id: UUID
    run_id: UUID
    epoch: int
    status: str
    items: list[ReviewItem]
    created_at: datetime
    completed_at: datetime | None

    @property
    def undecided(self) -> int:
        return sum(1 for i in self.items if i.decision is None)


class ReviewService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], runs: RunService) -> None:
        self._sessions = sessions
        self._runs = runs

    def _tx(self, org_id: UUID, user_id: UUID | None = None):
        return tenant_session(self._sessions, TenantContext(org_id, user_id=user_id))

    async def _batch_row(self, db: AsyncSession, run_id: UUID, *, lock: bool = False) -> Any:
        sql = (
            "SELECT id, run_id, epoch, status, created_at, completed_at FROM app.review_batches "
            "WHERE run_id = :r ORDER BY created_at DESC LIMIT 1"
        )
        row = (
            await db.execute(text(f"{sql} FOR UPDATE" if lock else sql), {"r": run_id})
        ).one_or_none()
        if row is None:
            raise NotFoundError("this run has no review")
        return row

    async def get(self, org_id: UUID, run_id: UUID) -> ReviewBatch:
        async with self._tx(org_id) as db:
            batch = await self._batch_row(db, run_id)
            items = (
                await db.execute(
                    text(
                        "SELECT item_index, auto_accept_candidate, decision, edited_answer, "
                        "reason, decided_by, decided_at FROM app.review_items "
                        "WHERE batch_id = :b ORDER BY item_index"
                    ),
                    {"b": batch.id},
                )
            ).all()
        return ReviewBatch(
            id=batch.id,
            run_id=batch.run_id,
            epoch=batch.epoch,
            status=batch.status,
            items=[ReviewItem(**row._mapping) for row in items],  # pyright: ignore[reportPrivateUsage]
            created_at=batch.created_at,
            completed_at=batch.completed_at,
        )

    async def decide(
        self,
        org_id: UUID,
        run_id: UUID,
        item_index: int,
        decision: Decision,
        reviewer: Reviewer,
        *,
        edited_answer: str | None = None,
        reason: str | None = None,
    ) -> ReviewItem:
        edited = (edited_answer or "").strip() or None
        why = (reason or "").strip() or None
        if decision == "edited" and edited is None:
            raise ReviewError("An edited answer cannot be empty.")
        if decision == "rejected" and why is None:
            raise ReviewError("Say why the answer is rejected.")
        if edited is not None and len(edited) > MAX_ANSWER:
            raise ReviewError(f"Answers are limited to {MAX_ANSWER} characters.")
        if why is not None and len(why) > MAX_REASON:
            raise ReviewError(f"Reasons are limited to {MAX_REASON} characters.")
        async with self._tx(org_id, reviewer.user_id) as db:
            batch = await self._batch_row(db, run_id, lock=True)
            if batch.status != "open":
                raise ReviewError("This review is already complete.")
            row = (
                await db.execute(
                    text(
                        "UPDATE app.review_items SET decision = :d, edited_answer = :e, "
                        "reason = :why, decided_by = :u, decided_at = now() "
                        "WHERE batch_id = :b AND item_index = :i "
                        "RETURNING item_index, auto_accept_candidate, decision, edited_answer, "
                        "reason, decided_by, decided_at"
                    ),
                    {
                        "d": decision,
                        "e": edited if decision == "edited" else None,
                        "why": why,
                        "u": reviewer.user_id,
                        "b": batch.id,
                        "i": item_index,
                    },
                )
            ).one_or_none()
            if row is None:
                raise NotFoundError("this item is not part of the review")
            await emit(
                db,
                org_id,
                run_id,
                "review_decided",
                node_id="review",
                item_index=item_index,
                data={"decision": decision},
            )
        await self._audit(org_id, reviewer, "review.decide", run_id, item_index, decision=decision)
        return ReviewItem(**row._mapping)  # pyright: ignore[reportPrivateUsage]

    async def approve_supported(self, org_id: UUID, run_id: UUID, reviewer: Reviewer) -> int:
        """Approve every undecided bulk-approval candidate (verified, high confidence)."""
        async with self._tx(org_id, reviewer.user_id) as db:
            batch = await self._batch_row(db, run_id, lock=True)
            if batch.status != "open":
                raise ReviewError("This review is already complete.")
            approved = (
                await db.execute(
                    text(
                        "UPDATE app.review_items SET decision = 'approved', decided_by = :u, "
                        "decided_at = now() WHERE batch_id = :b AND decision IS NULL "
                        "AND auto_accept_candidate RETURNING item_index"
                    ),
                    {"u": reviewer.user_id, "b": batch.id},
                )
            ).all()
            if approved:
                await emit(
                    db,
                    org_id,
                    run_id,
                    "review_bulk_approved",
                    node_id="review",
                    data={"count": len(approved)},
                )
        await self._audit(
            org_id, reviewer, "review.bulk_approve", run_id, None, count=len(approved)
        )
        return len(approved)

    async def complete(self, org_id: UUID, run_id: UUID, reviewer: Reviewer) -> ReviewBatch:
        """Close the review and let the run export. Only the first of concurrent requests
        completes the batch and signals the engine; the others see it already complete."""
        async with self._tx(org_id, reviewer.user_id) as db:
            batch = await self._batch_row(db, run_id, lock=True)
            if batch.status != "open":
                raise ReviewError("This review is already complete.")
            undecided = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM app.review_items "
                        "WHERE batch_id = :b AND decision IS NULL"
                    ),
                    {"b": batch.id},
                )
            ).scalar_one()
            if undecided:
                raise ReviewError(f"{undecided} answer(s) still need a decision.")
            await db.execute(
                text(
                    "UPDATE app.review_batches SET status = 'completed', completed_at = now(), "
                    "completed_by = :u WHERE id = :b AND status = 'open'"
                ),
                {"u": reviewer.user_id, "b": batch.id},
            )
            await emit(db, org_id, run_id, "review_completed", node_id="review")
        # After the commit: the batch is closed exactly once, so the signal is sent once.
        await self._runs.signal_review(org_id, run_id)
        await self._audit(org_id, reviewer, "review.complete", run_id, None)
        log.info("review_completed", run_id=str(run_id))
        return await self.get(org_id, run_id)

    async def _audit(
        self,
        org_id: UUID,
        reviewer: Reviewer,
        action: str,
        run_id: UUID,
        item_index: int | None,
        **details: str | int | bool | None,
    ) -> None:
        target = f"run:{run_id}" + (f"#item:{item_index}" if item_index is not None else "")
        await audit.record(
            self._sessions,
            audit.AuditEvent(
                action,
                "success",
                org_id=org_id,
                actor_user_id=reviewer.user_id,
                actor_email=reviewer.email,
                target=target,
                ip=reviewer.ip,
                details=dict(details),
            ),
        )
