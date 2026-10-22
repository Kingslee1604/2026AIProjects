"""Durability under fault injection (R1-36): 50 questionnaire runs on a real worker process that
is killed (SIGKILL) at random moments and restarted, while 10% of fake model calls time out,
are unavailable, or return invalid JSON. Every run must finish, with no duplicate artifacts and
no completed item answered twice. Recovery times are written to bench/results/.

Slow (a few minutes), so it runs only with AGENTFORGE_DURABILITY=1 (`make durability`, CI's
engine job)."""

import json
import os
import random
import statistics
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.demo_corpus import ROOT as DEMO_ROOT
from tools.demo_corpus import _write_xlsx  # pyright: ignore[reportPrivateUsage]

from .conftest import Database
from .test_questionnaire_run import Stack, _user_factory, spawn_worker, start_stack

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTFORGE_DURABILITY") != "1",
    reason="slow durability test: set AGENTFORGE_DURABILITY=1 (make durability)",
)

RUNS = int(os.environ.get("AGENTFORGE_DURABILITY_RUNS", "50"))
KILLS = os.environ.get("AGENTFORGE_DURABILITY_KILLS", "1") == "1"  # 0: a no-kill baseline
ITEMS = 6
LATENCY_MS = 50.0
FAULT_RATE = 0.1
SEED = 36
KILL_EVERY = (3.0, 7.0)  # seconds of work after a recovery before the next kill
RECOVERY_LIMIT = 60.0  # no progress within this long after a kill: nothing left, or stuck
DEADLINE = 900.0
# Events only the worker emits: the first one after a kill marks recovered progress.
WORKER_EVENTS = (
    "run_started",
    "plan_done",
    "item_done",
    "item_failed",
    "review_waiting",
    "review_done",
    "export_done",
    "run_succeeded",
)


class Chaos(threading.Thread):
    """Kills the worker (SIGKILL) at a random moment, starts a new one with the same executor
    id (DBOS then recovers the killed worker's workflows), waits until the new worker records
    progress (the recovery time), and repeats. Waiting for recovery before the next kill keeps
    this a test of recovery rather than of a worker that is never allowed to start."""

    def __init__(
        self, stack: Stack, database: Database, rng: random.Random, run_ids: list[str]
    ) -> None:
        super().__init__(daemon=True)
        self.stack, self.database, self.rng, self.run_ids = stack, database, rng, run_ids
        self.kills = 0
        self.recovery: list[float] = []  # seconds, kill -> first progress of the new worker
        self.unobserved = 0  # kills after which there was nothing left to do
        self.done = threading.Event()

    def _progress_since(self, killed: float) -> float | None:
        row = self.stack.query(
            "SELECT min(extract(epoch FROM created_at)) FROM app.run_events "
            "WHERE run_id = ANY(CAST(:ids AS uuid[])) AND type = ANY(:types) "
            "AND created_at > to_timestamp(:k)",
            ids=self.run_ids,
            types=list(WORKER_EVENTS),
            k=killed,
        )[0][0]
        return None if row is None else float(row)

    def run(self) -> None:
        while not self.done.wait(self.rng.uniform(*KILL_EVERY)):
            self.stack.worker.kill()
            self.stack.worker.wait()
            killed = time.time()
            self.kills += 1
            self.stack.worker = spawn_worker(
                self.database,
                self.stack.blobs,
                self.stack.worker_log,
                latency_ms=LATENCY_MS,
                # A new fault sequence per worker life (the same seed would replay the same
                # faults every time), still reproducible from SEED.
                **{**FAULTS, "AGENTFORGE_FAKE_FAULT_SEED": str(SEED + self.kills)},
            )
            while not self.done.is_set() and time.time() - killed < RECOVERY_LIMIT:
                progressed = self._progress_since(killed)
                if progressed is not None:
                    self.recovery.append(round(progressed - killed, 2))
                    break
                time.sleep(0.2)
            else:
                self.unobserved += 1


