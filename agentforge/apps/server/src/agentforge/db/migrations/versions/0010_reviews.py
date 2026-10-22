"""Per-item review (R1-34): one review batch per review round of a run, one review item per run
item with its decision.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-15

Completing a batch is a single conditional UPDATE (open -> completed), so the engine is
signalled exactly once per batch even if two reviewers click at the same moment.
"""

from alembic import op

from agentforge.db.rls import tenant_table_sql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in [
        """
        CREATE TABLE app.review_batches (
            id            uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id        uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            run_id        uuid NOT NULL REFERENCES app.runs (id) ON DELETE CASCADE,
            epoch         int NOT NULL,
            status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed')),
            created_at    timestamptz NOT NULL DEFAULT now(),
            completed_at  timestamptz,
            completed_by  uuid,
            UNIQUE (run_id, epoch)
        )
        """,
        *tenant_table_sql("review_batches"),
        """
        CREATE TABLE app.review_items (
            id                     uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id                 uuid NOT NULL REFERENCES app.organizations (id)
                                   ON DELETE CASCADE,
            batch_id               uuid NOT NULL REFERENCES app.review_batches (id)
                                   ON DELETE CASCADE,
            run_item_id            uuid NOT NULL REFERENCES app.run_items (id) ON DELETE CASCADE,
            item_index             int NOT NULL,
            auto_accept_candidate  bool NOT NULL DEFAULT false,
            decision               text CHECK (decision IN ('approved', 'edited', 'rejected')),
            edited_answer          text CHECK (length(edited_answer) <= 4000),
            reason                 text CHECK (length(reason) <= 1000),
            decided_by             uuid,
            decided_at             timestamptz,
            UNIQUE (batch_id, item_index),
            CHECK (decision <> 'edited' OR edited_answer IS NOT NULL),
            CHECK (decision <> 'rejected' OR reason IS NOT NULL)
        )
        """,
        "CREATE INDEX review_items_run_item ON app.review_items (run_item_id)",
        *tenant_table_sql("review_items"),
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE app.review_items")
    op.execute("DROP TABLE app.review_batches")
