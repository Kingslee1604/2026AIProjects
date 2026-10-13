"""/v1/admin: organization administration (Admin role only)."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

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


@router.get("/users", response_model=list[MemberOut])
async def get_users(
    request: Request,
    principal: Annotated[Principal, Depends(require(Permission.MEMBERS_MANAGE))],
) -> list[MemberOut]:
    users = await list_users(request.app.state.sessions, principal.org_id)
    return [MemberOut(**asdict(u)) for u in users]
