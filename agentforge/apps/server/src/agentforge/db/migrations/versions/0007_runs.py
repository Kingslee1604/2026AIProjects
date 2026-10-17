"""Runs (questionnaire workflows): runs, run items, step records, run events, artifacts.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14

Content (questions, answers, claims, evidence) lives here under RLS; the run engine (DBOS,
ADR-0002) keeps only IDs and status in the `engine` schema.

- app.run_events is append-only for the app role (INSERT/SELECT) and the source of the live
  trace. Each insert sends pg_notify('run_events', '<run_id>:<seq>'): IDs only, never content;
  listeners re-read the event under RLS.
- app.artifacts.idempotency_key = H(run, node, epoch) is unique, so an export can never produce
  two artifacts for one epoch (gate H4).
- app.llm_calls gains step_id so a step in the trace view can show its model calls.
"""

from alembic import op

from agentforge.db.rls import APP_ROLE, tenant_table_sql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

RUN_STATUSES = (
    "'queued', 'running', 'waiting_review', 'succeeded', 'failed', 'cancelled', 'timed_out'"
)


def upgrade() -> None:
    for statement in [
        f"""
        CREATE TABLE app.runs (
            id              uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id          uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            created_by      uuid,
            template_key    text NOT NULL CHECK (template_key ~ '^[a-z][a-z0-9_]{{1,62}}$'),
            title           text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
            status          text NOT NULL DEFAULT 'queued' CHECK (status IN ({RUN_STATUSES})),
            input           jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            budgets         jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            usage           jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            epoch           int NOT NULL DEFAULT 1 CHECK (epoch >= 1),
            error_class     text,
            error           text,
            item_count      int NOT NULL DEFAULT 0,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            started_at      timestamptz,
            finished_at     timestamptz
        )
        """,
        "CREATE INDEX runs_org_created ON app.runs (org_id, created_at DESC)",
        *tenant_table_sql("runs"),
        """
        CREATE TABLE app.run_items (
            id              uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id          uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            run_id          uuid NOT NULL REFERENCES app.runs (id) ON DELETE CASCADE,
            item_index      int NOT NULL CHECK (item_index >= 0),
            source_ref      text,
            section         text,
            question        text NOT NULL CHECK (length(question) BETWEEN 1 AND 4000),
            status          text NOT NULL DEFAULT 'pending' CHECK (status IN (
                              'pending', 'answering', 'answered', 'insufficient_evidence',
                              'failed', 'skipped')),
            answer          text,
            confidence      text CHECK (confidence IN ('high', 'medium', 'low')),
            reason          text,
            claims          jsonb NOT NULL DEFAULT '[]'::jsonb,
            evidence        jsonb NOT NULL DEFAULT '[]'::jsonb,
            agent_versions  jsonb NOT NULL DEFAULT '{}'::jsonb,
            timings         jsonb NOT NULL DEFAULT '{}'::jsonb,
            completed_at    timestamptz,
            UNIQUE (run_id, item_index)
        )
        """,
        *tenant_table_sql("run_items"),
        """
        CREATE TABLE app.step_records (
            id              uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id          uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            run_id          uuid NOT NULL REFERENCES app.runs (id) ON DELETE CASCADE,
            node_id         text NOT NULL,
            item_index      int,
            epoch           int NOT NULL DEFAULT 1,
            attempt         int NOT NULL DEFAULT 1,
            status          text NOT NULL DEFAULT 'running' CHECK (status IN (
                              'running', 'succeeded', 'failed', 'cancelled')),
            error_class     text,
            error           text,
            started_at      timestamptz NOT NULL DEFAULT now(),
            finished_at     timestamptz
        )
        """,
        "CREATE INDEX step_records_run ON app.step_records (run_id, started_at)",
        *tenant_table_sql("step_records"),
        """
        CREATE TABLE app.run_events (
            seq         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            org_id      uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            run_id      uuid NOT NULL REFERENCES app.runs (id) ON DELETE CASCADE,
            type        text NOT NULL,
            node_id     text,
            item_index  int,
            epoch       int,
            step_id     uuid,
            data        jsonb NOT NULL DEFAULT '{}'::jsonb,
            trace_id    text,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX run_events_run_seq ON app.run_events (run_id, seq)",
        "ALTER TABLE app.run_events ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE app.run_events FORCE ROW LEVEL SECURITY",
        """
        CREATE POLICY tenant_isolation ON app.run_events
        USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id())
        """,
        f"GRANT SELECT, INSERT ON app.run_events TO {APP_ROLE}",
        """
        CREATE FUNCTION app.notify_run_event() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            PERFORM pg_notify('run_events', NEW.run_id::text || ':' || NEW.seq::text);
            RETURN NULL;
        END
        $$
        """,
        """
        CREATE TRIGGER run_events_notify AFTER INSERT ON app.run_events
        FOR EACH ROW EXECUTE FUNCTION app.notify_run_event()
        """,
        """
        CREATE TABLE app.artifacts (
            id               uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id           uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            run_id           uuid NOT NULL REFERENCES app.runs (id) ON DELETE CASCADE,
            node_id          text NOT NULL,
            epoch            int NOT NULL,
            idempotency_key  text NOT NULL UNIQUE,
            blob_key         text NOT NULL,
            filename         text NOT NULL,
            media_type       text NOT NULL,
            size_bytes       bigint NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
        """,
        *tenant_table_sql("artifacts"),
        # The worker's start reconciler (gate H7) finds runs whose start was lost in a crash.
        # It is the one cross-tenant read, so it is a narrow SECURITY DEFINER function that
        # returns IDs only.
        """
        CREATE FUNCTION app.stale_queued_runs(older_than interval)
        RETURNS TABLE (org_id uuid, run_id uuid, template_key text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
            SELECT r.org_id, r.id, r.template_key FROM app.runs r
            WHERE r.status = 'queued' AND r.created_at < now() - older_than
            ORDER BY r.created_at LIMIT 100
        $$
        """,
        "REVOKE ALL ON FUNCTION app.stale_queued_runs(interval) FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION app.stale_queued_runs(interval) TO {APP_ROLE}",
        "ALTER TABLE app.llm_calls ADD COLUMN step_id uuid",
        "CREATE INDEX llm_calls_step ON app.llm_calls (step_id) WHERE step_id IS NOT NULL",
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX app.llm_calls_step")
    op.execute("ALTER TABLE app.llm_calls DROP COLUMN step_id")
    op.execute("DROP FUNCTION app.stale_queued_runs(interval)")
    for table in ("artifacts", "run_events", "step_records", "run_items", "runs"):
        op.execute(f"DROP TABLE app.{table}")
    op.execute("DROP FUNCTION app.notify_run_event()")
