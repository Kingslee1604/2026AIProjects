"""Roles and permissions: one static matrix, checked by one function (architecture §13.3).

Release 1 has two roles. Adding the Release 3 roles changes this table, not the call sites.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class Permission(StrEnum):
    KB_READ = "kb:read"
    KB_WRITE = "kb:write"
    RUNS_READ = "runs:read"
    RUNS_WRITE = "runs:write"
    REVIEWS_DECIDE = "reviews:decide"
    MEMBERS_MANAGE = "members:manage"
    API_KEYS_MANAGE = "api_keys:manage"
    AUDIT_READ = "audit:read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.MEMBER: frozenset(
        {Permission.KB_READ, Permission.KB_WRITE, Permission.RUNS_READ, Permission.RUNS_WRITE}
    ),
}


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, resolved per request."""

    user_id: UUID
    email: str
    org_id: UUID
    role: Role
    can_review: bool
    session_token_hash: bytes


def authorize(principal: Principal, permission: Permission) -> bool:
    if permission is Permission.REVIEWS_DECIDE and principal.can_review:
        return True
    return permission in ROLE_PERMISSIONS[principal.role]
