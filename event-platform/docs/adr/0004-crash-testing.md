# ADR-0004: Crash testing on three Kafka nodes — what held, what was tuned, what broke

- **Status:** Accepted
- **Date:** 2026-11-07
- **Evidence:** [`bench/crash.py`](../../bench/crash.py), the stack in
  [`compose.crash.yaml`](../../compose.crash.yaml), raw results in
  [`bench/results/`](../../bench/results/) (folders named `2026-11-0*-Kingslee-crash*`)

## Context

The platform promises that every acknowledged usage record is counted exactly once, that each
tenant's records keep their order, and that the system recovers from crashes on its own. Steps 1
to 3 tested parts of that (ADR-0002: worker kills on one broker; `bench/budget_chaos.py`:
gateway kills). This step breaks every part of the stack while it works, on a cluster that can
lose a node.

## Setup

- **Stack:** three KRaft nodes (`apache/kafka:4.3.1`, replication factor 3, min in-sync
  replicas 2), three gateways, the ledger, the totals service and Postgres, run as a separate,
  throwaway Compose project. Peak memory of the whole stack: 1.5–1.6 GB, inside the 4 GB WSL2
  cap. Machine: i7-1255U laptop.
- **Load:** 24 clients, one tenant each, send through the gateways and wait for each `202`
  before the next record, so each tenant's order is defined. A failed send is retried on
  another gateway with the same `event_id`, and every 10th send repeats an earlier record, as a
  client that lost a response would. Acknowledged rates were 160–400 records/s: the clients
  wait for every ack, so stalls lower the rate.
- **Checks, per scenario:** no acknowledged record missing from the ledger; no ledger row that
  was never sent; the database totals and the Kafka totals (`usage.totals`) equal what was
  acknowledged, to the token; per tenant, ledger offsets rise in send order. Also measured: how
  long a client waited for an ack, and the longest time one partition had records waiting while
  its consumer group's committed offset did not move ("stall").

| Scenario | What breaks |
|---|---|
| brokers | `SIGKILL` one Kafka node, restart it after 5 s, wait until fully in sync, repeat |
| consumers | totals and ledger scaled 1 → 2 (rebalance), `SIGKILL`ed, scaled 2 → 1 (rebalance) |
| mixed | a random part every 5 s: a node, a gateway, a totals or ledger instance |
| restart-race | `SIGKILL` the totals service, then a node 3 s later while it is starting up |
| db-freeze | Postgres frozen for 30 s (`docker pause`) |
| replay | a second ledger rebuilt from offset 0 into its own database, compared row for row |

## Result

Final run, all fixes in ([`2026-11-07-Kingslee-crash`](../../bench/results/2026-11-07-Kingslee-crash/)):

| Scenario | Records | Lost / extra / out of order | Both totals exact | Longest ack wait | Longest stall (totals / ledger) |
|---|---|---|---|---|---|
| brokers | 8,823 | 0 / 0 / 0 | ✅ | 11.5 s | 17.7 s / 2.6 s |
| consumers | 24,504 | 0 / 0 / 0 | ✅ | 2.0 s | 14.5 s / 13.0 s |
| mixed | 20,474 | 0 / 0 / 0 | ✅ | 2.5 s | 5.7 s / 2.5 s |
| restart-race | 26,023 | 0 / 0 / 0 | ✅ | 5.7 s | 23.1 s / 0.8 s |
| db-freeze | 21,600 | 0 / 0 / 0 | ✅ | 0.7 s | 1.2 s / 30.1 s |
| replay | 101,424 rows from 112,365 records | identical, row for row | ✅ | — | 13,219 records/s after a 1.4 s start |

- **Nothing was ever lost, counted twice or reordered**, in any run of any scenario during this
  step, including the runs that exposed the faults below. The ledger, which is what gets
  billed, was exact in every run.
- **A database outage does not reach clients.** With Postgres frozen for 30 s, ack latency was
  p50 40 ms / p99 175 ms, against 47 ms / 217 ms the rest of the run. The ledger waited, fell
  10,700–12,900 records behind, and caught up 1.6–2.0 s after Postgres came back.
- **Replay works.** Rebuilding the ledger from the log gives the same rows, offsets and totals.

## What was tuned

**Broker failure detection: 3 s instead of Kafka's 9 s** (crash stack only;
[`-broker-session-9000ms`](../../bench/results/2026-11-05-Kingslee-crash-broker-session-9000ms/),
[`-3000ms`](../../bench/results/2026-11-05-Kingslee-crash-broker-session-3000ms/)). Writes to a
killed node's partitions wait until the controllers declare it dead.

| Per broker kill | 9 s session (default) | 3 s session |
|---|---|---|
| Longest ack wait | 8.8–13.9 s | 2.9–6.0 s in earlier runs; 11.5 s once in the final run |
| p99 ack latency, whole run | 9,251 ms | 330 ms |

The cost: a node that stalls for more than 3 s, for example in a long GC pause, is treated as
dead and has to catch up (5–28 s here before all replicas were in sync again).

**Consumer session: 10 s instead of Kafka's 45 s** (both workers). A consumer removed for good
keeps its partitions until its session expires, because static membership never sends a
leave. Scale-down stalls, three runs each on a quiet laptop plus the full-suite runs
([`-consumer-session-*`](../../bench/results/)):

| Scale 2 → 1 | 45 s session | 10 s session |
|---|---|---|
| Longest stall | 10.1–12.8 s, and 47.2 s in 1 of 4 runs | 10.0–14.5 s in 4 runs |

