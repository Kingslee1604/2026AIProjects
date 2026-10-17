"""Per-item review (R1-34) on real Postgres with a recording engine: decisions, permissions,
bulk approval, completion that signals the engine exactly once, audit, and tenant isolation."""

import dataclasses
import functools
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentforge.api.app import create_app
from agentforge.auth.rbac import Role
from agentforge.db.engine import create_sync_engine
from agentforge.reviews.service import ReviewService
from agentforge.runs.service import NewItem, RunService

from .conftest import Database, UserFactory, login, outsider, settings_for
from .test_knowledge_flow import services_of
from .test_runs import RecordingEngine

# item index -> (status, confidence): 0-1 verified high, 2 low, 3 insufficient, 4 skipped
ITEMS: dict[int, tuple[str, str | None]] = {
    0: ("answered", "high"),
    1: ("answered", "high"),
    2: ("answered", "low"),
    3: ("insufficient_evidence", None),
    4: ("skipped", None),
}


@dataclasses.dataclass
class Review:
    member: TestClient
    member_csrf: str
    reviewer: TestClient
    reviewer_csrf: str
    engine: RecordingEngine
    run_id: UUID
    database: Database

    def as_reviewer(self, path: str, body: object | None = None) -> Any:
        return self.reviewer.post(path, json=body, headers={"X-CSRF-Token": self.reviewer_csrf})

    def decide(self, index: int, **body: object) -> Any:
        return self.as_reviewer(f"/v1/runs/{self.run_id}/review/items/{index}", body)

    def audit(self, action: str) -> list[Any]:
        engine = create_sync_engine(self.database.url("agentforge_owner"))
        try:
            with engine.connect() as connection:
                return list(
                    connection.execute(
                        text(
                            "SELECT target, details FROM app.audit_log WHERE action = :a "
                            "AND target LIKE :t"
                        ),
                        {"a": action, "t": f"run:{self.run_id}%"},
                    ).all()
                )
        finally:
            engine.dispose()


@pytest.fixture
def app(database: Database, tmp_path: Path) -> FastAPI:
    return create_app(settings_for(database, model_provider="fake", blob_root=tmp_path / "b"))


@pytest.fixture
def review(app: FastAPI, database: Database, make_user: UserFactory) -> Iterator[Review]:
    with TestClient(app) as member, TestClient(app) as reviewer:
        member_csrf = login(member, make_user(Role.MEMBER))
        reviewer_csrf = login(reviewer, make_user(Role.MEMBER, can_review=True))
        svc = services_of(member)
        engine = RecordingEngine()
        runs = RunService(svc.sessions, engine)
        app.state.services = dataclasses.replace(
            svc, runs=runs, run_engine=engine, reviews=ReviewService(svc.sessions, runs)
        )
        session = member.get("/v1/auth/session").json()
        org_id = UUID(session["org_id"])
        run_id: UUID = member.portal.call(  # type: ignore[union-attr]
            functools.partial(
                runs.create,
                org_id,
                UUID(session["user_id"]),
                template_key="questionnaire",
                title="Review test",
                input={},
                items=[NewItem(question=f"Q{i}?", source_ref=str(i + 2)) for i in ITEMS],
            )
        )
        owner = create_sync_engine(database.url("agentforge_owner"))
        with owner.begin() as connection:
            connection.execute(
                text("UPDATE app.runs SET status = 'running' WHERE id = :r"), {"r": run_id}
            )
            for index, (status, confidence) in ITEMS.items():
                connection.execute(
                    text(
                        "UPDATE app.run_items SET status = :s, confidence = :c, answer = :a "
                        "WHERE run_id = :r AND item_index = :i"
                    ),
                    {
                        "s": status,
                        "c": confidence,
                        "a": f"Answer {index}.",
                        "r": run_id,
                        "i": index,
                    },
                )
        owner.dispose()
        member.portal.call(svc.questionnaire_steps.route, org_id, run_id)  # type: ignore[union-attr]
        yield Review(member, member_csrf, reviewer, reviewer_csrf, engine, run_id, database)


