"""Authentication and authorization (R1-06; security baseline S2, S3)."""

import asyncio
import io
import re
import uuid

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentforge.api.app import PUBLIC_ROUTES, create_app
from agentforge.auth.rbac import Role
from agentforge.auth.service import AuthService, InvalidCredentialsError, LoginThrottledError
from agentforge.auth.users import set_disabled
from agentforge.cli import main
from agentforge.core.settings import Settings, get_settings
from agentforge.db.engine import create_app_engine, create_sessionmaker, create_sync_engine
from agentforge.db.migrate import DEFAULT_ORG_ID

from .conftest import PASSWORD, Database, UserFactory, login, settings_for


def owner_sql(database: Database, sql: str, **params: object) -> None:
    engine = create_sync_engine(database.url("agentforge_owner"))
    try:
        with engine.begin() as connection:
            connection.execute(text(sql), params)
    finally:
        engine.dispose()


# --- Sign-in --------------------------------------------------------------------------------


def test_login_sets_httponly_samesite_cookie(client: TestClient, make_user: UserFactory) -> None:
    email = make_user()
    response = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("af_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    body = response.json()
    assert body["email"] == email
    assert body["role"] == "member"
    assert body["org_id"] == str(DEFAULT_ORG_ID)


def test_secure_mode_uses_host_prefixed_secure_cookie(
    database: Database, make_user: UserFactory
) -> None:
    email = make_user()
    app = create_app(settings_for(database, cookie_secure=True))
    with TestClient(app, base_url="https://testserver") as secure_client:
        response = secure_client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("__Host-af_session=")
        assert "Secure" in cookie
        assert secure_client.get("/v1/auth/session").status_code == 200


def test_production_refuses_insecure_cookies() -> None:
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(env="production", cookie_secure=False)


def test_wrong_password_and_unknown_email_look_identical(
    client: TestClient, make_user: UserFactory
) -> None:
    email = make_user()
    wrong = client.post("/v1/auth/login", json={"email": email, "password": "wrong password!!"})
    unknown = client.post(
        "/v1/auth/login", json={"email": "nobody@example.test", "password": PASSWORD}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["title"] == unknown.json()["title"] == "Invalid email or password"
    assert "set-cookie" not in wrong.headers


def test_email_is_case_insensitive(client: TestClient, make_user: UserFactory) -> None:
    email = make_user()
    login(client, email.upper())


def test_passwords_are_stored_as_argon2id(database: Database, make_user: UserFactory) -> None:
    email = make_user()
    engine = create_sync_engine(database.url("agentforge_owner"))
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT password_hash FROM auth.users WHERE email = :e"), {"e": email}
        ).scalar_one()
    engine.dispose()
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


# --- Sessions -------------------------------------------------------------------------------


def test_session_endpoint_requires_and_reflects_login(
    client: TestClient, make_user: UserFactory
) -> None:
    assert client.get("/v1/auth/session").status_code == 401
    email = make_user()
    csrf = login(client, email)
    session = client.get("/v1/auth/session").json()
    assert session["email"] == email
    assert session["csrf_token"] == csrf


def test_login_rotates_the_session(
    database: Database, client: TestClient, make_user: UserFactory
) -> None:
    email = make_user()
    login(client, email)
    first = client.cookies["af_session"]
    login(client, email)
    second = client.cookies["af_session"]
    assert first != second
    with TestClient(create_app(settings_for(database))) as other:
        other.cookies.set("af_session", first)
        assert other.get("/v1/auth/session").status_code == 401  # old session was revoked


def test_unsafe_requests_need_the_csrf_token(client: TestClient, make_user: UserFactory) -> None:
    csrf = login(client, make_user())
    assert client.post("/v1/auth/logout").status_code == 403
    assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert client.get("/v1/auth/session").status_code == 200  # still signed in
    assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/v1/auth/session").status_code == 401


def test_logout_everywhere_ends_all_sessions(database: Database, make_user: UserFactory) -> None:
    email = make_user()
    with (
        TestClient(create_app(settings_for(database))) as laptop,
        TestClient(create_app(settings_for(database))) as phone,
    ):
        csrf = login(laptop, email)
        login(phone, email)
        assert laptop.post("/v1/auth/logout-all", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert phone.get("/v1/auth/session").status_code == 401
        assert laptop.get("/v1/auth/session").status_code == 401


@pytest.mark.parametrize(
    "expire_sql",
    [
        "UPDATE auth.sessions SET last_seen_at = now() - interval '1 day' WHERE user_id = "
        "(SELECT id FROM auth.users WHERE email = :e)",
        "UPDATE auth.sessions SET expires_at = now() - interval '1 second' WHERE user_id = "
        "(SELECT id FROM auth.users WHERE email = :e)",
    ],
    ids=["idle-timeout", "absolute-timeout"],
)
def test_expired_sessions_are_rejected(
    database: Database, client: TestClient, make_user: UserFactory, expire_sql: str
) -> None:
    email = make_user()
    login(client, email)
    owner_sql(database, expire_sql, e=email)
    assert client.get("/v1/auth/session").status_code == 401


def test_disabling_a_user_ends_their_sessions(
    database: Database, client: TestClient, make_user: UserFactory
) -> None:
    email = make_user()
    login(client, email)

    async def disable() -> None:
        engine = create_app_engine(database.url("agentforge_app"), pool_size=1)
        await set_disabled(create_sessionmaker(engine), email, disabled=True)
        await engine.dispose()

    asyncio.run(disable())
    assert client.get("/v1/auth/session").status_code == 401
    response = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 401


# --- Brute-force protection -----------------------------------------------------------------


def test_repeated_failures_lock_the_account(client: TestClient, make_user: UserFactory) -> None:
    email = make_user()
    for _ in range(5):
        response = client.post(
            "/v1/auth/login", json={"email": email, "password": "nope nope nope"}
        )
        assert response.status_code == 401
    locked = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert locked.status_code == 429
    assert locked.headers["retry-after"] == "900"
    login(client, make_user())  # other accounts are unaffected


def test_repeated_failures_from_one_address_are_throttled(database: Database) -> None:
    settings = settings_for(database, login_max_failures_per_ip=3)

    async def scenario() -> None:
        engine = create_app_engine(database.url("agentforge_app"), pool_size=1)
        auth = AuthService(create_sessionmaker(engine), settings)
        ip = f"203.0.113.{uuid.uuid4().int % 250 + 1}"
        try:
            for n in range(3):  # password spraying: different emails, one address
                with pytest.raises(InvalidCredentialsError):
                    await auth.login(f"spray-{n}@example.test", "x" * 12, ip=ip, user_agent=None)
            with pytest.raises(LoginThrottledError):
                await auth.login("spray-x@example.test", "x" * 12, ip=ip, user_agent=None)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# --- Authorization ----------------------------------------------------------------------------


def test_members_cannot_use_admin_routes(database: Database, make_user: UserFactory) -> None:
    member, admin = make_user(Role.MEMBER), make_user(Role.ADMIN)
    with TestClient(create_app(settings_for(database))) as client:
        login(client, member)
        response = client.get("/v1/admin/users")
        assert response.status_code == 403
        assert response.json()["title"] == "Missing permission: members:manage"
        login(client, admin)
        users = client.get("/v1/admin/users")
        assert users.status_code == 200
        emails = {u["email"] for u in users.json()}
        assert {member, admin} <= emails


def route_inventory(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path): documented routes from the OpenAPI schema (this covers included
    routers) plus top-level routes, which also catches routes hidden from the schema."""
    routes: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        routes.update((method.upper(), path) for method in operations)
    for route in app.router.routes:
        if isinstance(route, APIRoute):
            methods = (route.methods or set()) - {"HEAD"}
            routes.update((method, route.path) for method in methods)
    return routes


def test_every_non_public_route_requires_authentication(client: TestClient) -> None:
    """Route inventory (baseline S2): adding an unprotected route fails this test."""
    app = client.app
    assert isinstance(app, FastAPI)
    inventory = route_inventory(app)
    protected = sorted(inventory - PUBLIC_ROUTES)
    # Guards the test itself: if route discovery breaks, this must fail rather than pass empty.
    assert {("POST", "/v1/auth/logout"), ("GET", "/v1/admin/users")} <= set(protected)
    for method, route_path in protected:
        path = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), route_path)
        response = client.request(method, path, json={})
        assert response.status_code == 401, f"{method} {route_path} -> {response.status_code}"


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_creates_a_user_who_can_sign_in(
    database: Database, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = f"cli-{uuid.uuid4().hex[:8]}@example.test"
    monkeypatch.setenv("AGENTFORGE_DATABASE_URL", database.url("agentforge_app"))
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    get_settings.cache_clear()
    try:
        assert (
            main(["user", "create", "--email", email, "--role", "admin", "--password-stdin"]) == 0
        )
        monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
        assert main(["user", "reset-password", "--email", email, "--password-stdin"]) == 2
    finally:
        get_settings.cache_clear()
    session = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()
    assert session["role"] == "admin"
    assert session["can_review"] is True
