# ADR-0001: confluent-kafka as the Kafka client

- **Status:** Accepted
- **Date:** 2026-10-27
- **Evidence:** [`spikes/kafka_client/gates.py`](../../spikes/kafka_client/gates.py), raw results in
  [`bench/results/2026-10-27-Kingslee-kafka-client/`](../../bench/results/2026-10-27-Kingslee-kafka-client/)

## Context

Every service here (gateway, ledger, and the exactly-once totals to come) talks to Kafka. The
platform's central promise is that no usage record is lost or counted twice, so the client has
to get idempotent producing and transactions right on Kafka 4.x, not just be fast.

Two maintained, Apache-2.0 Python clients exist:

- **confluent-kafka** 2.16.0: a thin binding over librdkafka (C), maintained by Confluent.
- **aiokafka** 0.14.0: pure-Python asyncio client, maintained by the aio-libs community.

Both versions satisfy the 14-day supply-chain rule.

## Gates

Both clients ran the same script against the dev broker (`apache/kafka:4.3.1`, one KRaft node)
on an i7-1255U laptop (WSL2, 4 GB cap). Setup and verification used one reference reader, so
only the action under test differed.

| Gate | What it checks | confluent-kafka | aiokafka |
|---|---|---|---|
| G1 Kafka 4.3 | Round trip on a broker that dropped old protocol versions | ✅ | ✅ |
| G2 Broker kill | 50,000 numbered records at 2,000/s with idempotence on; broker `SIGKILL`ed a third of the way in (down ~16 s). Duplicates? Acked records missing? | ✅ 0 duplicates, 0 missing | ✅ 0 duplicates, 0 missing |
| G3 Transactions | Aborted batch invisible to `read_committed`; committed batch and input offset land together | ✅ | ✅ |
| G3 Zombie fencing | Offsets sent with consumer-group metadata (KIP-447), so a stale worker after a rebalance is rejected by the broker | ✅ `send_offsets_to_transaction(positions, group_metadata)` | ❌ group id only; `TxnOffsetCommit` v0 carries no member or generation |
| G4 Produce speed | 200,000 × 200 B, `acks=all`, idempotent | **168,281/s** (first run 179,643/s) | **51,949/s** |
| G4 Consume speed | Same 200,000 records | 354,063/s | 325,462/s |
| G5 Types | Ships type information | ✅ `.pyi` stubs | ❌ none |
| Peak RSS | Whole test process | 95 MiB | 81 MiB |

Speed numbers are single runs on a laptop: read them as "about 3× faster to produce", not as
exact figures.

## Decision

Use **confluent-kafka** for every service.

1. **Correctness is a tie on the basics, but not on fencing.** Both survive a broker kill with
   no loss and no duplicates, and both run transactions. Only confluent-kafka supports KIP-447
   fencing. Without it, the safe exactly-once pattern needs one transactional producer per
   input partition, which is more code, more connections and more memory in a 4 GB budget.
2. **The gateway's write path is the hot path.** Producing is about 3× faster, and producing is
   what the gateway does on every request.
3. **Types:** the stubs let `src/` stay under strict pyright, which aiokafka would break.

## Consequences

- confluent-kafka is callback- and thread-based, not asyncio. The gateway uses one background
  thread for delivery reports and blocks request threads on a future
  ([`gateway.py`](../../src/eventplat/gateway.py)). FastAPI runs plain `def` handlers in a
  thread pool, so this costs nothing in practice.
- librdkafka is bundled inside the wheel, so there is no system library to install, but the
  client is a C extension: a crash there takes the process down. Crash tests (step 4) cover it.
- aiokafka stays only in the `spikes` dependency group, so this ADR's result can be re-run.
  It is never installed in the images.

## Revisit if

- aiokafka adds KIP-447 fencing and type information, and we want asyncio end to end.
- confluent-kafka's asyncio API (`confluent_kafka.aio`, marked experimental in 2.16.0) becomes
  stable.
