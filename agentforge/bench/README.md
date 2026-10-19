# Measurements

Results that the release plan marks **(Measured)**, committed so they can be compared over time.

| File | Produced by | What it shows |
| --- | --- | --- |
| `results/<date>-<machine>/` | `make bench BENCH_MACHINE=<name>` (R1-49) | On the running stack with the real models: indexing the Northwind corpus, retrieval latency (40 questions × 3), the 12-question and 40-question runs end to end (tokens, model calls, per-step and per-call latency, peak memory of the WSL2 VM and of Ollama). Then, on the fake model, enqueue → start for 30 runs started at once. `manifest.json` (hardware, power source, Ollama version and model digests, commit), `real.json`, `fake.json`, `memory_windows.csv`, `memory_wsl.csv`, and `summary.md` |
| `results/durability.json` | `make durability` (R1-36) | 50 questionnaire runs (6 items each) on a real worker process that is killed with SIGKILL at random moments and restarted, while 10% of fake model calls time out, are unavailable, or return invalid JSON. Runs succeeded, duplicate artifacts, completed items answered twice, peak items in flight, and the recovery-time distribution |

Notes for `make bench`:

- Run it on mains power with heavy apps closed: on battery, Windows throttles the CPU. On
  Windows, `bench/memory-sampler.ps1` samples memory every 5 s and keeps the machine awake
  until the run ends. Everything goes through the public API with a dedicated admin user.
- **End to end** is from the run's creation to the last answer step finishing (the run then
  waits for a person's review). Steps run four questions at a time (`AGENTFORGE_RUN_ITEM_PARALLELISM`),
  but one CPU-bound Ollama does most of the work, so they mostly queue on the model.
- **Peak memory** is the highest 5-second sample during the run. `vmmem_mb` is the WSL2 virtual
  machine as Windows sees it (Linux, Docker, the stack, and page cache). `wsl_used_mb` is the
  memory Linux reports as used. `ollama_mb` covers all Ollama processes (server and model
  runners).
- Before an hour-long run, check the script quickly against a fake-model stack:
  `uv run python bench/measure.py smoke <dir>` (see the docstring).

Notes for reading `durability.json`:

- **Recovery time** is from the kill to the first progress recorded by the restarted worker. It
  includes starting the process, DBOS recovering the killed worker's workflows, and finishing
  the step that was in flight. The next kill comes 3–7 s after a recovery.
- **Interrupted step attempts** are attempts cut off by a kill. They are closed as
  `WorkerInterrupted` and redone, which is expected. A completed item is never redone
  (`completed_items_answered_twice` must be 0).
- The model is the fake provider with 50 ms latency, so the times measure the engine and the
  database, not a real model. CI runs the same test on every push (the `engine` job) and
  uploads its results as a build artifact; the committed file is from a local run.
