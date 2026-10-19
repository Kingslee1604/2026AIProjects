"""Workspace API keys (R1-42, FR-17): prefix + hashed secret, scopes, expiry, last use, revoke.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-18

Like sessions, keys live in the auth schema: a request presents a key before its organization
is known, so the lookup is by prefix, not under RLS. Every listing and revocation filters by
organization explicitly (tested). Only a SHA-256 of the full key is stored; keys are 256-bit
random secrets, so a slow password hash would add nothing.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in [
        """
        CREATE TABLE auth.api_keys (
            id            uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id        uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            workspace_id  uuid NOT NULL REFERENCES app.workspaces (id) ON DELETE CASCADE,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
            prefix        text NOT NULL UNIQUE,
            secret_hash   bytea NOT NULL,
            scopes        text[] NOT NULL CHECK (
                            cardinality(scopes) > 0
                            AND scopes <@ ARRAY['api:read', 'api:write', 'mcp:use']),
            created_by    uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
            created_at    timestamptz NOT NULL DEFAULT now(),
            expires_at    timestamptz NOT NULL,
            last_used_at  timestamptz,
            revoked_at    timestamptz,
            revoked_by    uuid REFERENCES auth.users (id) ON DELETE SET NULL
        )
        """,
        "CREATE INDEX api_keys_org ON auth.api_keys (org_id, created_at DESC)",
        "GRANT SELECT, INSERT, UPDATE ON auth.api_keys TO agentforge_app",
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE auth.api_keys")
