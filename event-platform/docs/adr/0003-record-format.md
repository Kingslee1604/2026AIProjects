# ADR-0003: JSON records, checked in CI, with no schema registry for now

- **Status:** Accepted
- **Date:** 2026-10-27
- **Evidence:** [`spikes/record_format/formats.py`](../../spikes/record_format/formats.py), raw
  results in
  [`bench/results/2026-10-27-Kingslee-record-format/`](../../bench/results/2026-10-27-Kingslee-record-format/)

## Context

Every usage record crosses Kafka once and is decoded by at least two consumers (ledger and
totals), and later also by budget enforcement. The format decides three things:

- how many bytes the broker stores;
- how much CPU each service spends turning bytes into a validated `UsageEvent`;
- what happens when the record changes shape, for example when a `cached_tokens` field is added.

The candidates are JSON validated by pydantic (what step 1 shipped), Avro (fastavro) and
Protobuf. Avro and Protobuf are usually paired with a schema registry, which is one more service
inside the same 4 GB budget. Three registries were tested:

- Confluent Schema Registry 8.3.2, under the Confluent Community License;
- Apicurio Registry 3.3.3, under Apache-2.0;
- Karapace 6.2.4, under Apache-2.0.

All versions satisfy the 14-day supply-chain rule.

## Gates

The tests ran on an i7-1255U laptop (WSL2, 4 GB cap) with the dev stack running and one KRaft
broker (`apache/kafka:4.3.1`). The data was 100,000 realistic records: 500 tenants, 4 models,
and token counts spread across real ranges. Every decode ends in a validated `UsageEvent`,
because that is what a consumer needs. All three formats round-trip the records unchanged.

### Size and speed

| | JSON (pydantic) | Avro (fastavro) | Protobuf |
|---|---|---|---|
| Bytes per record, raw | 189 | 79 | 64 |
| **Bytes on the broker, zstd** | **52** | **47** | **39** |
| Bytes on the broker, lz4 | 88 | 65 | 50 |
| Bytes on the broker, uncompressed | 210 | 100 | 85 |
| Encode, records/s | 622k | 197k | 736k |
| Decode to a validated `UsageEvent`, records/s | **667k** | 174k | 263k |
| Decode, codec only, records/s | 515k | 256k | 4.6M |

The broker sizes include Kafka's own batch overhead. Speeds are single-process figures from
one run. A second run on the same laptop was 2–3× slower across the board, so compare the
columns with each other, not with other ADRs.

- **Compression closes most of the size gap.** Raw JSON is 3× Protobuf, but with zstd it is
  1.3×, because batches of records share their field names, tenants and models.
- **JSON is the fastest path to a validated event.** pydantic parses and validates JSON in one
  pass, in Rust. Avro and Protobuf decode first and then validate in Python. Protobuf's codec
  alone is very fast, but the validation step costs more than the codec saves.

### Adding a field

The test adds `cached_tokens` with a default of 0 and checks two situations:
- a consumer upgraded before the producers reads an old record;
- a producer upgraded before the consumers writes a new record that old code must read.

| | New reader, old record | Old reader, new record |
|---|---|---|
| JSON, `extra="forbid"` (as step 1 shipped) | ✅ | ❌ rejected, so every record would be dead-lettered |
| JSON, `extra="ignore"` | ✅ | ✅ |
| Avro, writer's schema known (registry) | ✅ | ✅ |
| Avro, writer's schema unknown | ❌ read fails | ❌ one field silently lost |
| Protobuf | ✅ | ✅ |

Avro has no field tags, so a reader must know exactly which schema wrote each record. In
practice that means a registry. Protobuf and JSON carry enough in each record to cope alone.

### Registries

Each registry ran with a 512 MiB limit, stored its schemas in Kafka, used `BACKWARD`
compatibility, and got the same three Avro schemas: v1, then a safe change (a new field with
a default), then a breaking change (a new field without one).

| | Confluent 8.3.2 | Apicurio 3.3.3 | Karapace 6.2.4 |
|---|---|---|---|
| License | Confluent Community (source-available) | Apache-2.0 | Apache-2.0 |
| Accepts the safe change | ✅ | ✅ | ✅ |
| **Blocks the breaking change** | ✅ 409 | ✅ 409 | ✅ 409 |
| Seconds until it accepts a write | 6.7 | 16.0 | 7.0 |
| **Memory, idle** | 263 MiB | 256 MiB | **117 MiB** |
| Memory after 2,000 lookups | 277 MiB | 297 MiB | 118 MiB |
| Image size | 826 MiB | 648 MiB | 1,637 MiB |

All three work. Karapace answers reads within seconds of starting, but holds writes until it has
elected itself leader, so "ready" here means "accepted a write".

## Decision

1. **Keep JSON on Kafka, validated by pydantic.** It is the fastest path to a validated event.
   With zstd it is only 12% bigger than Avro and 34% bigger than Protobuf. It is also readable
   in any Kafka tool and in the dead-letter topic without a decoder.
2. **Readers ignore unknown fields; only the HTTP edge is strict.**
   - `UsageEvent` now uses `extra="ignore"`, so a producer can add a field before every
     consumer is upgraded.
   - The gateway's request body, `UsageRequest`, keeps `extra="forbid"`, so a client's typo is
     still a 422 instead of silently lost data.

   This fixes the ❌ found above. It is a real bug in step 1 that would have dead-lettered every
   record during a rolling upgrade.
3. **No schema registry for now. CI does its job.** There is one producer (the gateway), and it
   lives in this repo with every consumer, so CI can enforce compatibility instead.
   [`schemas/usage-event.json`](../../schemas/usage-event.json) is a snapshot of the record's
   JSON Schema. A test fails if the model changes without the snapshot changing in the same
   commit, which puts every schema change in front of a reviewer. The rule for a reviewer:
   - Safe: add a field with a default.
   - Breaking: remove, rename or retype a field, or add one without a default. A breaking
     change gets a new topic, such as `usage.events.v2`.
4. **Set producer compression to zstd** when the gateway is tuned (step 5). The size result
   above depends on it.

## Consequences

- Records take about a third more broker disk and network than Protobuf would, which is fine at
  this scale.
- Compatibility is checked by a test and a reviewer, not by a server that refuses bad writes.
  Anyone producing to `usage.events` from outside this repo would bypass it, so the topic is
  written only by the gateway.
- Producers send no registry ID, so nothing is needed at startup and nothing can stall on a
  registry outage.

## Revisit if

- **Producers outside this repo** (other teams, other languages) write to Kafka directly.
  Then a server-enforced contract is worth the memory. Choose **Protobuf with Karapace**:
  Apache-2.0, the smallest broker size, the least memory, and no Confluent licence terms.
- Volume grows enough that a third of broker disk and network matters more than a simpler
  stack.
