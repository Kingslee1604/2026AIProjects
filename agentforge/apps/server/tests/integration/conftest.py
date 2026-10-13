"""Real Postgres for integration tests: the same image and role setup as the Compose stack."""

import asyncio
import os
import secrets
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.auth.users import create_user
from agentforge.core.settings import Settings
from agentforge.db.engine import create_app_engine, create_sessionmaker, create_sync_engine
from agentforge.db.migrate import DEFAULT_ORG_ID, seed_defaults, upgrade

# Ryuk (testcontainers' reaper container) intermittently fails its port-mapping check on Docker
# Desktop. The `database` fixture stops its own container in a context manager, so the reaper
# is not needed; set TESTCONTAINERS_RYUK_DISABLED=false to opt back in.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

PROJECT_ROOT = Path(__file__).resolve().parents[4]
INITDB_DIR = PROJECT_ROOT / "deploy" / "postgres" / "initdb"
PG_IMAGE = (
    "pgvector/pgvector:0.8.7-pg18-trixie"
    "@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0"
)


@dataclass(frozen=True)
class Database:
    host: str
    port: int
    passwords: dict[str, str]

    def url(self, role: str, database: str = "agentforge") -> str:
        return f"postgresql://{role}:{self.passwords[role]}@{self.host}:{self.port}/{database}"


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return False
    return True


def _wait_until_connectable(url: str, timeout: float = 60.0) -> None:
    """The image runs init scripts on a temporary socket-only server, then restarts.

    Wait until the final server accepts TCP logins for a role the init script creates.
    """
    deadline = time.monotonic() + timeout
    while True:
        engine = create_sync_engine(url)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)
        finally:
            engine.dispose()


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    if not _docker_available():
        pytest.skip("Docker is not available; database integration tests need it")
    from testcontainers.community.postgres import PostgresContainer

    passwords = {
        role: secrets.token_urlsafe(24)
        for role in ("agentforge_owner", "agentforge_app", "agentforge_engine")
    }
    container = (
        PostgresContainer(
            PG_IMAGE,
            username="agentforge_owner",
            password=passwords["agentforge_owner"],
            dbname="agentforge",
            driver=None,
        )
        .with_env("AGENTFORGE_APP_DB_PASSWORD", passwords["agentforge_app"])
        .with_env("AGENTFORGE_ENGINE_DB_PASSWORD", passwords["agentforge_engine"])
        .with_volume_mapping(str(INITDB_DIR), "/docker-entrypoint-initdb.d", "ro")
    )
    with container:
        db = Database(
            container.get_container_host_ip(), int(container.get_exposed_port(5432)), passwords
        )
        _wait_until_connectable(db.url("agentforge_app"))
        upgrade(db.url("agentforge_owner"))
        seed_defaults(db.url("agentforge_owner"))
        yield db


# --- Shared helpers for tests that sign in --------------------------------------------------

PASSWORD = "correct horse battery staple"
UserFactory = Callable[..., str]


def settings_for(database: Database, **overrides: object) -> Settings:
    return Settings(
        env="test",
        database_url=SecretStr(database.url("agentforge_app")),
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture
def make_user(database: Database) -> UserFactory:
    """Create a user in the default organization and return their email."""

    def factory(role: Role = Role.MEMBER, can_review: bool = False) -> str:
        email = f"user-{uuid.uuid4().hex[:10]}@example.test"

        async def run() -> None:
            engine = create_app_engine(database.url("agentforge_app"), pool_size=1)
            try:
                await create_user(
                    create_sessionmaker(engine),
                    org_id=DEFAULT_ORG_ID,
                    email=email,
                    password=PASSWORD,
                    role=role,
                    can_review=can_review,
                )
            finally:
                await engine.dispose()

        asyncio.run(run())
        return email

    return factory


@pytest.fixture
def client(database: Database) -> Iterator[TestClient]:
    with TestClient(create_app(settings_for(database))) as test_client:
        yield test_client


def login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    """Sign in and return the CSRF token."""
    response = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["csrf_token"])
