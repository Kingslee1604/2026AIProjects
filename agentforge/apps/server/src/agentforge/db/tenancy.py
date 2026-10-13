"""Tenant context and tenant-scoped transactions (architecture §13.2).

The context is applied with set_config(..., is_local => true), the parameterizable form of
SET LOCAL: it lasts for one transaction only, so a pooled connection never carries one
tenant's context into another tenant's request.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_SET_CONTEXT = text(
    "SELECT set_config('app.org_id', :org_id, true), "
    "set_config('app.workspace_id', :workspace_id, true), "
    "set_config('app.user_id', :user_id, true)"
)


@dataclass(frozen=True, slots=True)
class TenantContext:
    org_id: UUID
    workspace_id: UUID | None = None
    user_id: UUID | None = None


@asynccontextmanager
async def _scoped(
    sessions: async_sessionmaker[AsyncSession],
    *,
    org_id: UUID | None,
    workspace_id: UUID | None = None,
    user_id: UUID | None = None,
) -> AsyncGenerator[AsyncSession]:
    async with sessions() as session, session.begin():
        await session.execute(
            _SET_CONTEXT,
            {
                "org_id": str(org_id) if org_id else "",
                "workspace_id": str(workspace_id) if workspace_id else "",
                "user_id": str(user_id) if user_id else "",
            },
        )
        yield session


@asynccontextmanager
async def tenant_session(
    sessions: async_sessionmaker[AsyncSession], context: TenantContext
) -> AsyncGenerator[AsyncSession]:
    """One transaction in which Postgres RLS sees `context.org_id` as the current tenant."""
    async with _scoped(
        sessions,
        org_id=context.org_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
    ) as session:
        yield session


@asynccontextmanager
async def user_session(
    sessions: async_sessionmaker[AsyncSession], user_id: UUID
) -> AsyncGenerator[AsyncSession]:
    """One transaction with only a user identity (no tenant): sees that user's memberships."""
    async with _scoped(sessions, org_id=None, user_id=user_id) as session:
        yield session
