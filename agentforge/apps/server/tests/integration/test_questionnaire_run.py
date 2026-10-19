"""A questionnaire run end to end (R1-33): the API in this process, a real worker process running
DBOS (as in production), the fake model, and the 40-question Northwind questionnaire."""

import functools
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pydantic import SecretStr
from sqlalchemy import Engine, text

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.db.engine import create_sync_engine
from agentforge.runs.service import NewItem
from tools.demo_corpus import ROOT as DEMO_ROOT
from tools.demo_corpus import _write_xlsx  # pyright: ignore[reportPrivateUsage]

from .conftest import Database, UserFactory, login, settings_for
from .test_knowledge_flow import services_of

POLICIES = DEMO_ROOT / "corpus/northwind/policies"
WAIT = 180.0


def worker_env(database: Database, blobs: Path, *, latency_ms: float = 0.0) -> dict[str, str]:
    return {
        **os.environ,
        "AGENTFORGE_ENV": "test",
        "AGENTFORGE_DATABASE_URL": database.url("agentforge_app"),
        "AGENTFORGE_ENGINE_DATABASE_URL": database.url("agentforge_engine"),
        "AGENTFORGE_MODEL_PROVIDER": "fake",
        "AGENTFORGE_FAKE_LATENCY_MS": str(latency_ms),
        "AGENTFORGE_BLOB_ROOT": str(blobs),
        "AGENTFORGE_WORKER_POLL_SECONDS": "0.2",
        "AGENTFORGE_WORKER_HEARTBEAT_PATH": str(blobs.parent / "worker.heartbeat"),
        "AGENTFORGE_ENGINE_EXECUTOR_ID": "test-worker",
        "AGENTFORGE_LOG_LEVEL": "WARNING",
    }


