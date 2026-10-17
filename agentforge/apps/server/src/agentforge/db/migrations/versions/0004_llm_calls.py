"""Model calls: one row per provider call (prompt, response, tokens, latency, outcome).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-13

Prompts and responses contain tenant data, so the table is RLS-isolated like any other
(architecture §13.1). The run/step foreign keys arrive with the run tables (R1-30).
"""

from alembic import op

from agentforge.db.rls import tenant_table_sql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in [
        """
        CREATE TABLE app.llm_calls (
            id                uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id            uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            run_id            uuid,
            purpose           text NOT NULL,
            model             text NOT NULL,
            model_digest      text,
            status            text NOT NULL CHECK (status IN (
                                'ok', 'invalid_output', 'timeout', 'unavailable', 'error'
                              )),
            attempt           int NOT NULL CHECK (attempt >= 1),
            latency_ms        double precision NOT NULL,
            prompt_tokens     int,
            completion_tokens int,
            error_class       text,
            request           jsonb NOT NULL DEFAULT '[]'::jsonb,
            response          text,
            json_schema       jsonb,
            trace_id          text,
            created_at        timestamptz NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX llm_calls_org_created ON app.llm_calls (org_id, created_at DESC)",
        "CREATE INDEX llm_calls_run ON app.llm_calls (run_id) WHERE run_id IS NOT NULL",
        *tenant_table_sql("llm_calls"),
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE app.llm_calls")
