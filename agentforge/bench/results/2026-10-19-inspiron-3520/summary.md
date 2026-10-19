# Measurements 2026-10-19-inspiron-3520

**Machine:** Inspiron 15 3520, 12th Gen Intel(R) Core(TM) i7-1255U (10 cores / 12 threads), 15.7 GB RAM, no GPU; Microsoft Windows 11 Home 26200; mains power: True. WSL2 memory 3919 MB. Ollama 0.40.6, models {'nomic-embed-text:v1.5': '0a109f422b47', 'qwen3:4b': '359d7dd4bcda', 'qwen2.5:7b': '845dbda0ea48', 'llama3.2:latest': 'a80c4f17acd5'}. Commit `1688557`, 2026-10-19T20:30:55Z. Raw data: `real.json`, `fake.json`, `memory_*.csv`, `manifest.json`. All values are **Measured** on this machine only.

| Measure | Result |
|---|---|
| Index the Northwind corpus (12 documents, 53 chunks) | 24.5 s from upload to all ready |
| Retrieval latency (120 searches, HTTP + query embedding + hybrid search) | p50 **168 ms**, p95 **208 ms** |
| 12-question run, end to end (queued → every answer ready for review) | **31.2 min** (156 s per question); status waiting_review, 12/12 answered |
| 12-question run: model calls / tokens | 42 calls, 19055 tokens (15956 prompt, 3099 completion) |
| 12-question run: step `answer` | p50 232.3 s, p95 333.8 s (n=26) |
| 12-question run: step `plan` | p50 118.6 s, p95 118.6 s (n=2) |
| 12-question run: model call `draft` | p50 173.2 s, p95 180.0 s (n=26) |
| 12-question run: model call `plan` | p50 123.6 s, p95 123.6 s (n=2) |
| 12-question run: model call `relevance` | p50 159.4 s, p95 180.1 s (n=14) |
| 12-question run: peak memory | WSL2 VM 2951 MB (Linux used 1581 MB), Ollama 3864 MB |
| 40-question run, end to end (queued → every answer ready for review) | **110.0 min** (165 s per question); status waiting_review, 40/40 answered |
| 40-question run: model calls / tokens | 89 calls, 55276 tokens (46199 prompt, 9077 completion) |
| 40-question run: step `answer` | p50 217.2 s, p95 1997.2 s (n=45) |
| 40-question run: step `plan` | p50 88.7 s, p95 2134.7 s (n=5) |
| 40-question run: model call `draft` | p50 123.8 s, p95 180.0 s (n=45) |
| 40-question run: model call `plan` | p50 92.7 s, p95 98.6 s (n=5) |
| 40-question run: model call `relevance` | p50 103.5 s, p95 161.9 s (n=38) |
| 40-question run: model call `verify` | p50 59.8 s, p95 59.8 s (n=1) |
| 40-question run: peak memory | WSL2 VM 1094 MB (Linux used 1599 MB), Ollama 4590 MB |
| Enqueue → start, fake model, 30 runs started at once | run started p50 0.82 s, **p95 1.07 s**; first step p95 1.51 s |

## What these numbers show

- **The runs lose time to model timeouts.** A run answers four questions at once
  (`AGENTFORGE_RUN_ITEM_PARALLELISM=4`), but Ollama on this CPU works through them in turn.
  Each call's time therefore includes waiting behind the others. Calls that crossed the 180 s
  model timeout were abandoned, and their question was answered again:
  - 12-question run: 14 of 42 model calls timed out, so 26 answer attempts were needed for
    12 questions;
  - 40-question run: 5 of 89 calls timed out, so 45 attempts for 40 questions.

  That is why a draft call shows p50 120–170 s here, against about 30–70 s when questions are
  asked one at a time (`docs/evaluation`). The `answer` and `plan` p95 values of 2000 s or more
  in the 40-question run are steps that waited for the model behind others. The end-to-end
  times are real for the default settings, but they include this waste. Counts come from
  `app.step_records` and `app.llm_calls` for the two run IDs in `real.json`.
- **Memory:** Ollama peaked at **3.9–4.6 GB** (qwen3:4b plus the embedding model). Linux inside
  WSL2 used at most **1.6 GB** for Docker and the stack, within its 3.9 GB cap. The Windows-side
  working set of the WSL2 VM (`vmmem_mb`) is trimmed by Windows, so it can read lower than
  what Linux uses (1094 MB against 1599 MB in the 40-question run). Use the Linux figure.
- **The engine adds about 1 s:** with no model time, 30 runs started at once each began within
  1.1 s (p95) and started their first step within 1.5 s.
- **Retrieval is not a bottleneck:** 168 ms p50 and 208 ms p95 per search, including embedding
  the question on the CPU.
