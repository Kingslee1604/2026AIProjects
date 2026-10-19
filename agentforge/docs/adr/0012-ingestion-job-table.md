# ADR-0012: Ingestion and answer jobs use a simple SKIP LOCKED job table, independent of the run engine

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Indexing a document and answering a single question are short, independent, retryable jobs.
They do not need the run engine's checkpoints, reviews or epochs (ADR-0002). They should keep
working if the engine is replaced, and they should not add a broker (ADR-0001).

## Decision

- Jobs live in the `queue` schema (`queue.ingestion_jobs`, `queue.answer_jobs`). Workers claim
  one with `FOR UPDATE SKIP LOCKED` and a **lease**. An expired lease makes the job claimable
  again, so a crashed worker's job is retried. Attempts are counted, and errors are classified
  (retry later, or fail with a message the user can read).
- Deleting a document cancels its queued jobs. Ingestion locks the document row before it writes
  chunks, so a delete during indexing leaves no orphans (R1-41).

## Consequences

- No broker, and job state is visible with plain SQL.
- Throughput is enough for a single machine. Very high job rates would need batching or
  partitioning.
- Two queueing mechanisms exist, the job table and the run engine, each used where it fits.
