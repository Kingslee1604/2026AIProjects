"""Hard gates H1-H8 (architecture §7.3), one test each, identical for every engine.

Scaled for a laptop where the design asks for long waits; each scale-down is recorded with the
result (for example H2 waits 15 s instead of 10 min: what matters is that waiting runs hold no
worker resources and survive a restart, which a short wait shows as well as a long one).
"""

import base64
import os
import random
import re
import time
import uuid
from datetime import timedelta

from spikes.engine.harness.steps import CONTENT_MARKER
from spikes.engine.tests.conftest import ENGINE, Harness, query, record

CHAOS_RUNS = int(os.environ.get("CONF_CHAOS_RUNS", "50"))
REVIEW_WAIT = float(os.environ.get("CONF_REVIEW_WAIT", "15"))
CHAOS_TIMEOUT = float(os.environ.get("CONF_CHAOS_TIMEOUT", "600"))


def event_counts(h: Harness, run_id: uuid.UUID) -> dict[tuple[str, int | None], int]:
    rows = query(
        h.owner,
        "SELECT type, epoch, count(*) AS n FROM conformance.run_events "
        "WHERE run_id = :r GROUP BY type, epoch",
        r=run_id,
    )
    return {(r.type, r.epoch): r.n for r in rows}


def calls_after_completion(h: Harness, run_ids: list[uuid.UUID]) -> int:
    """Model calls for an item that started after the item had already completed (H1)."""
    return int(
        query(
            h.owner,
            "SELECT count(*) FROM conformance.llm_log l JOIN conformance.run_items i "
            "ON i.run_id = l.run_id AND i.idx = l.idx "
            "WHERE l.run_id = ANY(:ids) AND i.completed_at IS NOT NULL "
            "AND l.started_at > i.completed_at",
            ids=run_ids,
        )[0][0]
    )


def max_concurrency(h: Harness, run_id: uuid.UUID) -> int:
    """Largest number of overlapping model calls in one run (H3)."""
    rows = query(
        h.owner,
        "SELECT started_at, coalesce(finished_at, started_at + interval '1 second') AS ended "
        "FROM conformance.llm_log WHERE run_id = :r",
        r=run_id,
    )
    points = sorted([(r.started_at, 1) for r in rows] + [(r.ended, -1) for r in rows])
    current = peak = 0
    for _, delta in points:
        current += delta
        peak = max(peak, current)
    return peak


def complete_event_set(h: Harness, run_id: uuid.UUID, epochs: int = 1) -> bool:
    counts = event_counts(h, run_id)
    expected = {
        ("run_started", None): 1,
        ("plan_done", None): 1,
        ("item_done", None): 20,
        ("review_waiting", None): 1,
        ("review_done", None): 1,
        **{("export_done", e): 1 for e in range(1, epochs + 1)},
        **{("run_succeeded", e): 1 for e in range(1, epochs + 1)},
    }
    return counts == expected


# --- H8 (and the happy path every other gate builds on) ---------------------------------------


def test_h8_every_step_emits_exactly_one_event(harness: Harness) -> None:
    runs = [harness.create_run(org=i) for i in range(3)]
    finished = harness.drive(runs, timeout=60)
    exact = finished and all(complete_event_set(harness, r) for _, r in runs)
    record("H8", exact, finished=finished, runs=len(runs))
    assert finished, "runs did not complete"
    assert exact, {str(r): event_counts(harness, r) for _, r in runs}


# --- H3 ---------------------------------------------------------------------------------------


def test_h3_fan_out_is_bounded_per_run(harness: Harness) -> None:
    runs = [harness.create_run(org=i) for i in range(3)]
    finished = harness.drive(runs, timeout=90)
    peaks = {str(r): max_concurrency(harness, r) for _, r in runs}
    passed = finished and all(1 < p <= 4 for p in peaks.values())
    record("H3", passed, finished=finished, peak_concurrency=peaks, limit=4)
    assert finished
    assert all(p <= 4 for p in peaks.values()), f"more than 4 concurrent item calls: {peaks}"
    assert all(p > 1 for p in peaks.values()), f"items did not run in parallel at all: {peaks}"


# --- H4 ---------------------------------------------------------------------------------------


