"""The ingestion queue (ADR-12): `FOR UPDATE SKIP LOCKED` claims with leases.

At-least-once: a job whose lease expires (worker crashed) is claimed again. Processing is
idempotent (chunks are replaced in one transaction), so a repeat is harmless.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

LEASE = timedelta(minutes=15)
MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class IngestionJob:
    id: int
    org_id: UUID
    document_id: UUID
    attempts: int


async def enqueue(db: AsyncSession, org_id: UUID, document_id: UUID) -> None:
    """Call inside the transaction that creates or resets the document."""
    await db.execute(
        text("INSERT INTO queue.ingestion_jobs (org_id, document_id) VALUES (:o, :d)"),
        {"o": org_id, "d": document_id},
    )


async def claim(sessions: async_sessionmaker[AsyncSession]) -> IngestionJob | None:
    async with sessions() as db, db.begin():
        row = (
            await db.execute(
                text(
                    """
                    UPDATE queue.ingestion_jobs
                    SET status = 'running', attempts = attempts + 1,
                        lease_expires_at = now() + :lease, updated_at = now()
                    WHERE id = (
                        SELECT id FROM queue.ingestion_jobs
                        WHERE (status = 'queued' AND run_after <= now())
                           OR (status = 'running' AND lease_expires_at < now())
                        ORDER BY id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id, org_id, document_id, attempts
                    """
                ),
                {"lease": LEASE},
            )
        ).one_or_none()
    return IngestionJob(row.id, row.org_id, row.document_id, row.attempts) if row else None


async def finish(
    sessions: async_sessionmaker[AsyncSession], job: IngestionJob, *, ok: bool
) -> None:
    async with sessions() as db, db.begin():
        await db.execute(
            text("UPDATE queue.ingestion_jobs SET status = :s, updated_at = now() WHERE id = :i"),
            {"s": "done" if ok else "failed", "i": job.id},
        )


async def retry_later(sessions: async_sessionmaker[AsyncSession], job: IngestionJob) -> bool:
    """Reschedule with exponential backoff. False when attempts are exhausted."""
    if job.attempts >= MAX_ATTEMPTS:
        await finish(sessions, job, ok=False)
        return False
    delay = timedelta(seconds=min(30 * 2 ** (job.attempts - 1), 600))
    async with sessions() as db, db.begin():
        await db.execute(
            text(
                "UPDATE queue.ingestion_jobs SET status = 'queued', run_after = now() + :d, "
                "lease_expires_at = NULL, updated_at = now() WHERE id = :i"
            ),
            {"d": delay, "i": job.id},
        )
    return True
