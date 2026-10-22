"""/v1/api-keys: admins create (the secret is shown once), list, and revoke workspace API keys."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from agentforge.api.deps import client_ip, get_api_keys, require, session_principal
from agentforge.auth.api_keys import (
    MAX_EXPIRY_DAYS,
    ApiKey,
    ApiKeyError,
    ApiKeyService,
    NotFoundError,
)
from agentforge.auth.rbac import Permission, Principal

router = APIRouter(prefix="/v1/api-keys", tags=["api keys"])

Scope = Literal["api:read", "api:write", "mcp:use"]
KeysDep = Annotated[ApiKeyService, Depends(get_api_keys)]


async def _admin(
    principal: Annotated[Principal, Depends(session_principal)],
    _: Annotated[Principal, Depends(require(Permission.API_KEYS_MANAGE))],
) -> Principal:
    """A signed-in admin. (A key could not pass `require` either: no scope grants this.)"""
    return principal


AdminDep = Annotated[Principal, Depends(_admin)]


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Scope] = Field(min_length=1, max_length=3)
    expires_in_days: int = Field(default=90, ge=1, le=MAX_EXPIRY_DAYS)


class ApiKeyOut(BaseModel):
    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    created_by_email: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class NewApiKeyOut(BaseModel):
    key: str = Field(description="The API key. Shown only in this response; store it now.")
    api_key: ApiKeyOut


def _out(key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(**asdict(key))


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(principal: AdminDep, keys: KeysDep) -> list[ApiKeyOut]:
    """This organization's API keys, newest first. Secrets are never returned."""
    return [_out(k) for k in await keys.list_keys(principal.org_id)]


@router.post("", response_model=NewApiKeyOut, status_code=201)
async def create_api_key(
    body: ApiKeyIn, request: Request, principal: AdminDep, keys: KeysDep
) -> NewApiKeyOut:
    """Create a key. Send it as `Authorization: Bearer <key>`; it acts for you, limited to its
    scopes."""
    try:
        new = await keys.create(
            principal,
            name=body.name,
            scopes=body.scopes,
            expires_in_days=body.expires_in_days,
            ip=client_ip(request),
        )
    except ApiKeyError as error:
        raise HTTPException(400, str(error)) from None
    return NewApiKeyOut(key=new.secret, api_key=_out(new.key))


@router.delete("/{key_id}", response_model=ApiKeyOut)
async def revoke_api_key(
    key_id: UUID, request: Request, principal: AdminDep, keys: KeysDep
) -> ApiKeyOut:
    """Revoke a key: it stops working at once. Revoking twice is harmless."""
    try:
        return _out(await keys.revoke(principal, key_id, ip=client_ip(request)))
    except NotFoundError:
        raise HTTPException(404, "API key not found") from None