def test_h4_export_once_per_epoch_and_retry_makes_a_new_artifact(harness: Harness) -> None:
    runs = [harness.create_run(org=i) for i in range(2)]
    assert harness.drive(runs, timeout=60)
    org_id, run_id = runs[0]
    epoch = harness.retry_export(org_id, run_id)
    retried = harness.wait_for(lambda: harness.statuses([run_id])[run_id] == "succeeded", 30)
    artifacts = query(
        harness.owner,
        "SELECT run_id, epoch FROM conformance.artifacts WHERE run_id = ANY(:ids) ORDER BY epoch",
        ids=[r for _, r in runs],
    )
    per_run = {str(r): [a.epoch for a in artifacts if a.run_id == r] for _, r in runs}
    passed = retried and per_run[str(run_id)] == [1, epoch] and per_run[str(runs[1][1])] == [1]
    record("H4", passed, retried=retried, artifacts=per_run)
    assert retried, "retry of the export did not finish"
    assert per_run[str(run_id)] == [1, 2], per_run
    assert per_run[str(runs[1][1])] == [1], per_run
    assert complete_event_set(harness, run_id, epochs=2)


# --- H5 ---------------------------------------------------------------------------------------


def test_h5_cancel_stops_new_steps_within_two_seconds(slow_harness: Harness) -> None:
    h = slow_harness
    org_id, run_id = h.create_run()
    assert h.wait_for(
        lambda: (
            query(h.owner, "SELECT count(*) FROM conformance.llm_log WHERE run_id = :r", r=run_id)[
                0
            ][0]
            >= 4
        ),
        30,
    ), "items never started"
    # (Not `AS t`: Row.t is SQLAlchemy's own tuple accessor and would shadow the column.)
    cancelled_at = query(h.owner, "SELECT clock_timestamp() AS now")[0].now
    h.cancel(org_id, run_id)
    time.sleep(5)
    late = query(
        h.owner,
        "SELECT count(*) FROM conformance.llm_log WHERE run_id = :r AND started_at > :t",
        r=run_id,
        t=cancelled_at + timedelta(seconds=2),
    )[0][0]
    started = query(
        h.owner, "SELECT count(*) FROM conformance.llm_log WHERE run_id = :r", r=run_id
    )[0][0]
    status = h.statuses([run_id])[run_id]
    passed = late == 0 and status == "cancelled"
    record("H5", passed, calls_started_2s_after_cancel=late, calls_total=started, status=status)
    assert late == 0, f"{late} model calls started more than 2 s after cancel"
    assert status == "cancelled"


# --- H7 ---------------------------------------------------------------------------------------


def test_h7_a_lost_start_is_recovered_and_no_execution_lacks_a_run(harness: Harness) -> None:
    # Crash between committing the run row and starting its execution: never call start().
    lost = harness.create_run(start=False)
    # And an execution for a run row that does not exist must not create one.
    ghost = uuid.uuid4()
    harness.run(harness.engine.start(harness.org_ids[0], ghost))
    recovered = harness.drive([lost], timeout=60)
    ghost_rows = query(
        harness.owner,
        "SELECT (SELECT count(*) FROM conformance.runs WHERE id = :g) "
        "+ (SELECT count(*) FROM conformance.run_items WHERE run_id = :g) AS n",
        g=ghost,
    )[0].n
    healthy = harness.drive([harness.create_run()], timeout=60)
    passed = recovered and ghost_rows == 0 and healthy
    record(
        "H7",
        passed,
        lost_start_recovered=recovered,
        ghost_rows=ghost_rows,
        worker_healthy_after=healthy,
    )
    assert recovered, "a run whose start was lost never ran"
    assert ghost_rows == 0
    assert healthy, "the worker stopped working after a start for a missing run"


# --- H6 ---------------------------------------------------------------------------------------

_B64 = re.compile(r"^[A-Za-z0-9+/=\s]{16,}$")


def _contains_marker(value: object) -> bool:
    if value is None:
        return False
    raw = bytes(value) if isinstance(value, (bytes, bytearray, memoryview)) else str(value).encode()
    if CONTENT_MARKER.encode() in raw:
        return True
    text = raw.decode("latin-1")
    if _B64.match(text):  # e.g. pickled-then-base64 step outputs
        try:
            return CONTENT_MARKER.encode() in base64.b64decode(text, validate=False)
        except ValueError:
            return False
    return False


