"""Audit log: append-only for the app, and auth/user events are recorded (R1-07, S13)."""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.cli import main
from agentforge.core.settings import get_settings
from agentforge.db.engine import create_sync_engine

from .conftest import PASSWORD, Database, UserFactory, login, settings_for


def audit_rows(database: Database, where: str, **params: object) -> list[dict[str, object]]:
    engine = create_sync_engine(database.url("agentforge_owner"))
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(f"SELECT * FROM app.audit_log WHERE {where} ORDER BY id"),  # noqa: S608
                params,
            )
            return [dict(row._mapping) for row in rows]
    finally:
        engine.dispose()


def test_app_role_cannot_update_or_delete_audit_rows(database: Database) -> None:
    engine = create_sync_engine(database.url("agentforge_app"))
    try:
        for statement in (
            "UPDATE app.audit_log SET outcome = 'success'",
            "DELETE FROM app.audit_log",
        ):
            with pytest.raises(ProgrammingError, match="permission denied"), engine.begin() as c:
                c.execute(text(statement))
        with pytest.raises(ProgrammingError, match="permission denied"), engine.begin() as c:
            c.execute(text("TRUNCATE app.audit_log"))
    finally:
        engine.dispose()


def test_sign_in_and_sign_out_are_audited(database: Database, make_user: UserFactory) -> None:
    email = make_user()
    with TestClient(create_app(settings_for(database))) as client:
        client.post("/v1/auth/login", json={"email": email, "password": "definitely wrong"})
        csrf = login(client, email)
        assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204

    rows = audit_rows(database, "actor_email = :e", e=email)
    assert [(r["action"], r["outcome"]) for r in rows] == [
        ("auth.login", "failure"),
        ("auth.login", "success"),
        ("auth.logout", "success"),
    ]
    failure, success, _ = rows
    assert failure["org_id"] is None  # before sign-in there is no organization
    assert failure["details"] == {"reason": "invalid_credentials"}
    assert success["org_id"] is not None
    assert success["trace_id"]
    assert all(PASSWORD not in str(r) for r in rows)


def test_lockout_is_audited_as_denied(database: Database, make_user: UserFactory) -> None:
    email = make_user()
    with TestClient(create_app(settings_for(database))) as client:
        for _ in range(6):
            client.post("/v1/auth/login", json={"email": email, "password": "wrong wrong wrong"})
    outcomes = [r["outcome"] for r in audit_rows(database, "actor_email = :e", e=email)]
    assert outcomes == ["failure"] * 5 + ["denied"]


def test_cli_user_changes_are_audited(database: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    email = f"audited-{uuid.uuid4().hex[:8]}@example.test"
    monkeypatch.setenv("AGENTFORGE_DATABASE_URL", database.url("agentforge_app"))
    get_settings.cache_clear()
    try:
        monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
        assert main(["user", "create", "--email", email, "--password-stdin"]) == 0
        assert main(["user", "disable", "--email", email]) == 0
        assert main(["user", "enable", "--email", email]) == 0
        monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "!\n"))
        assert main(["user", "reset-password", "--email", email, "--password-stdin"]) == 0
    finally:
        get_settings.cache_clear()
    rows = audit_rows(database, "target = :e", e=email)
    assert [r["action"] for r in rows] == [
        "user.create",
        "user.disable",
        "user.enable",
        "user.reset_password",
    ]
    assert {r["actor_email"] for r in rows} == {"cli"}


def test_admins_can_read_their_audit_log_members_cannot(
    database: Database, make_user: UserFactory
) -> None:
    admin, member = make_user(Role.ADMIN), make_user(Role.MEMBER)
    with TestClient(create_app(settings_for(database))) as client:
        login(client, member)
        assert client.get("/v1/admin/audit").status_code == 403
        login(client, admin)
        entries = client.get("/v1/admin/audit?limit=500").json()
    actions = {(e["action"], e["actor_email"]) for e in entries}
    assert ("auth.login", admin) in actions
    assert all(e["actor_email"] is not None or e["action"] for e in entries)
    # Pre-sign-in failures (org_id NULL) never appear in an organization's log.
    assert not any(e["outcome"] == "failure" and e["action"] == "auth.login" for e in entries)
