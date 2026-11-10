# ADR-0005: Benchmarks on one laptop, and what they changed

- **Status:** Accepted
- **Date:** 2026-11-08
- **Evidence:** [`bench/load.py`](../../bench/load.py),
  [`bench/cpu_probe.py`](../../bench/cpu_probe.py); raw results in
  [`2026-11-08-Kingslee-load-before-tuning`](../../bench/results/2026-11-08-Kingslee-load-before-tuning/),
  [`2026-11-08-Kingslee-load`](../../bench/results/2026-11-08-Kingslee-load/),
  [`2026-11-08-Kingslee-load-3-gateways`](../../bench/results/2026-11-08-Kingslee-load-3-gateways/)
  and [`2026-11-08-Kingslee-tuning`](../../bench/results/2026-11-08-Kingslee-tuning/cpu-per-record.md)

## Context

Steps 1 to 4 showed the platform is correct under failure. This step measures how fast it is
on the dev stack (one Kafka node, one gateway, the workers and Postgres): records per second
through a gateway, how long a client waits for its acknowledgement, how long until the ledger
has the record, how long a budget check takes, and how much memory the stack uses. The first
run found three things worth fixing before any number went into the README.

## How it is measured

- **Machine:** i7-1255U laptop (2 fast and 8 efficient cores), WSL2 capped at 4 GB; the load
  generator runs on the same laptop, in four processes so its own GIL is not the limit.
- **Saturation:** N clients each send the next record as soon as the last is acknowledged
  (`acks=all`: on Kafka), N = 8 to 128, 20 s each.
- **Steady rates:** clients send on a schedule at 25%, 50% and 80% of that maximum. The
  ledger's latency is the row's `recorded_at` (Postgres) minus `occurred_at` (set by the client
  just before sending); both read the same kernel clock, and the measured offset was under
  0.1 ms.
- **Budget checks:** `/v1/authorize` when the gateway's local allowance covers the call, and
  when every call has to top up from the budget service.
- **Comparing settings:** throughput on this laptop moves 10-30% between identical runs, with
  other programs and with which cores the work lands on. CPU time per record, read from each
  container's cgroup counter, moves much less, so settings were compared on that, three runs
  each ([`cpu_probe.py`](../../bench/cpu_probe.py)).

## What changed

**1. The workers no longer wait up to a second to fill a batch.** Both read with
`consume(500, timeout=1 s)`, which waits until it has 500 records or the second is up. Below
500 records/s that is nearly always the full second, so the ledger wrote a record 0.5 s after
it was sent at the median and 1.0 s at worst, even at 130 records/s. They now wait for the
first record, then up to `LINGER_S` more ([`batching.py`](../../src/eventplat/batching.py)).
Each batch costs an offset commit, and in the totals service a Kafka transaction, so the
linger is a trade:

| Linger after the first record | CPU per 1000 records: Kafka / ledger / totals |
|---|---|
| (old: up to 1 s for a full batch) | 0.6 s / 0.13 s / 0.15 s |
| 0 s (only what has arrived) | 1.7-2.6 s / 0.45-0.66 s / 0.6-1.5 s |
| 0.05 s | 1.4-1.7 s / 0.28-0.31 s / 0.38-0.74 s |
| **0.2 s (chosen)** | 0.5-0.7 s / 0.11-0.14 s / 0.12-0.19 s |

At 0.2 s the cost is back to the old level, and the ledger's latency fell to a quarter:

| Ledger has the record (p50 / p99) | Before | After |
|---|---|---|
| Light load (130-180 records/s) | 500 / 1,020 ms | 115 / 217 ms |
| 80% of maximum | 628 / 1,202 ms | 149-166 / 328-364 ms |

**2. The gateway does less per request.** It was the bottleneck: one Python process at a full
core (about 110% with the Kafka client's threads) while the laptop was 70% idle. Two changes,
kept because CPU per record fell relative to Kafka's in every run: no access log line per
request, and uvicorn's C-accelerated HTTP parser and event loop (`httptools`, `uvloop`,
the two new dependencies). Gateway CPU per record went from about 3.7 times Kafka's to about
2.2 times. Maximum throughput through one gateway was 532 records/s before and 613-718 after
(two runs).

**3. The totals service restores two partitions at a time, not all six.** Step 4 made restores
parallel to speed up rebalances. With full duplicate windows (50,000 records per partition,
which the crash tests' fresh stacks never reached), six at once held the service at its
256 MiB limit for about 10 s after a restart, kept alive only by swap: one step from an
out-of-memory kill. Two at a time peaks at about 210 MiB and restores all six in about 12 s,
no slower than six at once was.

**Not changed: producer compression stays off.** ADR-0003 planned zstd for the gateway. Each
request waits for its own acknowledgement, so the producer's batches hold a few records, and
compression costs per batch: with zstd, gateway CPU per record roughly doubled, Kafka's rose
about 8-fold, and throughput halved (about 220 records/s against 415-480).

## Result

Dev stack, after the changes ([`2026-11-08-Kingslee-load`](../../bench/results/2026-11-08-Kingslee-load/),
[`-3-gateways`](../../bench/results/2026-11-08-Kingslee-load-3-gateways/)). In every phase of
both runs, every acknowledged record reached the ledger and no request failed.

| Measure | Result |
|---|---|
| Most records/s through one gateway | 613-718 (32-64 clients) |
| Ack latency at half that rate, p50 / p99 | 8.7-9.6 / 83-112 ms |
| Ledger has the record, light load, p50 / p99 | 115-116 / 217-220 ms |
| Budget check from the local allowance, one client, p50 / p99 | 1.8-4.3 / 5.6-10.7 ms |
| Budget check that tops up from the budget service, one client, p50 / p99 | 6.7-17.7 / 12-31 ms |
| Three gateways, most records/s | 881, with the laptop 62-67% busy |
| Memory, whole stack, idle / peak under load | about 860 MiB / 1.0 GiB (1.13 GiB with three gateways) |

Three gateways give about 1.3 times one, because the laptop runs the clients (about 2 cores),
three gateways, Kafka and Postgres at once. This shows gateways scale out; it cannot show how
far.

## Decision

1. Workers read batches with a 0.2 s linger after the first record.
2. The gateway runs without an access log and with `uvloop` and `httptools`.
3. The totals service restores at most two partitions at a time.
4. No producer compression while each request waits for its own acknowledgement.
5. Compare settings by CPU per record, three runs each, not by single throughput numbers.

## Consequences

- A record reaches the ledger within about 0.2-0.4 s at any load measured, up to 1 s before.
- One gateway acks about 600-700 records/s on this laptop; more needs more gateways. The
  allowance design already allows any number (each holds its own slices of budget).
- No per-request access log: a stored record can be traced by `event_id` in Kafka and the
  ledger, and unhandled errors are still logged, but a deliberate 503 (the client retries) now
  leaves no line in the gateway's log. Request metrics are a better fit than logs at this rate
  and are not built yet.
- A restore after a restart takes about 12 s with full duplicate windows, during which the
  Kafka totals lag; the ledger is unaffected.

## Revisit if

- Clients send records in batches, or the gateway batches several requests per produce: then
  producer batches grow and zstd may pay off (ADR-0003's size result still holds).
- The duplicate window or the partition count grows: restore memory grows with both.
- The stack runs on separate machines: measure gateway scaling there.
