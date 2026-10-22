"""Document risk flags: what the ingestion screening found in a document's text (for example,
instructions addressed to an AI). Answers that cite a flagged document are marked for review.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-16
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.documents ADD COLUMN risk_flags jsonb NOT NULL DEFAULT '[]'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE app.documents DROP COLUMN risk_flags")
