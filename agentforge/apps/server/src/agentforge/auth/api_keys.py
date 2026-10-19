"""Workspace API keys (R1-42): created by an admin, shown once, sent as `Authorization: Bearer`.

A key is `af_<prefix>_<secret>`. The prefix finds the row; only a SHA-256 of the whole key is
stored and compared in constant time. A key acts for the person who created it, limited to its
scopes and to what that person's role allows *now*: a creator who is disabled, removed, or
demoted takes their keys' powers with them.
"""

import hashlib
import secrets
import string
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge import audit
from agentforge.audit import AuditEvent
from agentforge.auth.rbac import SCOPES, Principal, Role, scope_permissions
from agentforge.db.tenancy import TenantContext, tenant_session, user_session

PREFIX_LENGTH = 10
_PREFIX_ALPHABET = string.ascii_lowercase + string.digits
MAX_EXPIRY_DAYS = 365
TOUCH_INTERVAL = timedelta(minutes=1)  # last_used_at is written at most once a minute


class ApiKeyError(ValueError):
    """The request cannot be applied; the message is safe to show to the user."""


class NotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ApiKey:
    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    created_by_email: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewKey:
    secret: str  # the whole key; shown once, never stored
    key: ApiKey


def _hash(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


def parse(raw: str) -> str | None:
    """The prefix of a well-formed key, or None."""
    if not raw.startswith("af_"):
        return None
    prefix, sep, secret = raw[3:].partition("_")
    if not sep or len(prefix) != PREFIX_LENGTH or not secret:
        return None
    if any(c not in _PREFIX_ALPHABET for c in prefix):
        return None
    return prefix


_COLUMNS = (
    "k.id, k.name, k.prefix, k.scopes, u.email AS created_by_email, k.created_at, "
    "k.expires_at, k.last_used_at, k.revoked_at"
)


def _key(row: Any) -> ApiKey:
    return ApiKey(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        scopes=list(row.scopes),
        created_by_email=row.created_by_email,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


class ApiKeyService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        principal: Principal,
        *,
        name: str,
        scopes: Sequence[str],
        expires_in_days: int,
        ip: str | None,
    ) -> NewKey:
        name = name.strip()
        chosen = sorted(set(scopes))
        if not name or len(name) > 100:
            raise ApiKeyError("Give the key a name (up to 100 characters).")
        if not chosen or any(s not in SCOPES for s in chosen):
            raise ApiKeyError(f"Choose at least one scope of: {', '.join(SCOPES)}.")
        if not 1 <= expires_in_days <= MAX_EXPIRY_DAYS:
            raise ApiKeyError(f"Keys expire after 1 to {MAX_EXPIRY_DAYS} days.")
        prefix = "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(PREFIX_LENGTH))
        raw = f"af_{prefix}_{secrets.token_urlsafe(32)}"
        async with tenant_session(self._sessions, TenantContext(principal.org_id)) as db:
            workspace = (
                await db.execute(text("SELECT id FROM app.workspaces ORDER BY created_at LIMIT 1"))
            ).scalar_one()
            key_id: UUID = (
                await db.execute(
                    text(
                        "INSERT INTO auth.api_keys (org_id, workspace_id, name, prefix, "
                        "secret_hash, scopes, created_by, expires_at) VALUES (:o, :w, :n, :p, "
                        ":h, :s, :u, now() + :days) RETURNING id"
                    ),
                    {
                        "o": principal.org_id,
                        "w": workspace,
                        "n": name,
                        "p": prefix,
                        "h": _hash(raw),
                        "s": chosen,
                        "u": principal.user_id,
                        "days": timedelta(days=expires_in_days),
                    },
                )
            ).scalar_one()
        await self._audit(
            "api_key.create",
            principal,
            key_id,
            ip,
            prefix=prefix,
            scopes=" ".join(chosen),
            expires_in_days=expires_in_days,
        )
        return NewKey(secret=raw, key=await self.get(principal.org_id, key_id))

    async def get(self, org_id: UUID, key_id: UUID) -> ApiKey:
        async with self._sessions() as db, db.begin():
            row = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM auth.api_keys k "  # noqa: S608
                        "JOIN auth.users u ON u.id = k.created_by "
                        "WHERE k.id = :k AND k.org_id = :o"
                    ),
                    {"k": key_id, "o": org_id},
                )
            ).one_or_none()
        if row is None:
            raise NotFoundError("API key not found")
        return _key(row)

    async def list_keys(self, org_id: UUID) -> list[ApiKey]:
        """This organization's keys, newest first (no RLS here: filtered by org explicitly)."""
        async with self._sessions() as db, db.begin():
            rows = await db.execute(
                text(
                    f"SELECT {_COLUMNS} FROM auth.api_keys k "  # noqa: S608
                    "JOIN auth.users u ON u.id = k.created_by "
                    "WHERE k.org_id = :o ORDER BY k.created_at DESC"
                ),
                {"o": org_id},
            )
            return [_key(r) for r in rows]

    async def revoke(self, principal: Principal, key_id: UUID, *, ip: str | None) -> ApiKey:
        async with self._sessions() as db, db.begin():
            revoked = (
                await db.execute(
                    text(
                        "UPDATE auth.api_keys SET revoked_at = now(), revoked_by = :u "
                        "WHERE id = :k AND org_id = :o AND revoked_at IS NULL RETURNING prefix"
                    ),
                    {"u": principal.user_id, "k": key_id, "o": principal.org_id},
                )
            ).scalar_one_or_none()
        key = await self.get(principal.org_id, key_id)  # NotFoundError for other orgs
        if revoked is not None:
            await self._audit("api_key.revoke", principal, key_id, ip, prefix=revoked)
        return key

    async def resolve(self, raw: str) -> Principal | None:
        """The principal a key acts as, or None if it is malformed, unknown, wrong, revoked,
        expired, or its creator is disabled or no longer a member."""
        prefix = parse(raw)
        if prefix is None:
            return None
        async with self._sessions() as db, db.begin():
            row = (
                await db.execute(
                    text(
                        "SELECT k.id, k.org_id, k.secret_hash, k.scopes, k.created_by, u.email, "
                        "k.last_used_at IS NULL OR k.last_used_at < now() - :touch AS stale "
                        "FROM auth.api_keys k JOIN auth.users u ON u.id = k.created_by "
                        "WHERE k.prefix = :p AND k.revoked_at IS NULL AND k.expires_at > now() "
                        "AND u.disabled_at IS NULL"
                    ),
                    {"p": prefix, "touch": TOUCH_INTERVAL},
                )
            ).one_or_none()
            if row is None or not secrets.compare_digest(bytes(row.secret_hash), _hash(raw)):
                return None
            if row.stale:
                await db.execute(
                    text("UPDATE auth.api_keys SET last_used_at = now() WHERE id = :k"),
                    {"k": row.id},
                )
        async with user_session(self._sessions, row.created_by) as db:
            membership = (
                await db.execute(
                    text("SELECT role FROM app.memberships WHERE user_id = :u AND org_id = :o"),
                    {"u": row.created_by, "o": row.org_id},
                )
            ).one_or_none()
        if membership is None:
            return None
        return Principal(
            user_id=row.created_by,
            email=row.email,
            org_id=row.org_id,
            role=Role(membership.role),
            can_review=False,  # keys never decide reviews
            session_token_hash=b"",
            api_key_id=row.id,
            key_permissions=scope_permissions(row.scopes),
        )

    async def _audit(
        self,
        action: str,
        principal: Principal,
        key_id: UUID,
        ip: str | None,
        **details: str | int,
    ) -> None:
        await audit.record(
            self._sessions,
            AuditEvent(
                action,
                "success",
                org_id=principal.org_id,
                actor_user_id=principal.user_id,
                actor_email=principal.email,
                target=f"api_key:{key_id}",
                ip=ip,
                details=dict(details),  # never the secret
            ),
        )
