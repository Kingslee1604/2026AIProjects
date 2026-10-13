"""User administration (no public sign-up in Release 1): used by `agentforge user ...`."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.auth.passwords import hash_password
from agentforge.auth.rbac import Role
from agentforge.auth.service import normalize_email
from agentforge.db.tenancy import TenantContext, tenant_session


class UserExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class UserSummary:
    id: UUID
    email: str
    role: str
    can_review: bool
    disabled: bool


async def create_user(
    sessions: async_sessionmaker[AsyncSession],
    *,
    org_id: UUID,
    email: str,
    password: str,
    role: Role,
    can_review: bool = False,
) -> UUID:
    email = normalize_email(email)
    password_hash = hash_password(password)
    try:
        async with tenant_session(sessions, TenantContext(org_id)) as db:
            user_id: UUID = (
                await db.execute(
                    text(
                        "INSERT INTO auth.users (email, password_hash) VALUES (:e, :h) RETURNING id"
                    ),
                    {"e": email, "h": password_hash},
                )
            ).scalar_one()
            await db.execute(
                text(
                    "INSERT INTO app.memberships (org_id, user_id, role, can_review) "
                    "VALUES (:o, :u, :r, :c)"
                ),
                {"o": org_id, "u": user_id, "r": role.value, "c": can_review},
            )
    except IntegrityError as error:
        raise UserExistsError(email) from error
    return user_id


async def _user_id(db: AsyncSession, email: str) -> UUID:
    user_id = (
        await db.execute(
            text("SELECT id FROM auth.users WHERE email = :e"), {"e": normalize_email(email)}
        )
    ).scalar_one_or_none()
    if user_id is None:
        raise UserNotFoundError(email)
    return user_id


async def _revoke_sessions(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(
        text(
            "UPDATE auth.sessions SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"
        ),
        {"u": user_id},
    )


async def set_disabled(
    sessions: async_sessionmaker[AsyncSession], email: str, *, disabled: bool
) -> None:
    async with sessions() as db, db.begin():
        user_id = await _user_id(db, email)
        await db.execute(
            text(
                "UPDATE auth.users SET disabled_at = CASE WHEN :d THEN now() END, "
                "updated_at = now() WHERE id = :u"
            ),
            {"d": disabled, "u": user_id},
        )
        if disabled:
            await _revoke_sessions(db, user_id)


async def reset_password(
    sessions: async_sessionmaker[AsyncSession], email: str, new_password: str
) -> None:
    password_hash = hash_password(new_password)
    async with sessions() as db, db.begin():
        user_id = await _user_id(db, email)
        await db.execute(
            text("UPDATE auth.users SET password_hash = :h, updated_at = now() WHERE id = :u"),
            {"h": password_hash, "u": user_id},
        )
        await _revoke_sessions(db, user_id)


async def list_users(sessions: async_sessionmaker[AsyncSession], org_id: UUID) -> list[UserSummary]:
    async with tenant_session(sessions, TenantContext(org_id)) as db:
        rows = await db.execute(
            text(
                """
                SELECT u.id, u.email, m.role, m.can_review, u.disabled_at IS NOT NULL AS disabled
                FROM app.memberships m JOIN auth.users u ON u.id = m.user_id
                ORDER BY u.email
                """
            )
        )
        return [UserSummary(r.id, r.email, r.role, r.can_review, r.disabled) for r in rows]
