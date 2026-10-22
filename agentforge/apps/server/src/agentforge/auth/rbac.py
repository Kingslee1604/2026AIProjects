"""Roles and permissions: one static matrix, checked by one function (architecture §13.3).

Release 1 has two roles. Adding the Release 3 roles changes this table, not the call sites.
"""

from collections.abc import Iterable
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
    MCP_USE = "mcp:use"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.MEMBER: frozenset(
        {
            Permission.KB_READ,
            Permission.KB_WRITE,
            Permission.RUNS_READ,
            Permission.RUNS_WRITE,
            Permission.MCP_USE,
        }
    ),
}

# API key scopes (FR-17) and what each allows. Keys never decide reviews, manage members or
# keys, or read the audit log: those stay with a signed-in person.
SCOPES: dict[str, frozenset[Permission]] = {
    "api:read": frozenset({Permission.KB_READ, Permission.RUNS_READ}),
    "api:write": frozenset({Permission.KB_WRITE, Permission.RUNS_WRITE}),
    "mcp:use": frozenset({Permission.MCP_USE}),
}


def scope_permissions(scopes: Iterable[str]) -> frozenset[Permission]:
    return frozenset(p for scope in scopes for p in SCOPES.get(scope, frozenset()))


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, resolved per request: a signed-in person, or an API key acting
    for the person who created it."""

    user_id: UUID
    email: str
    org_id: UUID
    role: Role
    can_review: bool
    session_token_hash: bytes
    api_key_id: UUID | None = None
    # For an API key: what its scopes allow (always also limited by the creator's role).
    key_permissions: frozenset[Permission] | None = None


def authorize(principal: Principal, permission: Permission) -> bool:
    if principal.key_permissions is not None and permission not in principal.key_permissions:
        return False
    if permission is Permission.REVIEWS_DECIDE and principal.can_review:
        return True
    return permission in ROLE_PERMISSIONS[principal.role]
