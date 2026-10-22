# Architecture decision records

Each record states the context, the decision, and its consequences. A new decision gets the
next number. A changed decision gets a new record that supersedes the old one.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-modular-monolith-on-postgres.md) | Modular monolith on Postgres-only infrastructure | Accepted |
| [0002](0002-durable-execution.md) | Durable execution engine for runs: DBOS, chosen by conformance gates H1–H8 | Accepted |
| [0003](0003-shared-schema-tenancy-with-forced-rls.md) | Shared-schema multi-tenancy with forced RLS; engine schema holds IDs only | Accepted |
| [0004](0004-retrieval-inside-postgres.md) | Retrieval inside Postgres (pgvector HNSW + full-text + RRF) | Accepted |
| [0005](0005-thin-model-gateway.md) | Thin model gateway, schema-constrained output, no LangChain or LiteLLM | Accepted |
| [0006](0006-server-side-sessions.md) | Server-side sessions over browser JWTs | Accepted |
| [0007](0007-apache-2-no-agpl-runtime.md) | Apache-2.0; AGPL runtime dependencies excluded | Accepted |
| [0008](0008-embeddings-via-ollama.md) | Embeddings via Ollama; ML frameworks only in an optional image | Accepted |
| [0009](0009-public-demo-is-a-replay.md) | Public demo is a labelled replay; hosted live only behind the R2 gate | Accepted |
| [0010](0010-mcp-phased.md) | MCP in both directions, phased (read-only server in R1) | Accepted |
| [0011](0011-code-first-workflows.md) | Code-first workflows in R1 | Accepted |
| [0012](0012-ingestion-job-table.md) | Ingestion and answer jobs on a SKIP LOCKED table | Accepted |
| [0013](0013-lgpl-postgres-driver.md) | Allow the LGPL psycopg driver as a named exception | Accepted |
