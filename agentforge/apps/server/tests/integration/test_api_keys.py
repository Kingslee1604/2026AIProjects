"""Workspace API keys (R1-42) on real Postgres: created by an admin and shown once, sent as a
Bearer token, limited to their scopes, refused when revoked, expired, wrong, or when their
creator is disabled; audited; and invisible to other organizations."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.db.engine import create_sync_engine

from .conftest import Database, UserFactory, login, outsider, settings_for


@pytest.fixture
def app(database: Database, tmp_path: Path) -> FastAPI:
    return create_app(settings_for(database, model_provider="fake", blob_root=tmp_path / "b"))


class Admin:
    def __init__(self, client: TestClient, csrf: str, email: str) -> None:
        self.client, self.csrf, self.email = client, csrf, email

    def create(self, *scopes: str, name: str = "CI pipeline", days: int = 90) -> dict[str, Any]:
        response = self.client.post(
            "/v1/api-keys",
            json={"name": name, "scopes": list(scopes), "expires_in_days": days},
            headers={"X-CSRF-Token": self.csrf},
        )
        assert response.status_code == 201, response.text
        return response.json()

    def revoke(self, key_id: str) -> Any:
        return self.client.delete(f"/v1/api-keys/{key_id}", headers={"X-CSRF-Token": self.csrf})


@pytest.fixture
def admin(app: FastAPI, make_user: UserFactory) -> Iterator[Admin]:
    email = make_user(Role.ADMIN)
    with TestClient(app) as client:
        yield Admin(client, login(client, email), email)


@pytest.fixture
def api(app: FastAPI) -> Iterator[TestClient]:
    """A client with no session cookie: only the key authenticates it."""
    with TestClient(app) as client:
        yield client


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def owner_sql(database: Database, sql: str, **params: object) -> list[Any]:
    engine = create_sync_engine(database.url("agentforge_owner"))
    try:
        with engine.begin() as connection:
            result = connection.execute(text(sql), params)
            return list(result.all()) if result.returns_rows else []
    finally:
        engine.dispose()


def test_a_key_is_shown_once_and_works_as_a_bearer_token(admin: Admin, api: TestClient) -> None:
    created = admin.create("api:read")
    key = created["key"]
    assert key.startswith("af_")
    assert created["api_key"]["prefix"] in key
    assert created["api_key"]["scopes"] == ["api:read"]
    assert created["api_key"]["created_by_email"] == admin.email

    listed = admin.client.get("/v1/api-keys").json()
    mine = next(k for k in listed if k["id"] == created["api_key"]["id"])
    assert "key" not in mine
    assert key not in str(listed)  # the secret is never returned again

    assert api.get("/v1/knowledge-bases", headers=bearer(key)).status_code == 200
    # Read only: writing is refused (and no CSRF token is needed to find that out).
    refused = api.post("/v1/knowledge-bases", json={"name": "nope"}, headers=bearer(key))
    assert refused.status_code == 403
    assert "kb:write" in refused.json()["title"]

    used = next(k for k in admin.client.get("/v1/api-keys").json() if k["id"] == mine["id"])
    assert used["last_used_at"] is not None


def test_a_write_key_needs_no_csrf_and_acts_for_its_creator(
    admin: Admin, api: TestClient, database: Database
) -> None:
    key = admin.create("api:read", "api:write")["key"]
    created = api.post(
        "/v1/knowledge-bases", json={"name": f"via key {uuid.uuid4().hex[:6]}"}, headers=bearer(key)
    )
    assert created.status_code == 201, created.text
    [row] = owner_sql(
        database,
        "SELECT u.email FROM app.knowledge_bases k JOIN auth.users u ON u.id = k.created_by "
        "WHERE k.id = :k",
        k=created.json()["id"],
    )
    assert row.email == admin.email


def test_keys_cannot_manage_keys_sessions_admin_pages_or_reviews(
    admin: Admin, api: TestClient
) -> None:
    key = admin.create("api:read", "api:write", "mcp:use")["key"]  # every scope
    assert api.get("/v1/api-keys", headers=bearer(key)).status_code == 403
    assert (
        api.post("/v1/api-keys", json={"name": "x", "scopes": ["api:read"]}, headers=bearer(key))
    ).status_code == 403
    assert api.get("/v1/admin/audit", headers=bearer(key)).status_code == 403
    assert api.get("/v1/admin/users", headers=bearer(key)).status_code == 403
    assert api.get("/v1/auth/session", headers=bearer(key)).status_code == 403
    # A key cannot sign its creator out everywhere.
    assert api.post("/v1/auth/logout-all", headers=bearer(key)).status_code == 403
    assert admin.client.get("/v1/auth/session").status_code == 200
    decide = api.post(
        f"/v1/runs/{uuid.uuid4()}/review/items/0",
        json={"decision": "approved"},
        headers=bearer(key),
    )
    assert decide.status_code == 403


def test_revoked_expired_wrong_and_malformed_keys_are_refused(
    admin: Admin, api: TestClient, database: Database
) -> None:
    def status(headers: dict[str, str]) -> int:
        return api.get("/v1/knowledge-bases", headers=headers).status_code

    revoked = admin.create("api:read")
    assert status(bearer(revoked["key"])) == 200
    assert admin.revoke(revoked["api_key"]["id"]).json()["revoked_at"] is not None
    assert status(bearer(revoked["key"])) == 401
    assert admin.revoke(revoked["api_key"]["id"]).status_code == 200  # twice is harmless

    expired = admin.create("api:read")
    owner_sql(
        database,
        "UPDATE auth.api_keys SET expires_at = now() - interval '1 second' WHERE id = :k",
        k=expired["api_key"]["id"],
    )
    assert status(bearer(expired["key"])) == 401

    good = admin.create("api:read")["key"]
    wrong_secret = good[: good.rindex("_") + 1] + "x" * 43  # right prefix, wrong secret
    for headers in (
        bearer(wrong_secret),
        bearer("af_nosuchkey1_" + "y" * 43),
        bearer("not-a-key"),
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer "},
    ):
        assert status(headers) == 401, headers
    assert status(bearer(good)) == 200


def test_a_key_stops_working_when_its_creator_is_disabled(
    admin: Admin, api: TestClient, database: Database
) -> None:
    key = admin.create("api:read")["key"]
    assert api.get("/v1/knowledge-bases", headers=bearer(key)).status_code == 200
    owner_sql(database, "UPDATE auth.users SET disabled_at = now() WHERE email = :e", e=admin.email)
    assert api.get("/v1/knowledge-bases", headers=bearer(key)).status_code == 401


def test_members_cannot_manage_keys_and_input_is_validated(
    app: FastAPI, admin: Admin, make_user: UserFactory
) -> None:
    with TestClient(app) as member:
        csrf = login(member, make_user(Role.MEMBER))
        assert member.get("/v1/api-keys").status_code == 403
        made = member.post(
            "/v1/api-keys",
            json={"name": "mine", "scopes": ["api:read"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert made.status_code == 403
    for body in (
        {"name": "x", "scopes": []},
        {"name": "x", "scopes": ["admin"]},
        {"name": "x", "scopes": ["api:read"], "expires_in_days": 0},
        {"name": "x", "scopes": ["api:read"], "expires_in_days": 366},
        {"name": "", "scopes": ["api:read"]},
    ):
        response = admin.client.post(
            "/v1/api-keys", json=body, headers={"X-CSRF-Token": admin.csrf}
        )
        assert response.status_code == 422, body


def test_create_and_revoke_are_audited_without_the_secret(admin: Admin, database: Database) -> None:
    created = admin.create("api:read", "mcp:use", name="audited")
    admin.revoke(created["api_key"]["id"])
    rows = owner_sql(
        database,
        "SELECT action, actor_email, details::text AS details FROM app.audit_log "
        "WHERE target = :t ORDER BY at",
        t=f"api_key:{created['api_key']['id']}",
    )
    assert [r.action for r in rows] == ["api_key.create", "api_key.revoke"]
    assert {r.actor_email for r in rows} == {admin.email}
    assert "api:read mcp:use" in rows[0].details
    assert created["api_key"]["prefix"] in rows[0].details
    secret = created["key"].rsplit("_", 1)[1]
    assert all(secret not in r.details for r in rows)


def test_other_organizations_cannot_see_or_revoke_keys(
    app: FastAPI, admin: Admin, database: Database
) -> None:
    created = admin.create("api:read")
    with outsider(app, database) as (other, csrf):
        assert all(k["id"] != created["api_key"]["id"] for k in other.get("/v1/api-keys").json())
        revoke = other.delete(
            f"/v1/api-keys/{created['api_key']['id']}", headers={"X-CSRF-Token": csrf}
        )
        assert revoke.status_code == 404
    # Still working: the outsider's attempt changed nothing.
    with TestClient(app) as api:
        assert api.get("/v1/knowledge-bases", headers=bearer(created["key"])).status_code == 200
