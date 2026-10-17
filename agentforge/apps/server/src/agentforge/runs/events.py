"""Run events: the trace source for the UI (architecture §7.6).

Writing: `emit` inserts a row inside the caller's transaction; a trigger sends
pg_notify('run_events', '<run_id>:<seq>'), IDs only.

Reading live: one EventHub per API process LISTENs and wakes the streams of that run. Streams
always re-read from the table under RLS, so a lost notification only delays an update (each
stream also re-checks every few seconds) and content never travels through NOTIFY.
"""

import asyncio
import contextlib
import json
from collections import defaultdict
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.logging import get_logger
from agentforge.core.telemetry import current_trace_id
from agentforge.db.tenancy import TenantContext, tenant_session

log = get_logger("agentforge.runs")
CHANNEL = "run_events"


@dataclass(frozen=True, slots=True)
class RunEvent:
    seq: int
    run_id: UUID
    type: str
    node_id: str | None
    item_index: int | None
    epoch: int | None
    step_id: UUID | None
    data: dict[str, Any]
    created_at: datetime


async def emit(
    db: AsyncSession,
    org_id: UUID,
    run_id: UUID,
    type_: str,
    *,
    node_id: str | None = None,
    item_index: int | None = None,
    epoch: int | None = None,
    step_id: UUID | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Record an event in the caller's transaction (so it commits with the work it reports)."""
    await db.execute(
        text(
            "INSERT INTO app.run_events (org_id, run_id, type, node_id, item_index, epoch, "
            "step_id, data, trace_id) VALUES (:o, :r, :t, :n, :i, :e, :s, CAST(:d AS jsonb), :tr)"
        ),
        {
            "o": org_id,
            "r": run_id,
            "t": type_,
            "n": node_id,
            "i": item_index,
            "e": epoch,
            "s": step_id,
            "d": json.dumps(data or {}),
            "tr": current_trace_id(),
        },
    )


async def events_after(
    sessions: async_sessionmaker[AsyncSession],
    org_id: UUID,
    run_id: UUID,
    after_seq: int,
    limit: int = 500,
) -> list[RunEvent]:
    async with tenant_session(sessions, TenantContext(org_id)) as db:
        rows = await db.execute(
            text(
                "SELECT seq, run_id, type, node_id, item_index, epoch, step_id, data, created_at "
                "FROM app.run_events WHERE run_id = :r AND seq > :s ORDER BY seq LIMIT :n"
            ),
            {"r": run_id, "s": after_seq, "n": limit},
        )
        return [
            RunEvent(
                r.seq,
                r.run_id,
                r.type,
                r.node_id,
                r.item_index,
                r.epoch,
                r.step_id,
                dict(r.data),
                r.created_at,
            )
            for r in rows
        ]


class EventHub:
    """Fans pg_notify wake-ups out to the live streams of each run in this process."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._waiters: dict[UUID, set[asyncio.Event]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    @contextlib.contextmanager
    def subscribe(self, run_id: UUID) -> Generator[asyncio.Event]:
        event = asyncio.Event()
        self._waiters[run_id].add(event)
        try:
            yield event
        finally:
            self._waiters[run_id].discard(event)
            if not self._waiters[run_id]:
                del self._waiters[run_id]

    def _wake(self, payload: str) -> None:
        run_part, _, _ = payload.partition(":")
        try:
            run_id = UUID(run_part)
        except ValueError:
            return
        for event in self._waiters.get(run_id, ()):
            event.set()

    async def _listen(self) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self._url, autocommit=True
                ) as connection:
                    await connection.execute(f"LISTEN {CHANNEL}")
                    async for notification in connection.notifies():
                        self._wake(notification.payload)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # reconnect; streams keep polling meanwhile
                log.warning("run_event_listener_lost", error=error.__class__.__name__)
                await asyncio.sleep(2)