Most scale-downs cost about 11 s either way (the rebalance itself); the session sets the worst
case. The cost: a consumer frozen for more than 10 s is removed and restores its state.
Restarts after a `SIGKILL` took 1–2 s in every run, well inside it. Earlier runs on a busy
laptop, which also froze for 16 minutes once, gave stalls up to 123 s and were discarded.

**Totals rebalances move only what changes.** The totals service used eager assignment, so on
any rebalance a surviving instance gave up all six partitions and rebuilt them one at a time
(about 1 s each, even when nearly empty): 6 partitions in 5.1 s. With `cooperative-sticky`
assignment and restores run side by side, it rebuilds only the partitions that moved: 3 in
0.06 s.

## What broke, and the fixes

The restart-race scenario was written to reproduce a five-minute totals stall seen once in
mixed ([`2026-11-06-Kingslee-crash`](../../bench/results/2026-11-06-Kingslee-crash/)). It
found three faults in the totals service, all while it restarted with Kafka nodes dying. In
the first two it kept polling, received no records, and recovered only when Kafka gave up on it
about five minutes later; in the third it blocked. None reached the ledger, and none lost or
double-counted a record: the Kafka totals fell behind and then caught up. Which fault caused the
first sighting is not known: that log was not kept.

1. **A committed-offset lookup failed once and was never retried**
   ([log excerpt](../../bench/results/2026-11-07-Kingslee-crash-offset-fetch-hang/totals-log-excerpt.txt)).
   After assignment, the client looks up where each partition was committed. When the group
   coordinator died at that moment, the lookup failed with `NOT_COORDINATOR`, and the client
   never fetched those partitions. Seen in 1 of 3 restart-race runs. **Fix:** both workers now
   look the offsets up themselves inside the assignment callback, where a failure raises and the
   service restarts, and assign with explicit offsets. The client never makes the failing lookup.
   The totals service already looked them up to restore its state, so it now also resumes
   exactly where that state ends.
2. **A fatal error that did not look fatal**
   ([results and log excerpt](../../bench/results/2026-11-07-Kingslee-crash-static-fence-hang/)).
   A node died while the restarted service was rejoining; the broker fenced it as a duplicate of
   itself (same `group.instance.id`), and the client became permanently unusable. It reported
   this with code `_FATAL` but `fatal()` false, so the shared decoder logged a warning and the
   service kept polling a dead client: no group member for 329 s. **Fix:** the decoder treats
   code `_FATAL` as fatal, so the service crashes and restarts. A unit test builds that exact
   error. In the final run, the same fencing happened again and the service recovered in
   seconds.

3. **A transaction call that outlived its own timeout**
   ([log excerpt](../../bench/results/2026-11-07-Kingslee-crash-watchdog-restart/totals-log-excerpt.txt)).
   With nodes being killed, `send_offsets_to_transaction(..., 30 s)` blocked for more than
   120 s. This is inside the Kafka client and not fixable here; the watchdog below caught it,
   printed the stack that named the call, and restarted the service. That run passed, with a
   124.6 s totals stall.

Two safety nets cover faults like these, found or not:

- **Stall guard** ([`progress.py`](../../src/eventplat/progress.py)), in both workers: records
  waiting on assigned partitions with nothing consumed for 60 s raises, and the service
  restarts. Either of the first two faults would have cost about a minute instead of five.
- **Watchdog** in the totals service: a main-loop pass that takes longer than 120 s prints every
  thread's stack and exits, for a hang that blocks instead of idling (fault 3).

Restart-race runs: after the first fix alone, 4 passed and the next failed on the second fault.
After both fixes, 3 of 3 passed, one of them by way of the watchdog restart (fault 3).

## Also learned

- **Measure where the cost is, not after the fact.** The first consumer comparison timed how
  long the totals took to become exact after the load stopped. That number depended on when
  the scale-down happened to land, and it pointed at the wrong cause. Measuring each
  partition's stall as it happens fixed the comparison.
- **The harness can slow down what it measures.** Replay first measured 941 records/s because
  the harness checksummed the whole replay table every second to see if it was done. Watching
  the replay group's committed offsets instead measured 9,900–13,200 records/s.
- **One run is not enough when results vary.** A single run per setting first suggested the
  consumer session did not matter. Repeated runs showed it sets the worst case.

## Decision

1. Keep the crash harness and the three-node crash stack as the standing test for these
   promises; run it before changing consumer, producer or transaction code.
2. Use a 3 s broker session (crash stack) and a 10 s consumer session (both workers), with
   cooperative-sticky assignment and parallel restores in the totals service (capped at two
   at a time by [ADR-0005](0005-benchmarks-and-tuning.md), for memory).
3. Assign partitions with explicitly looked-up offsets, treat code `_FATAL` as fatal, and keep
   the stall guard and the watchdog.

## Consequences

- A worker stuck like faults 1 and 2 now costs about a minute, not five; one blocked like
  fault 3 costs about two. In every case the ledger keeps billing exactly.
- Shorter sessions trade false alarms for faster recovery: a node frozen for more than 3 s, or a
  consumer frozen for more than 10 s, is treated as gone. On a busy laptop that happens; on
  dedicated hosts it should be rare. Both are settings (`BROKER_SESSION_TIMEOUT_MS`,
  `CONSUMER_SESSION_TIMEOUT_MS`).
- Rates here are client-limited (each client waits for its ack) and laptop-bound. They show
  behaviour under failure, not peak throughput.

## Revisit if

- The Kafka client is upgraded: check whether a failed committed-offset lookup is retried, and
  how a fenced static member is reported, before removing either fix.
- Kafka's newer consumer group protocol (KIP-848) is adopted: rebalances, static members and
  session timeouts work differently there, and these measurements need repeating.
