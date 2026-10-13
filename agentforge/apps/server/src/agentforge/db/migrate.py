"""Run migrations and seed the default organization (`agentforge migrate`)."""

from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text

from agentforge.db.engine import create_sync_engine, sqlalchemy_url

# Release 1 has one organization and workspace in the UI (FR-02). A fixed ID makes seeding
# idempotent under RLS: the seed sets this tenant context before inserting.
DEFAULT_ORG_ID = UUID("01920000-0000-7000-8000-000000000001")
DEFAULT_ORG = ("Default Organization", "default")
DEFAULT_WORKSPACE = ("Default Workspace", "default")


def alembic_config(url: SecretStr | str) -> Config:
    config = Config()
    config.set_main_option("script_location", "agentforge.db:migrations")
    # ConfigParser interpolation would treat % in passwords specially.
    config.set_main_option("sqlalchemy.url", sqlalchemy_url(url).replace("%", "%%"))
    return config


def upgrade(url: SecretStr | str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: SecretStr | str, revision: str) -> None:
    command.downgrade(alembic_config(url), revision)


def seed_defaults(url: SecretStr | str) -> None:
    engine = create_sync_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(DEFAULT_ORG_ID)}
            )
            connection.execute(
                text(
                    "INSERT INTO app.organizations (id, name, slug) VALUES (:id, :name, :slug) "
                    "ON CONFLICT (slug) DO NOTHING"
                ),
                {"id": DEFAULT_ORG_ID, "name": DEFAULT_ORG[0], "slug": DEFAULT_ORG[1]},
            )
            connection.execute(
                text(
                    "INSERT INTO app.workspaces (org_id, name, slug) VALUES (:org, :name, :slug) "
                    "ON CONFLICT (org_id, slug) DO NOTHING"
                ),
                {"org": DEFAULT_ORG_ID, "name": DEFAULT_WORKSPACE[0], "slug": DEFAULT_WORKSPACE[1]},
            )
    finally:
        engine.dispose()
