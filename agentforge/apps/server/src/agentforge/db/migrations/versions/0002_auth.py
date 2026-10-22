"""Authentication: global identities, sessions, login attempts; org memberships with RLS.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13

`auth` holds global identity data (a person can belong to several organizations later), so it
is not tenant-scoped; only agentforge.auth reads it. Org membership is tenant data in `app`,
isolated by RLS, plus one extra policy: a signed-in user may see their own memberships.
"""

from alembic import op

from agentforge.db.rls import tenant_table_sql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in [
        "CREATE SCHEMA auth",
        "REVOKE ALL ON SCHEMA auth FROM PUBLIC",
        "GRANT USAGE ON SCHEMA auth TO agentforge_app",
        """
        CREATE FUNCTION app.current_user_id() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$
        """,
        "REVOKE ALL ON FUNCTION app.current_user_id() FROM PUBLIC",
        "GRANT EXECUTE ON FUNCTION app.current_user_id() TO agentforge_app",
        """
        CREATE TABLE auth.users (
            id            uuid PRIMARY KEY DEFAULT uuidv7(),
            email         text NOT NULL UNIQUE
                          CHECK (email = lower(email) AND email ~ '^[^@\\s]+@[^@\\s]+$'),
            password_hash text NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
            disabled_at   timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
        """,
        # Only a SHA-256 of the session token is stored, so a database leak yields no usable
        # session. The CSRF token is not a credential on its own (it needs the cookie too).
        """
        CREATE TABLE auth.sessions (
            token_hash   bytea PRIMARY KEY CHECK (length(token_hash) = 32),
            user_id      uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
            org_id       uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            csrf_token   text NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            expires_at   timestamptz NOT NULL,
            revoked_at   timestamptz,
            user_agent   text,
            ip           inet
        )
        """,
        "CREATE INDEX sessions_user_active ON auth.sessions (user_id) WHERE revoked_at IS NULL",
        """
        CREATE TABLE auth.login_attempts (
            id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            email      text NOT NULL,
            ip         inet,
            succeeded  boolean NOT NULL,
            at         timestamptz NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX login_attempts_email_at ON auth.login_attempts (email, at)",
        "CREATE INDEX login_attempts_ip_at ON auth.login_attempts (ip, at)",
        "GRANT SELECT, INSERT, UPDATE ON auth.users TO agentforge_app",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON auth.sessions TO agentforge_app",
        "GRANT SELECT, INSERT, DELETE ON auth.login_attempts TO agentforge_app",
        """
        CREATE TABLE app.memberships (
            org_id     uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            user_id    uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
            role       text NOT NULL CHECK (role IN ('admin', 'member')),
            can_review boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, user_id)
        )
        """,
        "CREATE INDEX memberships_user ON app.memberships (user_id)",
        *tenant_table_sql("memberships"),
        """
        CREATE POLICY own_memberships ON app.memberships FOR SELECT
        USING (user_id = app.current_user_id())
        """,
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE app.memberships")
    op.execute("DROP SCHEMA auth CASCADE")
    op.execute("DROP FUNCTION app.current_user_id()")
