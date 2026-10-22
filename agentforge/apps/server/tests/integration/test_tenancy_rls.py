"""Tenant isolation is enforced by Postgres itself (NFR-01, security baseline S4)."""

from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentforge.db.engine import create_app_engine, create_sessionmaker, create_sync_engine
from agentforge.db.lint import find_rls_violations
from agentforge.db.migrate import DEFAULT_ORG_ID
from agentforge.db.tenancy import TenantContext, tenant_session

from .conftest import Database

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def owner(database: Database) -> Iterator[Engine]:
    engine = create_sync_engine(database.url("agentforge_owner"))
    yield engine
    engine.dispose()


@pytest.fixture
async def app_sessions(database: Database) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # pool_size=1 forces every session onto the same connection, which is what makes the
    # "context does not leak between transactions" test meaningful.
    engine = create_app_engine(database.url("agentforge_app"), pool_size=1)
    yield create_sessionmaker(engine)
    await engine.dispose()


def make_org(owner: Engine, slug: str) -> UUID:
    """Create an org with one workspace, as the owner, under that org's own tenant context."""
    org_id = uuid4()
    with owner.begin() as connection:
        connection.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})
        connection.execute(
            text("INSERT INTO app.organizations (id, name, slug) VALUES (:id, :slug, :slug)"),
            {"id": org_id, "slug": slug},
        )
        connection.execute(
            text("INSERT INTO app.workspaces (org_id, name, slug) VALUES (:id, 'main', 'main')"),
            {"id": org_id},
        )
    return org_id


def test_schema_lint_passes_on_migrated_database(owner: Engine) -> None:
    with owner.connect() as connection:
        assert find_rls_violations(connection) == []


def test_lint_catches_a_table_without_rls(owner: Engine) -> None:
    with owner.connect() as connection, connection.begin() as transaction:
        connection.execute(text("CREATE TABLE app.unprotected (id uuid, org_id uuid)"))
        violations = find_rls_violations(connection)
        transaction.rollback()
    assert "app.unprotected: row level security not enabled" in violations
    assert "app.unprotected: row level security not forced" in violations
    assert "app.unprotected: no RLS policy" in violations


def test_lint_catches_a_table_without_tenant_column(owner: Engine) -> None:
    with owner.connect() as connection, connection.begin() as transaction:
        connection.execute(text("CREATE TABLE app.global_thing (id uuid)"))
        violations = find_rls_violations(connection)
        transaction.rollback()
    assert "app.global_thing: no tenant column 'org_id'" in violations


async def test_without_tenant_context_the_app_sees_zero_rows(
    owner: Engine, app_sessions: async_sessionmaker[AsyncSession]
) -> None:
    with owner.connect() as connection:
        connection.execute(
            text("SELECT set_config('app.org_id', :o, false)"), {"o": str(DEFAULT_ORG_ID)}
        )
        assert connection.execute(text("SELECT count(*) FROM app.workspaces")).scalar_one() >= 1

    async with app_sessions() as session, session.begin():
        for table in ("app.organizations", "app.workspaces"):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, f"{table} leaked {count} rows without a tenant context"


async def test_tenant_context_sees_only_its_own_rows(
    owner: Engine, app_sessions: async_sessionmaker[AsyncSession]
) -> None:
    org_a = make_org(owner, f"org-a-{uuid4().hex[:8]}")
    org_b = make_org(owner, f"org-b-{uuid4().hex[:8]}")

    async with tenant_session(app_sessions, TenantContext(org_a)) as session:
        orgs = (await session.execute(text("SELECT id FROM app.organizations"))).scalars().all()
        workspace_orgs = set(
            (await session.execute(text("SELECT org_id FROM app.workspaces"))).scalars().all()
        )
    assert orgs == [org_a]
    assert workspace_orgs == {org_a}
    assert org_b not in workspace_orgs


async def test_writing_into_another_tenant_is_rejected(
    owner: Engine, app_sessions: async_sessionmaker[AsyncSession]
) -> None:
    org_a = make_org(owner, f"org-a-{uuid4().hex[:8]}")
    org_b = make_org(owner, f"org-b-{uuid4().hex[:8]}")

    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(app_sessions, TenantContext(org_a)) as session:
            await session.execute(
                text("INSERT INTO app.workspaces (org_id, name, slug) VALUES (:b, 'x', 'sneaky')"),
                {"b": org_b},
            )

    async with tenant_session(app_sessions, TenantContext(org_a)) as session:
        await session.execute(
            text("UPDATE app.workspaces SET name = 'hijacked' WHERE org_id = :b"), {"b": org_b}
        )
    # Invisible rows cannot be updated: org B's workspace is unchanged (checked as owner).
    with owner.connect() as connection:
        names = connection.execute(
            text("SELECT name FROM app.workspaces WHERE org_id = :b"), {"b": org_b}
        ).scalars()
        assert list(names) == ["main"]


async def test_tenant_context_does_not_leak_into_the_next_transaction(
    owner: Engine, app_sessions: async_sessionmaker[AsyncSession]
) -> None:
    org_a = make_org(owner, f"org-a-{uuid4().hex[:8]}")
    async with tenant_session(app_sessions, TenantContext(org_a)) as session:
        assert (
            await session.execute(text("SELECT count(*) FROM app.workspaces"))
        ).scalar_one() == 1

    # Same pooled connection (pool_size=1), new transaction, no context set.
    async with app_sessions() as session, session.begin():
        assert (
            await session.execute(text("SELECT count(*) FROM app.workspaces"))
        ).scalar_one() == 0


async def test_app_role_cannot_disable_rls_or_read_the_engine_schema(database: Database) -> None:
    engine = create_sync_engine(database.url("agentforge_app"))
    try:
        with pytest.raises(ProgrammingError, match="must be owner"), engine.begin() as connection:
            connection.execute(text("ALTER TABLE app.workspaces DISABLE ROW LEVEL SECURITY"))
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            engine.begin() as connection,
        ):
            connection.execute(text("CREATE TABLE engine.sneaky (id int)"))
    finally:
        engine.dispose()


def test_engine_role_cannot_read_tenant_data(database: Database) -> None:
    engine = create_sync_engine(database.url("agentforge_engine"))
    try:
        with (
            pytest.raises(ProgrammingError, match="permission denied"),
            engine.begin() as connection,
        ):
            connection.execute(text("SELECT count(*) FROM app.workspaces"))
    finally:
        engine.dispose()
