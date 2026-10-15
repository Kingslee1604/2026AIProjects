# Measurements

Results that the release plan marks **(Measured)**, committed so they can be compared over time.

| File | Produced by | What it shows |
| --- | --- | --- |
| `results/durability.json` | `make durability` (R1-36) | 50 questionnaire runs (6 items each) on a real worker process that is killed with SIGKILL at random moments and restarted, while 10% of fake model calls time out, are unavailable, or return invalid JSON. Runs succeeded, duplicate artifacts, completed items answered twice, peak items in flight, and the recovery-time distribution |

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
