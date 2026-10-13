"""Real Postgres for integration tests: the same image and role setup as the Compose stack."""

import secrets
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text

from agentforge.db.engine import create_sync_engine
from agentforge.db.migrate import seed_defaults, upgrade

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
