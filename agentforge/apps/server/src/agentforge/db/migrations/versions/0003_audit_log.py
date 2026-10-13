"""Append-only audit log of security-relevant actions (FR-15, baseline S13).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13

The app role may INSERT and SELECT only: no UPDATE, no DELETE, so the application cannot
rewrite history. Events before sign-in (failed logins) have no organization (org_id NULL);
they are insertable but visible only to the schema owner.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in [
        """
        CREATE TABLE app.audit_log (
            id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            org_id        uuid REFERENCES app.organizations (id) ON DELETE SET NULL,
            at            timestamptz NOT NULL DEFAULT now(),
            actor_user_id uuid,
            actor_email   text,
            action        text NOT NULL CHECK (action ~ '^[a-z_]+(\\.[a-z_]+)+$'),
            target        text,
            outcome       text NOT NULL CHECK (outcome IN ('success', 'failure', 'denied')),
            ip            inet,
            trace_id      text,
            details       jsonb NOT NULL DEFAULT '{}'::jsonb
        )
        """,
        "CREATE INDEX audit_log_org_at ON app.audit_log (org_id, at DESC)",
        "ALTER TABLE app.audit_log ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE app.audit_log FORCE ROW LEVEL SECURITY",
        """
        CREATE POLICY tenant_read ON app.audit_log FOR SELECT
        USING (org_id = app.current_org_id())
        """,
        """
        CREATE POLICY tenant_append ON app.audit_log FOR INSERT
        WITH CHECK (org_id IS NULL OR org_id = app.current_org_id())
        """,
        "GRANT SELECT, INSERT ON app.audit_log TO agentforge_app",
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE app.audit_log")
