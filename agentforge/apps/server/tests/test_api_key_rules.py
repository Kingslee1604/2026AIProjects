"""API key format and what a key may do (R1-42): pure rules, no database."""

import uuid

import pytest

from agentforge.auth.api_keys import parse
from agentforge.auth.rbac import Permission, Principal, Role, authorize, scope_permissions


def principal(role: Role, scopes: list[str] | None, *, can_review: bool = False) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        email="someone@example.test",
        org_id=uuid.uuid4(),
        role=role,
        can_review=can_review,
        session_token_hash=b"",
        api_key_id=uuid.uuid4() if scopes is not None else None,
        key_permissions=scope_permissions(scopes) if scopes is not None else None,
    )


@pytest.mark.parametrize(
    ("raw", "prefix"),
    [
        ("af_abcde12345_secretpart", "abcde12345"),
        ("af_abcde12345_has_underscores-and-dashes", "abcde12345"),
        ("af_ABCDE12345_secret", None),  # prefixes are lowercase
        ("af_short_secret", None),
        ("af_abcde12345_", None),
        ("af_abcde12345", None),
        ("xx_abcde12345_secret", None),
        ("", None),
    ],
)
def test_key_format(raw: str, prefix: str | None) -> None:
    assert parse(raw) == prefix


def test_a_key_can_only_do_what_its_scopes_allow() -> None:
    reader = principal(Role.ADMIN, ["api:read"])
    assert authorize(reader, Permission.KB_READ)
    assert authorize(reader, Permission.RUNS_READ)
    assert not authorize(reader, Permission.KB_WRITE)
    assert not authorize(reader, Permission.MCP_USE)
    mcp = principal(Role.ADMIN, ["mcp:use"])
    assert authorize(mcp, Permission.MCP_USE)
    assert not authorize(mcp, Permission.KB_READ)


def test_no_scope_lets_a_key_act_as_a_reviewer_or_an_admin() -> None:
    everything = principal(Role.ADMIN, ["api:read", "api:write", "mcp:use"], can_review=True)
    for permission in (
        Permission.REVIEWS_DECIDE,
        Permission.MEMBERS_MANAGE,
        Permission.API_KEYS_MANAGE,
        Permission.AUDIT_READ,
    ):
        assert not authorize(everything, permission), permission


def test_people_are_unaffected_by_scopes() -> None:
    assert authorize(principal(Role.ADMIN, None), Permission.API_KEYS_MANAGE)
    assert not authorize(principal(Role.MEMBER, None), Permission.API_KEYS_MANAGE)
    assert authorize(principal(Role.MEMBER, None, can_review=True), Permission.REVIEWS_DECIDE)
