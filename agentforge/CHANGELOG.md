# Changelog

All notable changes to AgentForge. Versions follow [Semantic Versioning](https://semver.org/);
before 1.0, minor versions may change behaviour.

## v0.1.0: Release 1 (2026-10-22)

The first complete release: a self-hosted platform for evidence-grounded, human-supervised AI
work over private documents. It runs on one machine with open-weight models (Ollama).

### What it does
- **Ask your documents:** upload PDF, Word, Markdown or text; every sentence of an answer quotes
  its source passage, and questions the documents don't answer get "insufficient evidence".
- **Questionnaire runs:** upload an Excel or CSV questionnaire; a durable background run
  answers every question (planner → drafter → verifier → relevance check), visible live as
  agent cards and a step trace; a reviewer approves, edits or rejects each answer with the
  keyboard; the answered spreadsheet is exported.
- **MCP server (read-only):** AI assistants with an API key can search the documents, get cited
  answers, and read runs.
- **Overview dashboard** built from real data: runs, answers to review, the share of sentences
  proven word for word, model use, model status, knowledge, activity.
- **Dark theme by default with a light option**; a keyboard-first and accessible UI (WCAG 2.2
  AA checks in CI, plus a manual keyboard, zoom and high-contrast pass).

### Engineering
- **Modular monolith on Postgres only:** forced row-level security per organization, pgvector
  HNSW plus full-text with RRF, SKIP LOCKED job queues, an append-only audit log.
- **Durable execution:** DBOS, chosen by prototyping three engines against eight conformance
  gates (ADR-0002); 50/50 runs succeed while the worker is killed 29 times with 10% model
  faults.
- **Model gateway:** JSON-schema output, a repair attempt, fallback models, every call recorded,
  and a deterministic fake provider for CI.
- **Security baseline (S1–S15):**
  - loopback-only ports;
  - sessions with CSRF protection, and scoped, revocable API keys;
  - upload hardening with a sandboxed parser;
  - rate limits;
  - no secrets in logs;
  - only the model endpoint as egress;
  - dependency and image scans with SBOMs.

  See [`docs/security/r1-baseline.md`](docs/security/r1-baseline.md).
- **Prompt injection:** 0 tool executions are possible and 0 injected instructions were
  followed; documents that address an AI are flagged and answers quoting them need review
  ([report](docs/security/prompt-injection.md)).

### Measured (CPU-only laptop, i7-1255U, 16 GB, no GPU)
- Retrieval recall@5 **100%**, MRR 0.94 on a 40-question gold set; 0 answerable questions
  refused.
- A 12-question run in **14.9 min**, 40 questions in **36.6 min**, with 0 model timeouts
  ([results](bench/results/2026-10-19-inspiron-3520-one-at-a-time/summary.md)).

### Known limits
- Built for one machine; public exposure needs the planned pre-public gate (R3).
- On a CPU, an answer takes about a minute.
- Questionnaire parsing is not process-isolated; rate limits are per API process; no
  screen-reader test yet. See the [threat model](docs/security/threat-model.md).

### Not in this release
- A public replay site (R1-47) and the demo video (R1-50).
