# Engine prototypes (M3, R1-20 … R1-24)

Spike code for [ADR-0002](../../docs/adr/0002-durable-execution.md), kept for the record. Not
part of the product.

- `harness/`: the mini-flow (`steps.py`, identical for every engine), the `RunEngine` port, the
  worker process the tests kill, and the `conformance` schema (RLS like the product).
- `adapters/`: `stub` (deliberately broken), `dbos_engine`, `langgraph_engine`, `custom`.
- `tests/test_conformance.py`: hard gates H1–H8, one test each, identical for every engine.
- `results/<engine>.jsonl`: raw gate results (one JSON line per gate run).

Run one engine (needs Docker; about 3–6 minutes):

```bash
CONF_ENGINE=dbos uv run --group engine-spikes pytest spikes/engine/tests
```

Knobs: `CONF_CHAOS_RUNS` (default 50), `CONF_CHAOS_TIMEOUT` (600 s), `CONF_REVIEW_WAIT` (15 s).

Keep H1 at 50 runs: with fewer, runs can finish before the chaos loop has killed the worker
more than once. `results/stub.jsonl` shows this: a 10-run H1 "passed" after a single kill,
while the 50-run H1 failed as expected (1 of 50 runs finished, 49 stuck mid-flow).
