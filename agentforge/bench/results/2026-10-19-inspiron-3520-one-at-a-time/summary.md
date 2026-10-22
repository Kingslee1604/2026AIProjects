# Measurements 2026-10-19-inspiron-3520-one-at-a-time

**Machine:** Inspiron 15 3520, 12th Gen Intel(R) Core(TM) i7-1255U (10 cores / 12 threads), 15.7 GB RAM, no GPU; Microsoft Windows 11 Home 26200; mains power: False. WSL2 memory 3919 MB. Ollama 0.40.6, models {'nomic-embed-text:v1.5': '0a109f422b47', 'qwen3:4b': '359d7dd4bcda', 'qwen2.5:7b': '845dbda0ea48', 'llama3.2:latest': 'a80c4f17acd5'}. Commit `5d70f88`, 2026-10-20T05:53:57Z. Raw data: `real.json`, `fake.json`, `memory_*.csv`, `manifest.json`. All values are **Measured** on this machine only.

| Measure | Result |
|---|---|
| Index the Northwind corpus (12 documents, 53 chunks) | 16.7 s from upload to all ready |
| Retrieval latency (120 searches, HTTP + query embedding + hybrid search) | p50 **150 ms**, p95 **248 ms** |
| 12-question run, end to end (queued → every answer ready for review) | **14.9 min** (74 s per question); status waiting_review, 12/12 answered |
| 12-question run: model calls / tokens | 25 calls, 16400 tokens (13602 prompt, 2798 completion) |
| 12-question run: step `answer` | p50 60.8 s, p95 100.1 s (n=12) |
| 12-question run: step `plan` | p50 73.1 s, p95 73.1 s (n=2) |
| 12-question run: model call `draft` | p50 49.0 s, p95 102.4 s (n=12) |
| 12-question run: model call `plan` | p50 74.7 s, p95 74.7 s (n=2) |
| 12-question run: model call `relevance` | p50 14.6 s, p95 24.4 s (n=11) |
| 12-question run: peak memory | WSL2 VM 1973 MB (Linux used 1509 MB), Ollama 3847 MB |
| 40-question run, end to end (queued → every answer ready for review) | **36.6 min** (55 s per question); status waiting_review, 40/40 answered |
| 40-question run: model calls / tokens | 85 calls, 55479 tokens (46546 prompt, 8933 completion) |
| 40-question run: step `answer` | p50 47.0 s, p95 62.5 s (n=40) |
| 40-question run: step `plan` | p50 48.3 s, p95 52.3 s (n=5) |
| 40-question run: model call `draft` | p50 37.1 s, p95 51.3 s (n=40) |
| 40-question run: model call `plan` | p50 50.0 s, p95 53.3 s (n=5) |
| 40-question run: model call `relevance` | p50 11.3 s, p95 15.6 s (n=38) |
| 40-question run: model call `verify` | p50 13.4 s, p95 13.4 s (n=2) |
| 40-question run: peak memory | WSL2 VM 1134 MB (Linux used 1527 MB), Ollama 4387 MB |
| Enqueue → start, fake model, 30 runs started at once | run started p50 1.22 s, **p95 1.80 s**; first step p95 3.34 s |

## What changed, and how to read it

- **Setting:** the worker answers one question at a time (`AGENTFORGE_RUN_WORKER_CONCURRENCY=1`,
  the new Compose default). The earlier run, [`2026-10-19-inspiron-3520`](../2026-10-19-inspiron-3520/summary.md),
  used the old default of up to 8 at once (4 per run), so calls queued on the CPU-bound Ollama,
  and some hit the 180 s timeout and were redone.
- **Result:** 12 questions in **14.9 min instead of 31.2**, and 40 questions in **36.6 min
  instead of 110**. Every model call succeeded (25 and 85 calls, 0 timeouts, against 14 and 5
  before). A draft call now takes 37–49 s at the median, because it no longer waits behind
  others.
- **On battery:** the laptop ran on battery for this measurement (the manifest records the power
  source), and Windows slows the CPU on battery. The earlier run was on mains power. The gain
  therefore comes despite a slower CPU; on mains power these times would likely be shorter
  (not measured).
- **Engine start** (fake model, 30 runs at once) was p95 1.8 s against 1.07 s earlier. With one
  question at a time per worker, the burst of runs queues behind each other; it is still under
  2 s.
