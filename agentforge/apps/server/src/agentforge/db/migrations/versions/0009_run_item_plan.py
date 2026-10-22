"""Planner output per run item (R1-32): kind, search queries, and parts of compound questions.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15

Kept in the app table under RLS; the engine only ever sees item indexes.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.run_items ADD COLUMN plan jsonb NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE app.run_items DROP COLUMN plan")
