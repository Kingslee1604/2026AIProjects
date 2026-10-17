"""Migrations apply, roll back, and re-apply cleanly on a fresh database."""

from sqlalchemy import text

from agentforge.db.engine import create_sync_engine
from agentforge.db.lint import find_rls_violations
from agentforge.db.migrate import downgrade, seed_defaults, upgrade

from .conftest import Database


def _schemas(url: str) -> set[str]:
    engine = create_sync_engine(url)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text("SELECT nspname FROM pg_namespace WHERE nspname IN ('app', 'engine')")
                ).scalars()
            )
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_roundtrip(database: Database) -> None:
    admin = create_sync_engine(database.url("agentforge_owner"))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("DROP DATABASE IF EXISTS roundtrip"))
        connection.execute(text("CREATE DATABASE roundtrip"))
    admin.dispose()
    url = database.url("agentforge_owner", database="roundtrip")

    upgrade(url)
    assert _schemas(url) == {"app", "engine"}
    downgrade(url, "base")
    assert _schemas(url) == set()
    upgrade(url)
    seed_defaults(url)
    seed_defaults(url)  # idempotent

    engine = create_sync_engine(url)
    try:
        with engine.connect() as connection:
            assert find_rls_violations(connection) == []
    finally:
        engine.dispose()
