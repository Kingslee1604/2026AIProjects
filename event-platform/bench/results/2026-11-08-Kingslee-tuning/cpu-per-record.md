# Tuning measurements, 2026-11-08 (ADR-0005)

Machine: i7-1255U laptop, WSL2 capped at 4 GB, dev stack (one Kafka node, one gateway).
Each line is one 20 s closed-loop run with 32 clients (`bench/cpu_probe.py`; these runs used
an earlier copy of it in a scratch folder that measured the same way). Columns: records/s,
ack p50, then CPU milliseconds per 1000 acknowledged records in each container.
Settings are cumulative from top to bottom unless the line says otherwise.

## Consumer batching and producer compression

| Setting | records/s | ack p50 ms | gateway | kafka | ledger | totals |
|---|---|---|---|---|---|---|
| Old: wait up to 1 s for 500 records; no compression | 464 | 67.9 | 2523 | 609 | 132 | 140 |
| | 476 | 66.7 | 2608 | 597 | 138 | 154 |
| | 213 | 151.1 | 5535 | 1893 | 354 | 6873 (restoring after a restart; discarded) |
| Wait for the first record only (0 s linger) + zstd | 216 | 134.0 | 5712 | 5446 | 1069 | 1363 |
| | 228 | 128.4 | 5591 | 4643 | 1019 | 1310 |
| | 223 | 133.6 | 5905 | 5013 | 1159 | 1364 |
| | 174 | 169.6 | 6768 | 5698 | 1299 | 1492 |
| | 210 | 141.6 | 5909 | 5114 | 1166 | 1517 |
| | 223 | 142.3 | 5613 | 4792 | 1124 | 1441 |
| 0 s linger, no compression | 356 | 88.3 | 3603 | 2497 | 663 | 1513 |
| | 357 | 84.2 | 3343 | 2581 | 642 | 837 |
| | 473 | 65.1 | 2373 | 1731 | 446 | 610 |
| 0.05 s linger | 364 | 88.4 | 3424 | 1695 | 293 | 743 |
| | 371 | 86.3 | 3297 | 1394 | 280 | 376 |
| | 334 | 94.8 | 3597 | 1582 | 307 | 473 |
| **0.2 s linger (kept)** | 596 | 53.7 | 1981 | 526 | 109 | 186 |
| | 558 | 57.4 | 2093 | 541 | 117 | 121 |
| | 493 | 65.5 | 2351 | 672 | 136 | 147 |

The six zstd runs: the second three were meant to be without zstd, but the edit that removed
it had failed, which was found afterwards (the image still had it). They are zstd runs.

## Gateway

| Setting | records/s | ack p50 ms | gateway | kafka | ledger | totals |
|---|---|---|---|---|---|---|
| Access log off (`--no-access-log`) | 673 | 45.1 | 1700 | 560 | 110 | 108 |
| | 635 | 48.1 | 1791 | 497 | 108 | 105 |
| | 446 | 68.5 | 2844 | 935 | 185 | 199 |
| **+ uvloop and httptools (kept)** | 556 | 53.5 | 2119 | 933 | 191 | 1671 (restoring; discarded) |
| | 701 | 42.8 | 1590 | 733 | 150 | 145 |
| | 432 | 62.5 | 2651 | 1250 | 242 | 255 |

Gateway CPU per record moves with the laptop's speed, and so does Kafka's (compare the slow
third runs), so the gateway/Kafka ratio is the steadier comparison: about 3.7 with the access
log on (0.2 s linger rows), 3.0-3.6 with it off, 2.1-2.3 with uvloop and httptools.

## Totals service memory while restoring after a restart

`docker stats` once a second (about 1.3 s apart), MiB, limit 256. Six partitions, each with a
full 50,000-record duplicate window.

- Six partitions restored at once: 44, 163, 157, 145, 134, 249, 256, 256, 256, 256, 256, 256,
  256, 255, then 113 when done.
- Two at a time (`RESTORE_THREADS = 2`, kept): 34, 63, 100, 141, 168, 189, 210, then 133 when
  done; about 4 s per pair, 12 s in all.
