# ADR-0002: Durable execution engine for runs

- **Status:** Proposed (awaiting owner approval)
- **Date:** 2026-10-14
- **Decision rule (architecture §7.1, RELEASE_1_PLAN R1-24):** choose the **simplest** candidate
  that passes **every** hard gate.
- **Evidence:** `spikes/engine/` (harness, adapters, tests) and raw results in
  `spikes/engine/results/<engine>.jsonl` (one JSON line per gate run).

## Context

Questionnaire runs (M4) are long: plan, then 40 items with model calls, then a human review
that can take days, then an export. They must survive worker crashes, never repeat completed
model calls, never export twice, pause without holding resources, and cancel promptly. Three
candidates were prototyped on the **same mini-flow** (plan → 20 items with a fake model, at
most 4 at a time per run → review wait → idempotent export) and checked by the **same
conformance tests** (architecture §7.3, H1–H8), run against a real Postgres 18 in Docker on
the reference laptop (Inspiron 15 3520, WSL2 capped at 4 GB).

The harness was first validated against a deliberately naive **stub** engine, which fails
H1–H5 and passes H6–H8, showing that the tests detect broken engines (R1-20 acceptance).

## Results (final code, full scale)

| Gate | DBOS 3.1.0 | LangGraph 1.2.12 + own queue | Custom (Postgres jobs) | Stub |
|---|---|---|---|---|
| H1 kill -9 ×10–12 during 50 runs | ✅ 50/50, 0 repeat calls | ✅ 50/50, 0 repeat calls | ✅ 50/50, 0 repeat calls | ❌ |
| H2 40 runs parked, restart, resume | ✅ no growth 20→40 | ✅ no growth 20→40 | ✅ no growth 20→40 | ❌ |
| H3 ≤ 4 concurrent items per run | ✅ peak 4 | ✅ peak 4 | ✅ peak 4 | ❌ peak 15 |
| H4 one export per epoch; retry → new | ✅ | ✅ | ✅ | ❌ |
| H5 no new step > 2 s after cancel | ✅ 0 | ✅ 0 | ✅ 0 | ❌ |
| H6 engine tables hold no content | ✅ 13 tables, 0 leaks | ✅ 5 tables, 0 leaks | ✅ 2 tables, 0 leaks | ✅ |
| H7 lost start recovered; no orphan | ✅ | ✅ | ✅ | ✅ |
| H8 exactly one event per step | ✅ | ✅ | ✅ | ✅ |
| H9 no paid/extra services | ✅ (Conductor not used) | ✅ (Platform not used) | ✅ | — |
| H10 licenses (gate run) | ✅ MIT | ✅ MIT | ✅ ours | — |

H2 and H5–H7 use scaled-down waits (15 s instead of 10 min); the recorded results say so.

## Scored criteria

| | DBOS | LangGraph | Custom |
|---|---|---|---|
| Adapter code for the mini-flow (non-blank, non-comment) | **150** | 270 (graph ≈ 80, own queue/recovery ≈ 190) | 232 |
| Fixes needed to pass | 2 config: cap the thread pool; async-only API inside async code | 2 behavioural: `durability="sync"`; restart from a checkpoint with nothing scheduled + verify progress against our own table | none |
| Silent-failure risk seen | none | **yes**: default async checkpointing left runs stopped mid-flow with no error | none seen |
| Recovery after a kill (time to next event, Measured) | 0.8–2.9 s | 0.8–1.9 s | 0.9–4.3 s |
| New third-party packages | ~5 (click, websockets, pyyaml, dateutil, six) | ~25 (langchain-core, langsmith, requests, httpx, …) | 0 |
| Concepts for contributors | workflow, step, queue partitions, send/recv, client, executor id | graph, reducers, Send, interrupt/Command, checkpointer, durability **+ our queue** | jobs, leases, heartbeats (ours) |
| Built-ins M4 needs later | cancel with children, timeouts, resume/fork/rewind (retry from step), queues | interrupt; the rest is ours | all ours |
| Multi-worker failover (R2+) | automatic only for the restarting worker's own executor id; another worker needs Conductor **or** our own liveness check + `recover_pending_workflows` | via our queue's leases | via leases |
| Fit with async FastAPI | async API; database calls run on its own thread pool | async | native asyncio |
| API stability risk | moderate (3.x renamed queue APIs) | high (fast-moving; defaults affect durability) | none external, but we own every durability bug |

Findings worth keeping:

1. **DBOS's default thread pool is unbounded** (`sys.maxsize`); it grew to 653 threads under a
   burst and never shrank. `max_executor_threads=32` fixes it (H2: 42 threads flat from 20 to
   40 parked runs). This becomes a required setting.
2. **H2 was re-measured.** The first version compared idle with waiting and so counted a
   normal pool size as pinning. The final version measures growth from 20 to 40 parked runs,
   which is what "held per waiting run" means. All three engines were re-run on it.
3. **LangGraph's default `durability="async"` can lose progress on a crash**: runs stopped with
   items 0–11 of 20 done and no error. With `"sync"`, a kill at the very start left a
   checkpoint with nothing scheduled. Passing H1 needed both fixes **and** a check of our own
   run table after every invocation, which is a second source of truth for progress.
4. DBOS stores step inputs and outputs with pickle in the `engine` schema. Our steps take and
   return IDs only (H6 confirms), and only the engine role can write that schema.

## Decision (proposed)

**Adopt DBOS Transact** as the run engine for Release 1.

It passes every hard gate with the least adapter code, needs no queue of our own, adds few
packages, and already provides what M4 needs next (cancel with children, retry from a step via
fork/rewind, timeouts). LangGraph is rejected: it needed the most code, the heaviest
dependency tree, and its failure mode under crashes was silent. The custom engine is the
documented fallback (it passed everything, with zero dependencies and the best multi-worker
story) if DBOS becomes unsuitable.

### Conditions

1. Configure `max_executor_threads` (32 on the reference machine) and a **stable executor id
   per worker** (Compose service name), so a restarted worker recovers its own runs.
2. DBOS tables stay in the `engine` schema, written only by `agentforge_engine`; steps keep
   taking and returning IDs only. The H6 content check runs in CI.
3. The conformance tests (H1–H8) move into CI against the DBOS adapter (M4, R1-30) and must pass
   before any DBOS upgrade. The 14-day release-age policy applies.
4. Before running more than one worker (R2), add a liveness check for dead executors and call
   `recover_pending_workflows`, or revisit the custom engine. DBOS Conductor (hosted) stays
   unused.
5. Keep the start reconciler (H7) or switch to `enqueue_in_transaction` when the run row and
   the enqueue can share a connection.

## Consequences

- M4 builds on DBOS workflows; no re-estimate of M4 is needed (the fallback's +15–25 h does not
  apply).
- LangGraph stays out of the dependency tree; its spike is kept for the record only.
- The `engine-spikes` dependency group is replaced by DBOS as a product dependency in M4.
