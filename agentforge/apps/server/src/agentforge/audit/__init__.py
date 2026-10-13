"""Append-only audit trail of security-relevant actions (FR-15, security baseline S13).

Events never contain secrets or document content. Writes use their own transaction, so an
audit row is recorded even when the audited action itself is rejected.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.core.telemetry import current_trace_id
from agentforge.db.tenancy import TenantContext, tenant_session

Outcome = Literal["success", "failure", "denied"]

_INSERT = text(
    """
    INSERT INTO app.audit_log
      (org_id, actor_user_id, actor_email, action, target, outcome, ip, trace_id, details)
    VALUES (:org_id, :actor_user_id, :actor_email, :action, :target, :outcome,
            CAST(:ip AS inet), :trace_id, CAST(:details AS jsonb))
    """
)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    action: str  # dotted, e.g. "auth.login", "user.disable"
    outcome: Outcome
    org_id: UUID | None = None
    actor_user_id: UUID | None = None
    actor_email: str | None = None
    target: str | None = None
    ip: str | None = None
    details: dict[str, str | int | bool | None] = field(default_factory=lambda: {})


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int
    at: datetime
    actor_email: str | None
    action: str
    target: str | None
    outcome: str
    ip: str | None
    trace_id: str | None
    details: dict[str, object]


async def list_entries(
    sessions: async_sessionmaker[AsyncSession], org_id: UUID, *, limit: int = 100
) -> list[AuditEntry]:
    """Newest first; RLS restricts the result to `org_id`."""
    async with tenant_session(sessions, TenantContext(org_id)) as db:
        rows = await db.execute(
            text(
                "SELECT id, at, actor_email, action, target, outcome, host(ip) AS ip, trace_id, "
                "details FROM app.audit_log ORDER BY id DESC LIMIT :limit"
            ),
            {"limit": limit},
        )
        return [
            AuditEntry(
                id=m["id"],
                at=m["at"],
                actor_email=m["actor_email"],
                action=m["action"],
                target=m["target"],
                outcome=m["outcome"],
                ip=m["ip"],
                trace_id=m["trace_id"],
                details=m["details"],
            )
            for m in rows.mappings()
        ]


async def record(sessions: async_sessionmaker[AsyncSession], event: AuditEvent) -> None:
    params = {
        "org_id": event.org_id,
        "actor_user_id": event.actor_user_id,
        "actor_email": event.actor_email,
        "action": event.action,
        "target": event.target,
        "outcome": event.outcome,
        "ip": event.ip,
        "trace_id": current_trace_id(),
        "details": json.dumps(event.details),
    }
    if event.org_id is None:
        async with sessions() as db, db.begin():
            await db.execute(_INSERT, params)
    else:
        async with tenant_session(sessions, TenantContext(event.org_id)) as db:
            await db.execute(_INSERT, params)