def test_h6_engine_tables_hold_ids_and_status_only(harness: Harness) -> None:
    runs = [harness.create_run(org=i) for i in range(2)]
    assert harness.drive(runs, timeout=60)
    tables = [
        r.table_name
        for r in query(
            harness.owner,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'engine'",
        )
    ]
    leaks: list[str] = []
    rows_scanned = 0
    for table in tables:
        for row in query(harness.owner, f'SELECT * FROM engine."{table}"'):  # noqa: S608
            rows_scanned += 1
            leaks += [f"{table}.{k}" for k, v in row._mapping.items() if _contains_marker(v)]
    content_rows = query(
        harness.owner,
        "SELECT count(*) FROM conformance.run_items WHERE result LIKE :m",
        m=f"%{CONTENT_MARKER}%",
    )[0][0]
    passed = not leaks and content_rows == 40
    record("H6", passed, engine_tables=tables, rows_scanned=rows_scanned, leaks=sorted(set(leaks)))
    assert content_rows == 40, "the marker should be in the app tables (sanity check)"
    assert not leaks, f"tenant content found in engine tables: {sorted(set(leaks))}"


# --- H2 ---------------------------------------------------------------------------------------


def _worker_connections(h: Harness) -> int:
    return int(
        query(
            h.owner,
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'conf-worker'",
        )[0][0]
    )


def _park(h: Harness, count: int) -> tuple[list[tuple[uuid.UUID, uuid.UUID]], bool]:
    runs = [h.create_run(org=i) for i in range(count)]
    ids = [r for _, r in runs]
    parked = h.wait_for(lambda: all(s == "waiting_review" for s in h.statuses(ids).values()), 120)
    return runs, parked


def test_h2_waiting_runs_hold_no_resources_and_survive_a_restart(harness: Harness) -> None:
    """Measured as growth: park 20 runs, measure; park 20 more, measure again. An engine that
    pins a thread or connection per waiting run grows by about 20; a fixed-size pool (which
    may already be full after the first batch's activity) does not grow.

    Revision (2026-10-14): the first version compared idle against waiting and so counted a
    pool's normal size as pinning (DBOS: 11 -> 653 threads with its default unbounded pool,
    11 -> 42 with max_executor_threads=32, while its waits are futures, not threads)."""
    h = harness
    time.sleep(2)
    idle_connections, idle_threads = _worker_connections(h), h.worker.threads()
    first, first_parked = _park(h, 20)
    time.sleep(REVIEW_WAIT)
    waiting_connections, waiting_threads = _worker_connections(h), h.worker.threads()
    second, second_parked = _park(h, 20)
    time.sleep(REVIEW_WAIT)
    more_connections, more_threads = _worker_connections(h), h.worker.threads()
    runs = first + second
    ids = [r for _, r in runs]
    all_waiting = first_parked and second_parked
    h.worker.kill()
    h.worker.start()
    time.sleep(3)
    still_waiting = all(s == "waiting_review" for s in h.statuses(ids).values())
    for org_id, run_id in runs:
        h.approve(org_id, run_id)
    resumed = h.drive(runs, timeout=180)
    # 20 more waiting runs must not add anything close to 20 threads or connections.
    no_pinning = more_connections - waiting_connections < 10 and more_threads - waiting_threads < 10
    passed = all_waiting and still_waiting and resumed and no_pinning
    record(
        "H2",
        passed,
        waiting_runs=len(runs),
        wait_seconds=REVIEW_WAIT,
        connections_idle=idle_connections,
        connections_20_waiting=waiting_connections,
        connections_40_waiting=more_connections,
        threads_idle=idle_threads,
        threads_20_waiting=waiting_threads,
        threads_40_waiting=more_threads,
        all_waiting=all_waiting,
        still_waiting_after_restart=still_waiting,
        resumed_after_signal=resumed,
        scaled_from="10 min wait in the design",
    )
    assert all_waiting, h.statuses(ids)
    assert no_pinning, (waiting_connections, more_connections, waiting_threads, more_threads)
    assert still_waiting
    assert resumed, h.statuses(ids)


# --- H1 ---------------------------------------------------------------------------------------