def test_route_opens_a_batch_with_bulk_approval_candidates(review: Review) -> None:
    body = review.member.get(f"/v1/runs/{review.run_id}/review").json()
    assert body["status"] == "open"
    assert [i["item_index"] for i in body["items"]] == [0, 1, 2, 3]  # skipped item 4 excluded
    assert [i["auto_accept_candidate"] for i in body["items"]] == [True, True, False, False]
    assert body["undecided"] == 4
    assert review.member.get(f"/v1/runs/{review.run_id}").json()["status"] == "waiting_review"


def test_the_queue_counts_what_is_left_to_decide(review: Review) -> None:
    def entry() -> Any:
        queue = review.member.get("/v1/reviews").json()
        return next((e for e in queue if e["run_id"] == str(review.run_id)), None)

    assert entry() == {
        "run_id": str(review.run_id),
        "title": "Review test",
        "epoch": 1,
        "total": 4,
        "undecided": 4,
        "candidates": 2,
        "created_at": entry()["created_at"],
    }
    review.as_reviewer(f"/v1/runs/{review.run_id}/review/approve-supported")
    review.decide(2, decision="approved")
    assert (entry()["undecided"], entry()["candidates"]) == (1, 0)
    review.decide(3, decision="rejected", reason="Not applicable.")
    review.as_reviewer(f"/v1/runs/{review.run_id}/review/complete")
    assert entry() is None  # completed reviews leave the queue


def test_decisions_are_validated_and_permission_checked(review: Review) -> None:
    run = review.run_id
    member_decide = review.member.post(
        f"/v1/runs/{run}/review/items/0",
        json={"decision": "approved"},
        headers={"X-CSRF-Token": review.member_csrf},
    )
    assert member_decide.status_code == 403  # Member without can_review
    assert review.decide(0, decision="edited", edited_answer="   ").status_code == 400
    assert review.decide(0, decision="rejected").status_code == 400  # a reason is required
    assert review.decide(99, decision="approved").status_code == 404
    edited = review.decide(2, decision="edited", edited_answer="We keep logs for 90 days.")
    assert edited.status_code == 200
    assert edited.json()["edited_answer"] == "We keep logs for 90 days."
    rejected = review.decide(3, decision="rejected", reason="Covered by a separate contract.")
    assert rejected.json()["reason"] == "Covered by a separate contract."
    assert {a.target for a in review.audit("review.decide")} == {
        f"run:{run}#item:2",
        f"run:{run}#item:3",
    }


def test_bulk_approval_then_completion_signals_the_engine_once(review: Review) -> None:
    run = review.run_id
    assert review.as_reviewer(f"/v1/runs/{run}/review/complete").status_code == 409  # undecided
    bulk = review.as_reviewer(f"/v1/runs/{run}/review/approve-supported")
    assert bulk.json() == {"approved": 2}
    assert review.as_reviewer(f"/v1/runs/{run}/review/approve-supported").json() == {"approved": 0}
    review.decide(2, decision="approved")
    review.decide(3, decision="rejected", reason="Not applicable to us.")

    # Two reviewers click "complete" at the same moment: one wins, the engine hears once.
    results: list[int] = []

    def complete() -> None:
        results.append(review.as_reviewer(f"/v1/runs/{run}/review/complete").status_code)

    threads = [threading.Thread(target=complete) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 409]
    assert review.engine.calls.count(("signal_review", run)) == 1
    body = review.member.get(f"/v1/runs/{run}/review").json()
    assert body["status"] == "completed"
    assert review.decide(0, decision="rejected", reason="late").status_code == 409
    assert len(review.audit("review.complete")) == 1
    assert len(review.audit("review.bulk_approve")) == 2
    events = [e["type"] for e in review.member.get(f"/v1/runs/{run}/events").json()]
    assert events.count("review_completed") == 1


def test_other_organizations_cannot_see_or_decide(
    review: Review, app: FastAPI, database: Database
) -> None:
    with outsider(app, database) as (other, csrf):  # an admin, so permission is not the reason
        assert all(e["run_id"] != str(review.run_id) for e in other.get("/v1/reviews").json())
        assert other.get(f"/v1/runs/{review.run_id}/review").status_code == 404
        decided = other.post(
            f"/v1/runs/{review.run_id}/review/items/0",
            json={"decision": "approved"},
            headers={"X-CSRF-Token": csrf},
        )
        assert decided.status_code == 404
        completed = other.post(
            f"/v1/runs/{review.run_id}/review/complete", headers={"X-CSRF-Token": csrf}
        )
        assert completed.status_code == 404
