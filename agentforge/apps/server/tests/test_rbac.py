import subprocess
import sys
import uuid

import pytest

from agentforge.auth.cli_args import ROLES
from agentforge.auth.passwords import WeakPasswordError, hash_password, verify_password
from agentforge.auth.rbac import ROLE_PERMISSIONS, Permission, Principal, Role, authorize


def test_cli_role_choices_match_the_role_enum() -> None:
    assert set(ROLES) == {role.value for role in Role}


def test_healthcheck_command_stays_lightweight() -> None:
    """Container health checks run this every few seconds; heavy imports made them time out."""
    probe = (
        "import sys; from agentforge.cli import main; main(['healthcheck', 'worker']); "
        "print(','.join(m for m in ('sqlalchemy', 'argon2', 'fastapi', 'psycopg') "
        "if m in sys.modules))"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False, timeout=30
    )
    assert result.stdout.strip() == "", f"heavy modules imported: {result.stdout}"


def principal(role: Role, can_review: bool = False) -> Principal:
    return Principal(uuid.uuid4(), "a@example.test", uuid.uuid4(), role, can_review, b"x" * 32)


def test_admin_has_every_permission() -> None:
    assert ROLE_PERMISSIONS[Role.ADMIN] == frozenset(Permission)


@pytest.mark.parametrize(
    "permission",
    [Permission.MEMBERS_MANAGE, Permission.API_KEYS_MANAGE, Permission.AUDIT_READ],
)
def test_members_cannot_administer(permission: Permission) -> None:
    assert not authorize(principal(Role.MEMBER), permission)


def test_review_permission_follows_the_can_review_flag() -> None:
    assert not authorize(principal(Role.MEMBER), Permission.REVIEWS_DECIDE)
    assert authorize(principal(Role.MEMBER, can_review=True), Permission.REVIEWS_DECIDE)
    assert authorize(principal(Role.ADMIN), Permission.REVIEWS_DECIDE)


def test_password_rules() -> None:
    with pytest.raises(WeakPasswordError):
        hash_password("too short")
    with pytest.raises(WeakPasswordError):
        hash_password(" leading space is not allowed")
    stored = hash_password("a long enough passphrase")
    assert stored.startswith("$argon2id$")
    assert verify_password(stored, "a long enough passphrase")
    assert not verify_password(stored, "a long enough passphrasE")
    assert not verify_password(None, "a long enough passphrase")
