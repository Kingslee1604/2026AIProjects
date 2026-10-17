"""Login, session lifecycle, and brute-force limits, backed by Postgres."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge import audit
from agentforge.audit import AuditEvent
from agentforge.auth.passwords import hash_password, needs_rehash, verify_password
from agentforge.auth.rbac import Principal, Role
from agentforge.core.settings import Settings
from agentforge.db.tenancy import user_session

# Sessions are touched at most once a minute, to avoid a write on every request.
TOUCH_INTERVAL = timedelta(minutes=1)


class LoginThrottledError(Exception):
    """Too many recent failures for this email or client address."""


class InvalidCredentialsError(Exception):
    """Wrong email or password, disabled account, or no organization membership."""


@dataclass(frozen=True, slots=True)
class NewSession:
    token: str  # goes into the cookie; never stored
    csrf_token: str
    principal: Principal


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessions = sessions
        self._settings = settings

    async def _check_throttle(self, db: AsyncSession, email: str, ip: str | None) -> None:
        window = timedelta(minutes=self._settings.login_window_minutes)
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (WHERE email = :email) AS email_failures,
                      count(*) FILTER (WHERE ip = CAST(:ip AS inet)) AS ip_failures
                    FROM auth.login_attempts
                    WHERE NOT succeeded AND at > now() - :window
                      AND (email = :email OR ip = CAST(:ip AS inet))
                    """
                ),
                {"email": email, "ip": ip, "window": window},
            )
        ).one()
        if (
            row.email_failures >= self._settings.login_max_failures_per_email
            or row.ip_failures >= self._settings.login_max_failures_per_ip
        ):
            raise LoginThrottledError

    async def login(
        self, email: str, password: str, *, ip: str | None, user_agent: str | None
    ) -> NewSession:
        email = normalize_email(email)
        try:
            async with self._sessions() as db, db.begin():
                await self._check_throttle(db, email, ip)
        except LoginThrottledError:
            await audit.record(
                self._sessions,
                AuditEvent(
                    "auth.login",
                    "denied",
                    actor_email=email,
                    ip=ip,
                    details={"reason": "throttled"},
                ),
            )
            raise
        async with self._sessions() as db, db.begin():
            user = (
                await db.execute(
                    text("SELECT id, password_hash, disabled_at FROM auth.users WHERE email = :e"),
                    {"e": email},
                )
            ).one_or_none()
            ok = verify_password(user.password_hash if user else None, password)
            ok = ok and user is not None and user.disabled_at is None
            await db.execute(
                text(
                    "INSERT INTO auth.login_attempts (email, ip, succeeded) "
                    "VALUES (:e, CAST(:ip AS inet), :ok)"
                ),
                {"e": email, "ip": ip, "ok": ok},
            )
        if not ok or user is None:
            await self._audit_login_failure(email, ip, "invalid_credentials")
            raise InvalidCredentialsError

        user_id: UUID = user.id
        if needs_rehash(user.password_hash):
            async with self._sessions() as db, db.begin():
                await db.execute(
                    text(
                        "UPDATE auth.users SET password_hash = :h, updated_at = now() WHERE id = :u"
                    ),
                    {"h": hash_password(password), "u": user_id},
                )

        async with user_session(self._sessions, user_id) as db:
            membership = (
                await db.execute(
                    text(
                        "SELECT org_id, role, can_review FROM app.memberships "
                        "WHERE user_id = :u ORDER BY created_at LIMIT 1"
                    ),
                    {"u": user_id},
                )
            ).one_or_none()
        if membership is None:
            await self._audit_login_failure(email, ip, "no_membership")
            raise InvalidCredentialsError

        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        async with self._sessions() as db, db.begin():
            await db.execute(
                text(
                    """
                    INSERT INTO auth.sessions
                      (token_hash, user_id, org_id, csrf_token, expires_at, user_agent, ip)
                    VALUES (:h, :u, :o, :c, now() + :absolute, :ua, CAST(:ip AS inet))
                    """
                ),
                {
                    "h": hash_token(token),
                    "u": user_id,
                    "o": membership.org_id,
                    "c": csrf_token,
                    "absolute": timedelta(hours=self._settings.session_absolute_hours),
                    "ua": (user_agent or "")[:300],
                    "ip": ip,
                },
            )
        principal = Principal(
            user_id=user_id,
            email=email,
            org_id=membership.org_id,
            role=Role(membership.role),
            can_review=membership.can_review,
            session_token_hash=hash_token(token),
        )
        await audit.record(
            self._sessions,
            AuditEvent(
                "auth.login",
                "success",
                org_id=principal.org_id,
                actor_user_id=user_id,
                actor_email=email,
                ip=ip,
            ),
        )
        return NewSession(token=token, csrf_token=csrf_token, principal=principal)

    async def _audit_login_failure(self, email: str, ip: str | None, reason: str) -> None:
        # No organization yet: stored with org_id NULL (visible to the schema owner only).
        await audit.record(
            self._sessions,
            AuditEvent(
                "auth.login", "failure", actor_email=email, ip=ip, details={"reason": reason}
            ),
        )

    async def resolve(self, token: str) -> tuple[Principal, str] | None:
        """Principal and CSRF token for a live session.

        None if the session is unknown, revoked, idle too long, past its absolute lifetime,
        or belongs to a disabled user or one who lost their membership.
        """
        token_hash = hash_token(token)
        idle = timedelta(minutes=self._settings.session_idle_minutes)
        async with self._sessions() as db, db.begin():
            row = (
                await db.execute(
                    text(
                        """
                        SELECT s.user_id, s.org_id, s.csrf_token, u.email,
                               s.last_seen_at < now() - :touch AS stale
                        FROM auth.sessions s JOIN auth.users u ON u.id = s.user_id
                        WHERE s.token_hash = :h
                          AND s.revoked_at IS NULL
                          AND s.expires_at > now()
                          AND s.last_seen_at > now() - :idle
                          AND u.disabled_at IS NULL
                        """
                    ),
                    {"h": token_hash, "idle": idle, "touch": TOUCH_INTERVAL},
                )
            ).one_or_none()
            if row is None:
                return None
            if row.stale:
                await db.execute(
                    text("UPDATE auth.sessions SET last_seen_at = now() WHERE token_hash = :h"),
                    {"h": token_hash},
                )
        async with user_session(self._sessions, row.user_id) as db:
            membership = (
                await db.execute(
                    text(
                        "SELECT role, can_review FROM app.memberships "
                        "WHERE user_id = :u AND org_id = :o"
                    ),
                    {"u": row.user_id, "o": row.org_id},
                )
            ).one_or_none()
        if membership is None:  # membership removed since login
            return None
        principal = Principal(
            user_id=row.user_id,
            email=row.email,
            org_id=row.org_id,
            role=Role(membership.role),
            can_review=membership.can_review,
            session_token_hash=token_hash,
        )
        return principal, row.csrf_token

    async def revoke(self, token_hash: bytes) -> None:
        """Revoke one session without auditing (used when a login replaces an old session)."""
        async with self._sessions() as db, db.begin():
            await db.execute(
                text(
                    "UPDATE auth.sessions SET revoked_at = now() "
                    "WHERE token_hash = :h AND revoked_at IS NULL"
                ),
                {"h": token_hash},
            )

    async def logout(self, principal: Principal, *, ip: str | None) -> None:
        await self.revoke(principal.session_token_hash)
        await audit.record(self._sessions, _principal_event("auth.logout", principal, ip))

    async def logout_everywhere(self, principal: Principal, *, ip: str | None) -> int:
        async with self._sessions() as db, db.begin():
            result = await db.execute(
                text(
                    "UPDATE auth.sessions SET revoked_at = now() "
                    "WHERE user_id = :u AND revoked_at IS NULL RETURNING 1"
                ),
                {"u": principal.user_id},
            )
            revoked = len(result.all())
        event = _principal_event("auth.logout_all", principal, ip, sessions_revoked=revoked)
        await audit.record(self._sessions, event)
        return revoked


def _principal_event(
    action: str, principal: Principal, ip: str | None, **details: str | int | bool | None
) -> AuditEvent:
    return AuditEvent(
        action,
        "success",
        org_id=principal.org_id,
        actor_user_id=principal.user_id,
        actor_email=principal.email,
        ip=ip,
        details=dict(details),
    )
