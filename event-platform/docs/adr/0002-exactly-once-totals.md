# ADR-0002: Usage totals two ways — database for billing, Kafka transactions for live limits

- **Status:** Accepted
- **Date:** 2026-10-27
- **Evidence:** [`bench/exactly_once.py`](../../bench/exactly_once.py), raw results in
  [`bench/results/2026-10-27-Kingslee-exactly-once/`](../../bench/results/2026-10-27-Kingslee-exactly-once/)

## Context

The platform's promise is that every usage record counts exactly once in a tenant's totals.
That has to hold when consumers crash mid-batch and when clients retry a record they already
sent. Two ways to keep per-tenant, per-UTC-day totals were built and tested:

- **A. Kafka transactions** ([`totals.py`](../../src/eventplat/totals.py)). The service reads
  `usage.events` (`read_committed`). One Kafka transaction writes the changed totals to the
  compacted topic `usage.totals`, parks rejected records, and commits the input offsets
  (KIP-447 fencing, ADR-0001). Duplicates are caught by a filter over the last
  `DEDUPE_WINDOW` (50,000) input records of each partition. On restart, the filter is rebuilt
  by replaying those records from Kafka.
- **B. Database** ([`ledger.py`](../../src/eventplat/ledger.py)). The ledger already writes
  each record to Postgres with `ON CONFLICT (event_id) DO NOTHING`. The same SQL statement adds
  only the rows actually inserted to `usage_totals`, so duplicates and replays add nothing.

## Test

The setup was one KRaft broker (`apache/kafka:4.3.1`) on an i7-1255U laptop (WSL2, 4 GB cap),
with both consumers running side by side on the same input. 10% of sends were client retries:
every 10th record re-sends the record 500 places earlier. The pass bar is that every tenant's
input tokens, output tokens and event count match what was sent, exactly.

- **Chaos:** 60,000 records plus 5,950 retries, sent at 2,000/s. Every 4 s, the totals service
  or the ledger was `SIGKILL`ed and restarted, alternating between them.
- **Backlog:** both consumers were stopped and 200,000 records plus 19,950 retries were loaded.
  Then each consumer was started alone and timed until its totals were exact.

| | A. Kafka transactions | B. Database |
|---|---|---|
| Chaos: exact after `SIGKILL`s | ✅ to the token (3 kills) | ✅ to the token (3 kills) |
| Chaos: seconds to exact after the last send | 11.2 s | 1.2 s |
| Backlog: exact | ✅ | ✅ |
| Backlog: catch-up rate | 10,474 records/s | 9,732 records/s |
| Backlog: peak memory | 158 MiB | 93 MiB |
| Duplicates caught | Retries within 50,000 records of the original, per partition | Forever (primary key) |
| Code | ~250 lines, state rebuilt from Kafka | One SQL statement |

Backlog rates varied between runs on this laptop (about 10k to 16k records/s). Both paths
moved together, so treat their speed as a tie.

### What changed along the way

- **Static membership** (`group.instance.id`) on both consumers. Without it, a restarted
  member waited out the killed member's 45 s session timeout before getting its partitions
  back. In the first run, path A took **69.6 s** to become exact after chaos; with static
  membership it took **11.2 s**.
- **Streaming restore and a smaller prefetch** in path A. Restore had loaded the whole 50,000-
  record replay into one list, and librdkafka pre-fetches up to 64 MiB per consumer by default.
  Replaying in batches of 500 and capping the prefetch at 8 MiB brought peak memory from
  **232 MiB to 158 MiB**, against a 256 MiB container limit.

## Decision

Keep both, each in its own role.

1. **Billing source of truth: the database totals (B).** They are exact with no time limit on
   duplicates, they recover fastest, they use the least memory, and they are the least code.
2. **Live feed: the Kafka-transaction totals (A).** `usage.totals` **pushes** each change as it
   happens, with no database polling. Step 3 (budget enforcement) subscribes to it to cut off a
   tenant that goes over its limit.
3. **Reconciliation.** A periodic check compares A against B per tenant and day. A mismatch
   means a retry arrived outside A's window, which is an alert, not a billing error, because B
   is what gets billed.

## Consequences

- A can over-count only when a client retries more than 50,000 records (per partition) after
  the original. B is unaffected. The reconciliation check makes this visible.
- A restores partitions one at a time, about 1 s each even when empty, waiting for the poll
  and end-of-partition signal. That is acceptable at 6 partitions; parallel restore is a
  possible later improvement.
- A has the larger memory footprint (filter state plus replay), so it keeps its own 256 MiB
  limit and is watched in the crash tests (step 4).

## Revisit if

- Budget enforcement can tolerate polling Postgres. Then A could be dropped and B used alone.
- Retries far older than the window turn out to be common. Then grow `DEDUPE_WINDOW`, or check
  A's misses against B before enforcing.
