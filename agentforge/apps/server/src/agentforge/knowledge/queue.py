"""Job queues in the `queue` schema (ADR-12): `FOR UPDATE SKIP LOCKED` claims with leases.

At-least-once: a job whose lease expires (worker crashed) is claimed again. Every handler is
idempotent (ingestion replaces chunks in one transaction; answering overwrites its own row),
so a repeat is harmless. Queue rows hold IDs and status only, never tenant content.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

LEASE = timedelta(minutes=15)
MAX_ATTEMPTS = 5

QueueName = Literal["ingestion", "answer"]
# Fixed table and subject-column names, never user input.
_TABLES: dict[QueueName, tuple[str, str]] = {
    "ingestion": ("queue.ingestion_jobs", "document_id"),
    "answer": ("queue.answer_jobs", "answer_id"),
}


@dataclass(frozen=True, slots=True)
class Job:
    queue: QueueName
    id: int
    org_id: UUID
    subject_id: UUID  # the document or answer the job is about
    attempts: int


async def enqueue(
    db: AsyncSession, org_id: UUID, subject_id: UUID, *, queue: QueueName = "ingestion"
) -> None:
    """Call inside the transaction that creates or resets the subject row."""
    table, column = _TABLES[queue]
    await db.execute(
        text(f"INSERT INTO {table} (org_id, {column}) VALUES (:o, :s)"),  # noqa: S608
        {"o": org_id, "s": subject_id},
    )


async def claim(
    sessions: async_sessionmaker[AsyncSession], queue: QueueName = "ingestion"
) -> Job | None:
    table, column = _TABLES[queue]
    sql = (
        f"UPDATE {table} SET status = 'running', attempts = attempts + 1, "  # noqa: S608
        "lease_expires_at = now() + :lease, updated_at = now() "
        f"WHERE id = (SELECT id FROM {table} "
        "WHERE (status = 'queued' AND run_after <= now()) "
        "OR (status = 'running' AND lease_expires_at < now()) "
        "ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) "
        f"RETURNING id, org_id, {column} AS subject_id, attempts"
    )
    async with sessions() as db, db.begin():
        row = (await db.execute(text(sql), {"lease": LEASE})).one_or_none()
    if row is None:
        return None
    return Job(queue, row.id, row.org_id, row.subject_id, row.attempts)


async def finish(sessions: async_sessionmaker[AsyncSession], job: Job, *, ok: bool) -> None:
    table, _ = _TABLES[job.queue]
    async with sessions() as db, db.begin():
        await db.execute(
            text(f"UPDATE {table} SET status = :s, updated_at = now() WHERE id = :i"),  # noqa: S608
            {"s": "done" if ok else "failed", "i": job.id},
        )


async def retry_later(sessions: async_sessionmaker[AsyncSession], job: Job) -> bool:
    """Reschedule with exponential backoff. False when attempts are exhausted."""
    if job.attempts >= MAX_ATTEMPTS:
        await finish(sessions, job, ok=False)
        return False
    table, _ = _TABLES[job.queue]
    delay = timedelta(seconds=min(30 * 2 ** (job.attempts - 1), 600))
    sql = (
        f"UPDATE {table} SET status = 'queued', run_after = now() + :d, "  # noqa: S608
        "lease_expires_at = NULL, updated_at = now() WHERE id = :i"
    )
    async with sessions() as db, db.begin():
        await db.execute(text(sql), {"d": delay, "i": job.id})
    return True
