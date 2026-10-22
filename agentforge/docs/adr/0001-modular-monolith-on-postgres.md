# ADR-0001: A modular monolith on Postgres-only infrastructure

- **Status:** Accepted
- **Date:** 2026-10-12 (architecture v0.2 §22)

## Context

AgentForge has to run on one CPU-only laptop alongside a local model (architecture §23.1:
16 GB of RAM, WSL2 capped at 4 GB). It is built by one person, and it must still show
production habits: tenant isolation, durable runs, audit, and tests. Every extra service, such
as a message broker, vector database, search engine, cache or workflow server, costs memory,
setup steps and failure modes.

## Decision

- **One Python package** (`agentforge`) deployed as two processes from one image: the **API**
  (FastAPI) and the **worker** (ingestion, answers, and the run engine). The web app (Next.js)
  is the third container.
- **Postgres is the only infrastructure.** It holds relational data (with RLS), vectors
  (pgvector), full-text search, job queues (ADR-0012), run-engine state (ADR-0002), the event
  stream (`LISTEN/NOTIFY` plus `run_events`), and the audit log. Files go to a blob directory.
- **Module boundaries are enforced in CI**, not by network calls. `import-linter` contracts in
  `pyproject.toml` say, for example, that `core` and `db` never import domain modules, that
  domain modules never import `api`, `worker` or FastAPI, and that `knowledge` and `models`
  sit below `agents` and `qa`.

## Consequences

- `make up` starts four containers: Postgres, API, worker and web. Idle, they used about
  420 MB together (`docker stats`, 2026-10-19; R1-49 measures under load).
- Modules can later be split into services along the enforced boundaries if load demands it.
  Nothing in R1 needs that.
- Postgres is a single point of failure, which is acceptable for a self-hosted single-node
  release. Backups are the operator's job (deployment guide).
- Scaling the API to several processes needs shared rate-limit state; R1's limits are per
  process (threat model T9).
