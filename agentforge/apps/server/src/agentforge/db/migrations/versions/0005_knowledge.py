"""Knowledge bases, documents, chunks, embeddings, and the ingestion queue.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13

- Tenant content (documents, normalized text, chunks, embeddings) lives in `app` with RLS.
- The ingestion queue lives in `queue` and holds only IDs and status, so a worker can claim
  jobs across tenants without reading tenant content (same principle as the `engine` schema,
  architecture §7.4; ADR-12). The worker then processes each job under that job's tenant.
- One embedding table per embedding model (fixed dimension; HNSW needs it). Changing the
  model means a new table and a reindex, never mixed vectors (architecture §11.1).
"""

from alembic import op

from agentforge.db.rls import tenant_table_sql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# model key -> dimensions. Keys must match agentforge.knowledge.embeddings.EMBEDDING_TABLES.
EMBEDDING_TABLES = {"nomic_v15": 768, "fake256": 256}


def upgrade() -> None:
    statements = [
        # Usually created by the database init script (it needs elevated rights); kept here so
        # a fresh database also migrates. A no-op when the extension already exists.
        "CREATE EXTENSION IF NOT EXISTS vector",
        """
        CREATE TABLE app.knowledge_bases (
            id           uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id       uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            workspace_id uuid NOT NULL REFERENCES app.workspaces (id) ON DELETE CASCADE,
            name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
            description  text NOT NULL DEFAULT '',
            created_by   uuid,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, name)
        )
        """,
        *tenant_table_sql("knowledge_bases"),
        """
        CREATE TABLE app.documents (
            id                uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id            uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            knowledge_base_id uuid NOT NULL REFERENCES app.knowledge_bases (id) ON DELETE CASCADE,
            filename          text NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
            media_type        text NOT NULL,
            size_bytes        bigint NOT NULL CHECK (size_bytes > 0),
            sha256            text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            blob_key          text NOT NULL,
            status            text NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
            error             text,
            page_count        int,
            chunk_count       int,
            normalized_text   text,
            page_map          jsonb,
            uploaded_by       uuid,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (knowledge_base_id, sha256)
        )
        """,
        "CREATE INDEX documents_kb ON app.documents (knowledge_base_id, created_at DESC)",
        *tenant_table_sql("documents"),
        """
        CREATE TABLE app.chunks (
            id                uuid PRIMARY KEY DEFAULT uuidv7(),
            org_id            uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
            knowledge_base_id uuid NOT NULL REFERENCES app.knowledge_bases (id) ON DELETE CASCADE,
            document_id       uuid NOT NULL REFERENCES app.documents (id) ON DELETE CASCADE,
            ordinal           int NOT NULL,
            content           text NOT NULL,
            page_start        int NOT NULL,
            page_end          int NOT NULL,
            char_start        int NOT NULL,
            char_end          int NOT NULL CHECK (char_end > char_start),
            section_path      jsonb NOT NULL DEFAULT '[]'::jsonb,
            token_estimate    int NOT NULL,
            fts               tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            UNIQUE (document_id, ordinal)
        )
        """,
        "CREATE INDEX chunks_fts ON app.chunks USING gin (fts)",
        "CREATE INDEX chunks_kb ON app.chunks (knowledge_base_id)",
        *tenant_table_sql("chunks"),
        # The queue: IDs and status only. agentforge_app may claim any tenant's job.
        "CREATE SCHEMA queue",
        "REVOKE ALL ON SCHEMA queue FROM PUBLIC",
        "GRANT USAGE ON SCHEMA queue TO agentforge_app",
        """
        CREATE TABLE queue.ingestion_jobs (
            id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            org_id           uuid NOT NULL,
            document_id      uuid NOT NULL,
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
        CREATE INDEX ingestion_jobs_ready ON queue.ingestion_jobs (run_after)
        WHERE status IN ('queued', 'running')
        """,
        "GRANT SELECT, INSERT, UPDATE, DELETE ON queue.ingestion_jobs TO agentforge_app",
    ]
    for key, dims in EMBEDDING_TABLES.items():
        table = f"chunk_embeddings_{key}"
        statements += [
            f"""
            CREATE TABLE app.{table} (
                chunk_id          uuid PRIMARY KEY REFERENCES app.chunks (id) ON DELETE CASCADE,
                org_id            uuid NOT NULL REFERENCES app.organizations (id) ON DELETE CASCADE,
                knowledge_base_id uuid NOT NULL,
                embedding         vector({dims}) NOT NULL
            )
            """,
            f"CREATE INDEX {table}_hnsw ON app.{table} USING hnsw (embedding vector_cosine_ops)",
            f"CREATE INDEX {table}_kb ON app.{table} (knowledge_base_id)",
            *tenant_table_sql(table),
        ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    for key in EMBEDDING_TABLES:
        op.execute(f"DROP TABLE app.chunk_embeddings_{key}")
    op.execute("DROP SCHEMA queue CASCADE")
    op.execute("DROP TABLE app.chunks")
    op.execute("DROP TABLE app.documents")
    op.execute("DROP TABLE app.knowledge_bases")
