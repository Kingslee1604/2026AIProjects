"""/v1/admin: organization administration (Admin role only)."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from agentforge import audit
from agentforge.api.deps import require
from agentforge.auth.rbac import Permission, Principal
from agentforge.auth.users import list_users

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class MemberOut(BaseModel):
    id: UUID
    email: str
    role: str
    can_review: bool
    disabled: bool


class AuditEntryOut(BaseModel):
    id: int
    at: datetime
    actor_email: str | None
    action: str
    target: str | None
    outcome: str
    ip: str | None
    trace_id: str | None
    details: dict[str, object]


@router.get("/audit", response_model=list[AuditEntryOut])
async def get_audit_log(
    request: Request,
    principal: Annotated[Principal, Depends(require(Permission.AUDIT_READ))],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEntryOut]:
    entries = await audit.list_entries(request.app.state.sessions, principal.org_id, limit=limit)
    return [AuditEntryOut(**asdict(e)) for e in entries]


@router.get("/users", response_model=list[MemberOut])
async def get_users(
    request: Request,
    principal: Annotated[Principal, Depends(require(Permission.MEMBERS_MANAGE))],
) -> list[MemberOut]:
    users = await list_users(request.app.state.sessions, principal.org_id)
    return [MemberOut(**asdict(u)) for u in users]
