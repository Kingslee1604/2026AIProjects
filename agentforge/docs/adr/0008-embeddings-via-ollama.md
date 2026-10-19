# ADR-0008: Embeddings via Ollama; torch only in an optional worker-ml image

- **Status:** Accepted
- **Date:** 2026-10-12

## Context

Computing embeddings in-process means PyTorch or ONNX in the image: hundreds of MB to several GB,
slower builds, and more CVE surface. The machine already runs Ollama for chat, and Ollama serves
embedding models too.

## Decision

- Embeddings go through the model gateway to **Ollama** (`AGENTFORGE_EMBEDDING_MODEL`, default
  `ollama:nomic-embed-text:v1.5`). The server image contains no ML framework.
- Anything that truly needs local ML libraries, such as rerankers or OCR, goes into a
  **separate, optional `worker-ml` image** that the default stack does not pull. None is
  needed in R1, and none is built.
- The fake provider produces deterministic bag-of-words embeddings, so CI needs no model.

## Consequences

- The server image stays small, and its scans stay clean.
- Ingestion speed depends on Ollama's embedding throughput on the machine (R1-49 measures it on
  the reference laptop).
- Changing the embedding model changes vector dimensions. Each dimension has its own table, and
  re-indexing is required.