FAULTS = {
    "AGENTFORGE_FAKE_FAULT_RATE": str(FAULT_RATE),
    "AGENTFORGE_FAKE_FAULT_SEED": str(SEED),
}


@pytest.fixture(scope="module")
def stack(database: Database, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    yield from start_stack(
        database,
        _user_factory(database),
        tmp_path_factory.mktemp("durability"),
        latency_ms=LATENCY_MS,
        **FAULTS,
    )


def _review(stack: Stack, run_id: str) -> None:
    """Approve everything and complete the review (the reviewer's part of the run)."""
    review = f"/v1/runs/{run_id}/review"
    stack.post(f"{review}/approve-supported", as_reviewer=True)
    for item in stack.reviewer.get(review).json()["items"]:
        if item["decision"] is None:
            stack.post(
                f"{review}/items/{item['item_index']}", {"decision": "approved"}, as_reviewer=True
            )
    completed = stack.post(f"{review}/complete", as_reviewer=True)
    assert completed.status_code == 200, completed.text


def test_runs_survive_worker_kills_and_model_faults(
    stack: Stack, database: Database, tmp_path: Path
) -> None:
    source = json.loads((DEMO_ROOT / "questionnaire/northwind-questionnaire.json").read_text())
    workbook = tmp_path / "durability.xlsx"
    _write_xlsx(source["questions"][:ITEMS], workbook, source["title"])
    questionnaire = stack.upload_questionnaire(workbook.read_bytes(), "durability.xlsx")
    started = time.monotonic()
    run_ids = [stack.start(questionnaire["id"]) for _ in range(RUNS)]

    rng = random.Random(SEED)  # noqa: S311 - kill timing, not crypto
    chaos = Chaos(stack, database, rng, run_ids)
    if KILLS:
        chaos.start()
    reviewed: set[str] = set()
    statuses: dict[str, str] = {}
    last_report = 0.0
    try:
        while time.monotonic() - started < DEADLINE:
            statuses = {r: stack.run(r)["status"] for r in run_ids}
            if time.monotonic() - last_report > 10:
                last_report = time.monotonic()
                counts = {s: list(statuses.values()).count(s) for s in set(statuses.values())}
                print(f"t={last_report - started:.0f}s kills={chaos.kills} {counts}")
            ended = {r: s for r, s in statuses.items() if s in {"failed", "cancelled"}}
            assert not ended, f"runs ended early: {ended}"
            for run_id, status in statuses.items():
                if status == "waiting_review" and run_id not in reviewed:
                    _review(stack, run_id)
                    reviewed.add(run_id)
            if all(s == "succeeded" for s in statuses.values()):
                break
            time.sleep(1.0)
    finally:
        chaos.done.set()
        if chaos.is_alive():
            chaos.join(timeout=30)
    elapsed = time.monotonic() - started
    succeeded = sum(s == "succeeded" for s in statuses.values())
    assert succeeded == RUNS, f"{succeeded}/{RUNS} runs succeeded: {statuses}"
    if KILLS:
        assert chaos.kills >= max(2, RUNS // 10), "too few kills to say anything"

    ids = {"ids": run_ids}
    duplicate_artifacts = stack.query(
        "SELECT count(*) FROM (SELECT run_id FROM app.artifacts "
        "WHERE run_id = ANY(CAST(:ids AS uuid[])) GROUP BY run_id, epoch HAVING count(*) > 1) d",
        **ids,
    )[0][0]
    artifacts = stack.query(
        "SELECT count(*) FROM app.artifacts WHERE run_id = ANY(CAST(:ids AS uuid[]))", **ids
    )[0][0]
    # A completed item is never answered again: one item_done event per item and epoch (it is
    # written in the same transaction as the answer), and at most one succeeded answer step.
    reanswered = stack.query(
        "SELECT count(*) FROM (SELECT run_id, item_index, epoch FROM app.run_events "
        "WHERE run_id = ANY(CAST(:ids AS uuid[])) AND type = 'item_done' "
        "GROUP BY run_id, item_index, epoch HAVING count(*) > 1) d",
        **ids,
    )[0][0]
    double_steps = stack.query(
        "SELECT count(*) FROM (SELECT run_id, item_index, epoch FROM app.step_records "
        "WHERE run_id = ANY(CAST(:ids AS uuid[])) AND node_id = 'answer' "
        "AND status = 'succeeded' GROUP BY run_id, item_index, epoch HAVING count(*) > 1) d",
        **ids,
    )[0][0]
    left_running = stack.query(
        "SELECT count(*) FROM app.step_records WHERE run_id = ANY(CAST(:ids AS uuid[])) "
        "AND status = 'running'",
        **ids,
    )[0][0]
    interrupted = stack.query(
        "SELECT count(*) FROM app.step_records WHERE run_id = ANY(CAST(:ids AS uuid[])) "
        "AND error_class = 'WorkerInterrupted'",
        **ids,
    )[0][0]
    faults = dict(
        stack.query(
            "SELECT status, count(*) FROM app.llm_calls WHERE run_id = ANY(CAST(:ids AS uuid[])) "
            "AND status <> 'ok' GROUP BY status",
            **ids,
        )
    )
    failed_items = stack.query(
        "SELECT count(*) FROM app.run_items WHERE run_id = ANY(CAST(:ids AS uuid[])) "
        "AND status = 'failed'",
        **ids,
    )[0][0]
    recovery = chaos.recovery
    # Worker-wide cap across all runs (run_worker_concurrency, default 8). Completed attempts
    # only: an interrupted attempt's end time is when it was closed, not when it stopped.
    steps = stack.query(
        "SELECT started_at, finished_at FROM app.step_records WHERE run_id = "
        "ANY(CAST(:ids AS uuid[])) AND node_id = 'answer' AND status = 'succeeded'",
        **ids,
    )
    points = sorted([(s.started_at, 1) for s in steps] + [(s.finished_at, -1) for s in steps])
    peak = current = 0
    for _, delta in points:
        current += delta
        peak = max(peak, current)

    assert peak <= 8, f"{peak} items answered at once across runs"
    assert artifacts == RUNS
    assert duplicate_artifacts == 0
    assert reanswered == 0
    assert double_steps == 0
    assert left_running == 0
    assert {"timeout", "unavailable", "invalid_output"} <= faults.keys(), faults
    assert recovery or not KILLS, "no recovery was observed"
    recovery = recovery or [0.0]

    results = {
        "test": "R1-36 durability under worker kills and model faults",
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "runs": RUNS,
        "items_per_run": ITEMS,
        "fake_latency_ms": LATENCY_MS,
        "fault_rate": FAULT_RATE,
        "seed": SEED,
        "elapsed_seconds": round(elapsed, 1),
        "runs_succeeded": succeeded,
        "kills": chaos.kills,
        "kills_with_nothing_left_to_do": chaos.unobserved,
        "artifacts": artifacts,
        "duplicate_artifacts": duplicate_artifacts,
        "completed_items_answered_twice": reanswered,
        "items_failed_after_retries": failed_items,
        "interrupted_step_attempts": interrupted,
        "peak_items_in_flight": peak,
        "faulty_model_calls": faults,
        "recovery_seconds": {
            "definition": "SIGKILL to the next progress recorded by the restarted worker "
            "(process start + DBOS recovery + the step in flight); the next kill comes "
            f"{KILL_EVERY[0]:g}-{KILL_EVERY[1]:g} s after a recovery",
            "observed": len(recovery),
            "min": min(recovery),
            "p50": round(statistics.median(recovery), 2),
            "p95": round(sorted(recovery)[max(0, round(0.95 * len(recovery)) - 1)], 2),
            "max": max(recovery),
            "all": recovery,
        },
    }
    print(json.dumps(results, indent=2))
    if target := os.environ.get("AGENTFORGE_BENCH_RESULTS"):
        out = Path(target)
        out.mkdir(parents=True, exist_ok=True)
        (out / "durability.json").write_text(json.dumps(results, indent=2) + "\n")