@dataclass
class Stack:
    client: TestClient
    csrf: str
    reviewer: TestClient
    reviewer_csrf: str
    owner: Engine
    blobs: Path
    kb_id: str
    worker: subprocess.Popen[bytes]
    worker_log: BinaryIO

    def post(self, path: str, body: object | None = None, *, as_reviewer: bool = False) -> Any:
        client, csrf = (
            (self.reviewer, self.reviewer_csrf) if as_reviewer else (self.client, self.csrf)
        )
        return client.post(path, json=body, headers={"X-CSRF-Token": csrf})

    def query(self, sql: str, **params: Any) -> list[Any]:
        with self.owner.connect() as connection:
            return list(connection.execute(text(sql), params).all())

    def run(self, run_id: str) -> dict[str, Any]:
        return self.client.get(f"/v1/runs/{run_id}").json()

    def wait(
        self, run_id: str, predicate: Callable[[dict[str, Any]], bool], timeout: float = WAIT
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            run = self.run(run_id)
            if predicate(run):
                return run
            if run["status"] in {"failed", "cancelled"} and not predicate(run):
                raise AssertionError(f"run ended {run['status']}: {run['error']}")
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting; run is {run['status']}")
            if self.worker.poll() is not None:
                raise AssertionError("the worker process died")
            time.sleep(0.3)

    def upload_questionnaire(self, data: bytes, name: str = "northwind.xlsx") -> dict[str, Any]:
        response = self.client.post(
            "/v1/questionnaires",
            content=data,
            headers={
                "X-CSRF-Token": self.csrf,
                "X-Filename": quote(name),
                "Content-Type": "application/octet-stream",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    def start(self, questionnaire_id: str) -> str:
        response = self.post(
            "/v1/runs", {"questionnaire_id": questionnaire_id, "knowledge_base_ids": [self.kb_id]}
        )
        assert response.status_code == 201, response.text
        return str(response.json()["id"])


@pytest.fixture(scope="module")
def northwind_xlsx(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    source = json.loads((DEMO_ROOT / "questionnaire/northwind-questionnaire.json").read_text())
    path = tmp_path_factory.mktemp("q") / "northwind.xlsx"
    _write_xlsx(source["questions"], path, source["title"])
    return path.read_bytes()


def spawn_worker(
    database: Database, blobs: Path, log: BinaryIO, *, latency_ms: float, **env: str
) -> subprocess.Popen[bytes]:
    """The worker process as in production; `env` adds settings (e.g. fault injection)."""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from agentforge.cli import main; raise SystemExit(main())",
            "worker",
        ],
        env={**worker_env(database, blobs, latency_ms=latency_ms), **env},
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def start_stack(
    database: Database,
    make_user: UserFactory,
    root: Path,
    *,
    latency_ms: float = 0.0,
    **worker_settings: str,
) -> Iterator[Stack]:
    blobs = root / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    settings = settings_for(
        database,
        model_provider="fake",
        blob_root=blobs,
        engine_database_url=SecretStr(database.url("agentforge_engine")),
        rate_runs_per_minute=1000,  # the durability test starts 50 runs at once
    )
    log = (root / "worker.log").open("ab")
    worker = spawn_worker(database, blobs, log, latency_ms=latency_ms, **worker_settings)
    owner = create_sync_engine(database.url("agentforge_owner"))
    stack: Stack | None = None
    try:
        with (
            TestClient(create_app(settings)) as client,
            TestClient(create_app(settings)) as reviewer,
        ):
            csrf = login(client, make_user(Role.MEMBER))
            reviewer_csrf = login(reviewer, make_user(Role.MEMBER, can_review=True))
            kb = client.post(
                "/v1/knowledge-bases",
                json={"name": f"Northwind {time.time_ns()}"},
                headers={"X-CSRF-Token": csrf},
            ).json()["id"]
            for path in sorted(POLICIES.glob("*.md")):
                response = client.post(
                    f"/v1/knowledge-bases/{kb}/documents",
                    content=path.read_bytes(),
                    headers={
                        "X-CSRF-Token": csrf,
                        "X-Filename": quote(path.name),
                        "Content-Type": "application/octet-stream",
                    },
                )
                assert response.status_code == 201, response.text
            stack = Stack(client, csrf, reviewer, reviewer_csrf, owner, blobs, kb, worker, log)
            deadline = time.monotonic() + 120
            while (
                client.get("/v1/knowledge-bases").json()[-1]["ready_count"]
                < len(list(POLICIES.glob("*.md")))
                and time.monotonic() < deadline
            ):  # the worker indexes the policies
                time.sleep(0.5)
            yield stack
    finally:
        current = stack.worker if stack is not None else worker  # a test may restart it
        current.terminate()
        try:
            current.wait(timeout=20)
        except subprocess.TimeoutExpired:
            current.kill()
        log.close()
        owner.dispose()


@pytest.fixture(scope="module")
def stack(database: Database, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    """One worker for the module (two would share DBOS's queues). Fake model calls take 0.3 s:
    a 40-item run still takes seconds, and the cancel test has time to cancel."""
    # make_user is function-scoped; this module-scoped stack builds an equivalent factory.
    yield from start_stack(
        database, _user_factory(database), tmp_path_factory.mktemp("stack"), latency_ms=300
    )


def _user_factory(database: Database) -> UserFactory:
    import asyncio
    import uuid as uuid_module

    from agentforge.auth.users import create_user
    from agentforge.db.engine import create_app_engine, create_sessionmaker
    from agentforge.db.migrate import DEFAULT_ORG_ID

    from .conftest import PASSWORD

    def factory(role: Role = Role.MEMBER, can_review: bool = False) -> str:
        email = f"user-{uuid_module.uuid4().hex[:10]}@example.test"

        async def run() -> None:
            engine = create_app_engine(database.url("agentforge_app"), pool_size=1)
            try:
                await create_user(
                    create_sessionmaker(engine),
                    org_id=DEFAULT_ORG_ID,
                    email=email,
                    password=PASSWORD,
                    role=role,
                    can_review=can_review,
                )
            finally:
                await engine.dispose()

        asyncio.run(run())
        return email

    return factory


# --- the full run ---------------------------------------------------------------------------


def test_a_40_item_questionnaire_runs_to_an_exported_file(
    stack: Stack, northwind_xlsx: bytes
) -> None:
    questionnaire = stack.upload_questionnaire(northwind_xlsx)
    run_id = stack.start(questionnaire["id"])
    run = stack.wait(run_id, lambda r: r["status"] == "waiting_review")
    assert run["item_count"] == 40
    assert run["items_done"] == 40

    items = stack.client.get(f"/v1/runs/{run_id}/items").json()
    assert {i["status"] for i in items} <= {"answered", "insufficient_evidence"}
    assert sum(i["status"] == "answered" for i in items) >= 20, "fake mode should answer most"
    answered = next(i for i in items if i["status"] == "answered")
    assert answered["claims"]
    assert answered["evidence"]

    # Bounded fan-out (gate H3): never more than 4 items answered at once in this run. Counted
    # in event order (seq), not by timestamps: a WSL2 clock that steps back made timestamped
    # steps appear to overlap that never did.
    events = stack.query(
        "SELECT type FROM app.run_events WHERE run_id = :r "
        "AND type IN ('item_started', 'item_done', 'item_failed') ORDER BY seq",
        r=run_id,
    )
    assert sum(e.type == "item_started" for e in events) == 40
    peak = current = 0
    for event in events:
        current += 1 if event.type == "item_started" else -1
        peak = max(peak, current)
    assert peak <= 4, f"{peak} items answered at once"
    steps = stack.query(
        "SELECT count(*) FROM app.step_records WHERE run_id = :r AND node_id = 'answer'", r=run_id
    )
    assert steps[0][0] == 40

    # Every model call is linked to its run and step (trace view).
    unlinked = stack.query(
        "SELECT count(*) FROM app.llm_calls WHERE run_id = :r AND purpose = 'draft' "
        "AND step_id IS NULL",
        r=run_id,
    )[0][0]
    assert unlinked == 0

    # The trace (R1-38): every step attempt, budget used against the limits, and each step's
    # own model calls.
    trace = stack.client.get(f"/v1/runs/{run_id}/trace").json()
    nodes = [s["node_id"] for s in trace["steps"]]
    assert nodes.count("answer") == 40
    assert "plan" in nodes
    assert trace["llm_calls"] >= 40
    assert trace["max_llm_calls"] == 3000  # the default limit (run_max_llm_calls)
    assert trace["seconds"] > 0
    step = next(s for s in trace["steps"] if s["node_id"] == "answer" and s["item_index"] == 0)
    calls = stack.client.get(f"/v1/runs/{run_id}/steps/{step['id']}/calls").json()
    draft = next(c for c in calls if c["purpose"] == "draft")
    assert draft["request"]
    assert draft["response"]
    items_out = stack.client.get(f"/v1/runs/{run_id}/items").json()
    assert items_out[0]["plan"]["planner_version"]  # the plan behind each item

    # Review: a Member without can_review cannot decide; the reviewer bulk-approves verified
    # answers, edits item 0, rejects item 1, approves the rest, and completes the review.
    review = f"/v1/runs/{run_id}/review"
    assert stack.post(f"{review}/items/0", {"decision": "approved"}).status_code == 403
    stack.post(f"{review}/approve-supported", as_reviewer=True)
    edited = stack.post(
        f"{review}/items/0",
        {"decision": "edited", "edited_answer": "Edited by the reviewer."},
        as_reviewer=True,
    )
    assert edited.status_code == 200, edited.text
    rejected = stack.post(
        f"{review}/items/1", {"decision": "rejected", "reason": "Not our policy."}, as_reviewer=True
    )
    assert rejected.status_code == 200, rejected.text
    for item in stack.client.get(review).json()["items"]:
        if item["decision"] is None:
            stack.post(
                f"{review}/items/{item['item_index']}", {"decision": "approved"}, as_reviewer=True
            )
    completed = stack.post(f"{review}/complete", as_reviewer=True)
    assert completed.status_code == 200, completed.text
    run = stack.wait(run_id, lambda r: r["status"] == "succeeded")
    assert run["usage"]["llm_calls"] >= 40

    [artifact] = stack.query(
        "SELECT epoch, blob_key, filename FROM app.artifacts WHERE run_id = :r", r=run_id
    )
    assert artifact.filename == "northwind - answered.xlsx"
    sheet = load_workbook(stack.blobs / artifact.blob_key).active
    assert sheet is not None
    assert sheet["D1"].value == "Answer"
    assert sheet["E1"].value == "Notes"  # the customer's own column is left alone
    assert sheet["F1"].value == "Sources"
    # Row 2 is item 0 (edited), row 3 is item 1 (rejected: left blank for a person to fill).
    assert sheet["D2"].value == "Edited by the reviewer."
    assert sheet["D3"].value in (None, "")
    assert sheet["F3"].value in (None, "")
    by_row = {i["question"]: i for i in items}
    row_four = by_row[sheet["C4"].value]
    expected = (
        row_four["answer"]
        if row_four["status"] == "answered"
        else "Insufficient evidence in the knowledge base."
    )
    assert sheet["D4"].value == expected
    filled = sum(1 for r in range(2, 42) if sheet[f"D{r}"].value)
    assert filled == 39

    events = [e["type"] for e in stack.client.get(f"/v1/runs/{run_id}/events").json()]
    for expected_type in (
        "run_created",
        "run_started",
        "plan_done",
        "review_waiting",
        "review_done",
        "export_done",
        "run_succeeded",
    ):
        assert events.count(expected_type) == 1, (expected_type, events.count(expected_type))
    assert events.count("item_done") == 40

    # A user-initiated re-export is a new epoch and a new artifact; the first one is kept.
    response = stack.post(f"/v1/runs/{run_id}/export/retry")
    assert response.status_code == 202
    assert response.json()["epoch"] == 2
    stack.wait(run_id, lambda r: r["status"] == "succeeded" and r["epoch"] == 2)
    epochs = [
        a.epoch
        for a in stack.query(
            "SELECT epoch FROM app.artifacts WHERE run_id = :r ORDER BY epoch", r=run_id
        )
    ]
    assert epochs == [1, 2]


def test_retrying_one_item_answers_only_that_item(stack: Stack, northwind_xlsx: bytes) -> None:
    run_id = stack.start(stack.upload_questionnaire(northwind_xlsx)["id"])
    stack.wait(run_id, lambda r: r["status"] == "waiting_review")
    before = stack.query(
        "SELECT item_index, count(*) AS n FROM app.step_records WHERE run_id = :r "
        "AND node_id = 'answer' GROUP BY item_index",
        r=run_id,
    )
    assert {r.n for r in before} == {1}
    decided = stack.post(
        f"/v1/runs/{run_id}/review/items/3",
        {"decision": "rejected", "reason": "Too vague."},
        as_reviewer=True,
    )
    assert decided.status_code == 200

    response = stack.post(f"/v1/runs/{run_id}/items/3/retry")
    assert response.status_code == 202, response.text
    epoch = response.json()["epoch"]
    again: list[Any] = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        again = stack.query(
            "SELECT item_index, epoch, status FROM app.step_records WHERE run_id = :r "
            "AND node_id = 'answer' AND epoch = :e",
            r=run_id,
            e=epoch,
        )
        if again and again[0].status == "succeeded":
            break
        time.sleep(0.3)
    assert [(a.item_index, a.status) for a in again] == [(3, "succeeded")]
    assert stack.run(run_id)["status"] == "waiting_review"
    # The new answer needs a new decision: the earlier rejection was cleared.
    item = next(
        i
        for i in stack.client.get(f"/v1/runs/{run_id}/review").json()["items"]
        if i["item_index"] == 3
    )
    assert item["decision"] is None
    assert item["reason"] is None
    assert stack.post(f"/v1/runs/{run_id}/items/999/retry").status_code == 404


def test_budget_exceeded_fails_the_run_visibly(stack: Stack, northwind_xlsx: bytes) -> None:
    questionnaire = stack.upload_questionnaire(northwind_xlsx)
    svc = services_of(stack.client)
    session = stack.client.get("/v1/auth/session").json()
    run_id = stack.client.portal.call(  # type: ignore[union-attr]
        functools.partial(
            svc.runs.start_questionnaire,
            UUID(session["org_id"]),
            UUID(session["user_id"]),
            questionnaire_id=UUID(questionnaire["id"]),
            items=[
                NewItem(question=i["question"], source_ref=str(i["row"]))
                for i in questionnaire["items"]
            ],
            knowledge_base_ids=[UUID(stack.kb_id)],
            title="Budget test",
            budgets={"max_llm_calls": 12},
        )
    )
    run = stack.wait(str(run_id), lambda r: r["status"] == "failed")
    assert run["error_class"] == "budget_exceeded"
    statuses = [i["status"] for i in stack.client.get(f"/v1/runs/{run_id}/items").json()]
    assert "failed" in statuses


def test_cancel_stops_new_items_within_two_seconds(stack: Stack, northwind_xlsx: bytes) -> None:
    s = stack
    run_id = s.start(s.upload_questionnaire(northwind_xlsx)["id"])
    deadline = time.monotonic() + 60
    while not s.query(
        "SELECT 1 FROM app.step_records WHERE run_id = :r AND node_id = 'answer' "
        "AND status = 'succeeded' LIMIT 1",
        r=run_id,
    ):
        assert time.monotonic() < deadline, "no item finished"
        time.sleep(0.2)
    cancelled_at = s.query("SELECT clock_timestamp() AS now")[0].now
    response = s.post(f"/v1/runs/{run_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    time.sleep(4)
    late = s.query(
        "SELECT count(*) FROM app.step_records WHERE run_id = :r AND node_id = 'answer' "
        "AND started_at > :t + interval '2 seconds'",
        r=run_id,
        t=cancelled_at,
    )[0][0]
    done = s.query(
        "SELECT count(*) FROM app.run_items WHERE run_id = :r AND status <> 'pending'", r=run_id
    )[0][0]
    assert late == 0, f"{late} items started more than 2 s after cancel"
    assert done < 40, "the run finished before it could be cancelled"
