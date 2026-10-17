"""App-side tables for the mini-flow, in their own `conformance` schema (created by the test
setup, not by product migrations; M4 task R1-30 designs the real run tables).

They follow the product rules: tenant rows carry org_id and are RLS-isolated with the same
app.current_org_id() policy, and content (item results, artifacts) lives only here, never in
engine-owned tables (gate H6).
"""

APP_ROLE = "agentforge_app"
ENGINE_ROLE = "agentforge_engine"

_TENANT_TABLES = ["runs", "run_items", "llm_log", "run_events", "artifacts", "export_log"]

STATEMENTS = [
    "DROP SCHEMA IF EXISTS conformance CASCADE",
    "CREATE SCHEMA conformance",
    f"GRANT USAGE ON SCHEMA conformance TO {APP_ROLE}",
    """
    CREATE TABLE conformance.runs (
        id               uuid PRIMARY KEY,
        org_id           uuid NOT NULL,
        status           text NOT NULL DEFAULT 'queued' CHECK (status IN (
                           'queued', 'running', 'waiting_review', 'succeeded', 'failed',
                           'cancelled')),
        epoch            int NOT NULL DEFAULT 1,
        review_decision  text,
        item_count       int NOT NULL DEFAULT 20,
        max_parallelism  int NOT NULL DEFAULT 4,
        cancelled_at     timestamptz,
        created_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at       timestamptz NOT NULL DEFAULT clock_timestamp()
    )
    """,
    """
    CREATE TABLE conformance.run_items (
        run_id        uuid NOT NULL REFERENCES conformance.runs (id) ON DELETE CASCADE,
        idx           int NOT NULL,
        org_id        uuid NOT NULL,
        status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done')),
        result        text,
        llm_call_id   bigint,
        completed_at  timestamptz,
        PRIMARY KEY (run_id, idx)
    )
    """,
    # One row per fake model call, committed before the call "returns": evidence for H1 (no
    # call for an item after it completed) and H3 (concurrency from overlapping intervals).
    """
    CREATE TABLE conformance.llm_log (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        org_id       uuid NOT NULL,
        run_id       uuid NOT NULL,
        idx          int NOT NULL,
        worker_pid   int NOT NULL,
        started_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
        finished_at  timestamptz
    )
    """,
    """
    CREATE TABLE conformance.run_events (
        seq         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        org_id      uuid NOT NULL,
        run_id      uuid NOT NULL,
        type        text NOT NULL,
        node        text,
        idx         int,
        epoch       int,
        created_at  timestamptz NOT NULL DEFAULT clock_timestamp()
    )
    """,
    """
    CREATE TABLE conformance.artifacts (
        id          uuid PRIMARY KEY,
        org_id      uuid NOT NULL,
        run_id      uuid NOT NULL,
        node        text NOT NULL,
        epoch       int NOT NULL,
        content     text NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (run_id, node, epoch)
    )
    """,
    # Every execution of the export side effect, kept even when it turns out to be a repeat.
    """
    CREATE TABLE conformance.export_log (
        id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        org_id      uuid NOT NULL,
        run_id      uuid NOT NULL,
        epoch       int NOT NULL,
        created     bool NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT clock_timestamp()
    )
    """,
]

for _table in _TENANT_TABLES:
    STATEMENTS += [
        f"ALTER TABLE conformance.{_table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE conformance.{_table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON conformance.{_table} "
        "USING (org_id = app.current_org_id()) WITH CHECK (org_id = app.current_org_id())",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON conformance.{_table} TO {APP_ROLE}",
    ]

# The worker's reconciler (gate H7) lists queued runs across tenants. That is the one
# cross-tenant read, done through a SECURITY DEFINER function returning IDs only.
STATEMENTS += [
    """
    CREATE FUNCTION conformance.stale_queued_runs(older_than interval)
    RETURNS TABLE (org_id uuid, run_id uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
        SELECT r.org_id, r.id FROM conformance.runs r
        WHERE r.status = 'queued' AND r.created_at < clock_timestamp() - older_than
    $$
    """,
    "REVOKE ALL ON FUNCTION conformance.stale_queued_runs(interval) FROM PUBLIC",
    f"GRANT EXECUTE ON FUNCTION conformance.stale_queued_runs(interval) TO {APP_ROLE}",
]

# Reset the engine schema too, so every engine starts from an empty schema.
RESET_ENGINE_SCHEMA = [
    "DROP SCHEMA IF EXISTS engine CASCADE",
    "CREATE SCHEMA engine",
    "REVOKE ALL ON SCHEMA engine FROM PUBLIC",
    f"GRANT USAGE, CREATE ON SCHEMA engine TO {ENGINE_ROLE}",
]