def diagnose(h: Harness, ids: list[uuid.UUID]) -> dict[str, object]:
    """Where unfinished runs are stuck: their status, items done, and the engine's job states
    (any engine-owned table with a `status` column), so a failure can be attributed fairly."""
    stuck = query(
        h.owner,
        "SELECT r.status, (SELECT count(*) FROM conformance.run_items i WHERE i.run_id = r.id "
        "AND i.status = 'done') AS items_done, (SELECT max(type) FILTER (WHERE seq = "
        "(SELECT max(seq) FROM conformance.run_events e2 WHERE e2.run_id = r.id)) "
        "FROM conformance.run_events e WHERE e.run_id = r.id) AS last_event "
        "FROM conformance.runs r WHERE r.id = ANY(:ids) AND r.status <> 'succeeded'",
        ids=ids,
    )
    shapes: dict[str, int] = {}
    for row in stuck:
        key = f"{row.status}/items={row.items_done}/last={row.last_event}"
        shapes[key] = shapes.get(key, 0) + 1
    engine_jobs: dict[str, object] = {}
    for (table,) in query(
        h.owner,
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema = 'engine' AND column_name = 'status'",
    ):
        rows = query(h.owner, f'SELECT status, count(*) AS n FROM engine."{table}" GROUP BY status')  # noqa: S608
        engine_jobs[table] = {r.status: r.n for r in rows}
    return {"stuck_runs": shapes, "engine_job_states": engine_jobs}


def test_h1_runs_survive_repeated_kill_9(harness: Harness) -> None:
    h = harness
    rng = random.Random(1)  # noqa: S311 - reproducible chaos, not security
    runs = [h.create_run(org=i) for i in range(CHAOS_RUNS)]
    ids = [r for _, r in runs]
    recovery: list[float] = []
    next_kill = time.monotonic() + rng.uniform(0.5, 2.5)
    max_kills = 12

    def chaos() -> None:
        nonlocal next_kill
        if h.worker.kills >= max_kills or time.monotonic() < next_kill:
            return
        h.worker.kill()
        restart_at = time.monotonic()
        seq = query(h.owner, "SELECT coalesce(max(seq), 0) FROM conformance.run_events")[0][0]
        h.worker.start()
        if h.wait_for(
            lambda: (
                query(h.owner, "SELECT coalesce(max(seq), 0) FROM conformance.run_events")[0][0]
                > seq
            ),
            60,
            every=0.05,
        ):
            recovery.append(round(time.monotonic() - restart_at, 2))
        next_kill = time.monotonic() + rng.uniform(0.5, 2.5)

    finished = h.drive(runs, timeout=CHAOS_TIMEOUT, tick=chaos)
    states = h.statuses(ids)
    repeat_calls = calls_after_completion(h, ids)
    wasted = query(
        h.owner,
        "SELECT count(*) FROM conformance.llm_log l WHERE l.run_id = ANY(:ids) AND NOT EXISTS "
        "(SELECT 1 FROM conformance.run_items i WHERE i.llm_call_id = l.id)",
        ids=ids,
    )[0][0]
    artifacts = query(
        h.owner, "SELECT count(*) FROM conformance.artifacts WHERE run_id = ANY(:ids)", ids=ids
    )[0][0]
    exact_events = sum(complete_event_set(h, r) for r in ids)
    passed = finished and repeat_calls == 0 and artifacts == len(ids) and exact_events == len(ids)
    record(
        "H1",
        passed,
        runs=len(ids),
        kills=h.worker.kills,
        succeeded=sum(s == "succeeded" for s in states.values()),
        calls_after_item_completed=repeat_calls,
        calls_lost_to_kills=wasted,
        artifacts=artifacts,
        runs_with_exact_events=exact_events,
        recovery_seconds=recovery,
        **({} if finished else diagnose(h, ids)),
    )
    assert h.worker.kills > 0, "the chaos loop never killed the worker"
    assert finished, {s: list(states.values()).count(s) for s in set(states.values())}
    assert repeat_calls == 0, f"{repeat_calls} model calls for already completed items"
    assert artifacts == len(ids)
    assert exact_events == len(ids)


def test_engine_name_is_known() -> None:
    assert ENGINE in {"stub", "custom", "dbos", "langgraph"}
