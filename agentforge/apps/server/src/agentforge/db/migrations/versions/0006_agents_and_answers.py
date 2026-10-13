"""Agent definitions (immutable versions) and grounded Q&A answers.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13

- app.agent_versions is append-only for the app role (INSERT/SELECT): a published version can
  never change, so a run always knows exactly which prompt and model produced its output.
- app.qa_answers stores each question, its status, the verified claims, and a snapshot of the
  evidence shown to the model (so citations stay viewable even after a document is reindexed).
- queue.answer_jobs: IDs only, like queue.ingestion_jobs; the worker answers questions so the
  API never runs long model calls inline.
"""

from alembic import op

from agentforge.db.rls import tenant_table_sql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in [
        """
        CREATE TABLE app.agents (
            id         uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id     uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            key        text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{1,62}$'),
            name       text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, key)
        )
        """,
        *tenant_table_sql("agents"),
        """
        CREATE TABLE app.agent_versions (
            id         uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id     uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            agent_id   uuid NOT NULL REFERENCES app.agents (id) ON DELETE CASCADE,
            version    int NOT NULL CHECK (version >= 1),
            checksum   text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
            definition jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (agent_id, version),
            UNIQUE (agent_id, checksum)
        )
        """,
        "ALTER TABLE app.agent_versions ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE app.agent_versions FORCE ROW LEVEL SECURITY",
        """
        CREATE POLICY tenant_isolation ON app.agent_versions
        USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id())
        """,
        "GRANT SELECT, INSERT ON app.agent_versions TO agentforge_app",
        """
        CREATE TABLE app.qa_answers (
            id                 uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id             uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            asked_by           uuid,
            question           text NOT NULL CHECK (length(question) BETWEEN 1 AND 2000),
            knowledge_base_ids uuid[] NOT NULL,
            status             text NOT NULL DEFAULT 'pending' CHECK (status IN (
                                 'pending', 'running', 'answered', 'insufficient_evidence',
                                 'failed')),
            answer             text,
            confidence         text CHECK (confidence IN ('high', 'medium', 'low')),
            reason             text,
            claims            jsonb NOT NULL DEFAULT '[]'::jsonb,
            evidence           jsonb NOT NULL DEFAULT '[]'::jsonb,
            agent_versions     jsonb NOT NULL DEFAULT '{}'::jsonb,
            error              text,
            timings            jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at         timestamptz NOT NULL DEFAULT now(),
            completed_at       timestamptz
        )
        """,
        "CREATE INDEX qa_answers_org_created ON app.qa_answers (org_id, created_at DESC)",
        *tenant_table_sql("qa_answers"),
        """
        CREATE TABLE queue.answer_jobs (
            id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            org_id           uuid NOT NULL,
            answer_id        uuid NOT NULL,
            status           text NOT NULL DEFAULT 'queued'
                             CHECK (status IN ('queued', 'running', 'done', 'failed')),
            attempts         int NOT NULL DEFAULT 0,
            run_after        timestamptz NOT NULL DEFAULT now(),
            lease_expires_at timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE INDEX answer_jobs_ready ON queue.answer_jobs (run_after)
        WHERE status IN ('queued', 'running')
        """,
        "GRANT SELECT, INSERT, UPDATE, DELETE ON queue.answer_jobs TO agentforge_app",
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE queue.answer_jobs")
    op.execute("DROP TABLE app.qa_answers")
    op.execute("DROP TABLE app.agent_versions")
    op.execute("DROP TABLE app.agents")
