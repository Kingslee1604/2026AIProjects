"""Foundation: app and engine schemas, tenant context, organizations, workspaces.

Revision ID: 0001
Revises:
Create Date: 2026-10-13
"""

from alembic import op

from agentforge.db.rls import tenant_table_sql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SLUG_CHECK = r"CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$')"


def upgrade() -> None:
    for statement in [
        # Tenant data lives in `app` (RLS enforced); the durable run engine gets `engine`,
        # which holds IDs and status only (architecture §7.4, gate H6).
        "CREATE SCHEMA app",
        "CREATE SCHEMA engine",
        "REVOKE ALL ON SCHEMA app FROM PUBLIC",
        "REVOKE ALL ON SCHEMA engine FROM PUBLIC",
        "GRANT USAGE ON SCHEMA app TO agentforge_app",
        "GRANT USAGE, CREATE ON SCHEMA engine TO agentforge_engine",
        # The current tenant, set per transaction by agentforge.db.tenancy.tenant_session.
        # Unset or empty means no tenant, so every RLS predicate is false.
        """
        CREATE FUNCTION app.current_org_id() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(current_setting('app.org_id', true), '')::uuid $$
        """,
        "REVOKE ALL ON FUNCTION app.current_org_id() FROM PUBLIC",
        "GRANT EXECUTE ON FUNCTION app.current_org_id() TO agentforge_app",
        f"""
        CREATE TABLE app.organizations (
            id         uuid PRIMARY KEY DEFAULT uuidv7(),
            name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
            slug       text NOT NULL UNIQUE {SLUG_CHECK},
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE app.workspaces (
            id         uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id     uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
            slug       text NOT NULL {SLUG_CHECK},
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, slug)
        )
        """,
        *tenant_table_sql("organizations", tenant_column="id"),
        *tenant_table_sql("workspaces"),
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP SCHEMA engine CASCADE")
    op.execute("DROP SCHEMA app CASCADE")
