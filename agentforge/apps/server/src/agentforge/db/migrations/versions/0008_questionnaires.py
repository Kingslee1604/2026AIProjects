"""Uploaded questionnaires: the original file (as a blob, for the export) and its parsed items.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15
"""

from alembic import op

from agentforge.db.rls import tenant_table_sql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in [
        """
        CREATE TABLE app.questionnaires (
            id          uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id      uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            created_by  uuid,
            filename    text NOT NULL,
            media_type  text NOT NULL,
            size_bytes  bigint NOT NULL,
            sha256      text NOT NULL,
            blob_key    text NOT NULL,
            format      text NOT NULL CHECK (format IN ('xlsx', 'csv')),
            sheet       text,
            header_row  int NOT NULL,
            columns     jsonb NOT NULL,
            headers     jsonb NOT NULL DEFAULT '[]'::jsonb,
            items       jsonb NOT NULL,
            item_count  int NOT NULL CHECK (item_count BETWEEN 1 AND 500),
            warnings    jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX questionnaires_org_created ON app.questionnaires (org_id, created_at DESC)",
        *tenant_table_sql("questionnaires"),
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE app.questionnaires")
