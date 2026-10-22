# ADR-0004: Retrieval inside Postgres (pgvector HNSW, full-text search, RRF) behind a Retriever interface

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Grounded answers need good recall on short, exact facts (retention periods, key lengths) and on
paraphrased questions. Vector search alone misses exact terms; keyword search alone misses
paraphrases. A separate vector database or search engine would add a service (ADR-0001), and
its tenant filter would sit outside RLS (ADR-0003).

## Decision

- Chunks live in `app.chunks` with an embedding in a per-dimension table. An **HNSW** index
  (`vector_cosine_ops`) is queried with filtered iterative scan, so tenant and knowledge-base
  filters do not starve the result.
- A **full-text** query runs over the same chunks. User text is turned into an OR query of
  words that cannot inject query syntax (`test_or_query_matches_any_word_and_cannot_inject_syntax`).
- The two rankings are fused with **reciprocal rank fusion** (RRF), so agreement between them
  wins (`test_rrf_rewards_agreement_between_channels`).
- Callers use a `Retriever` interface, so another backend can be swapped in without touching
  the QA pipeline.

## Consequences

- Retrieval inherits RLS and transactions, with no second copy of tenant data.
- **Measured** (R1-40, Northwind corpus, `nomic-embed-text:v1.5`): recall@5 100%, MRR 0.94,
  and the HNSW top 10 equals an exact scan. Retrieval is not the bottleneck at demo scale.
- Very large corpora, millions of chunks, may need tuning (`ef_search`, partitioning) or
  another backend behind the interface. Not needed for R1.
