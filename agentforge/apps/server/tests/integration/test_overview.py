"""/v1/overview: real counts for the caller's organization, and none of another's."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from .conftest import Database, outsider
from .test_qa_flow import answer_queue, app, ask, asker

__all__ = ["app", "asker"]  # fixtures, shared with test_qa_flow


def overview(client: TestClient) -> dict[str, Any]:
    response = client.get("/v1/overview")
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_the_overview_counts_this_organizations_work(
    asker: tuple[TestClient, str, str],
) -> None:
    client, csrf, kb = asker
    before = overview(client)
    assert before["knowledge_bases"] >= 1
    assert before["documents_by_status"].get("ready", 0) >= 1
    assert before["model"] == {
        "provider": "fake",
        "reachable": True,
        "chat_model": "fake",
        "chat_model_installed": True,
        "embedding_model": "fake",
        "embedding_model_installed": True,
    }

    ask(client, csrf, kb, "How long are access logs retained?")
    assert answer_queue(client) == 1
    after = overview(client)
    assert after["questions_7d"].get("answered", 0) == before["questions_7d"].get("answered", 0) + 1
    # The answer's one kept sentence was proven by an exact quote.
    assert (
        after["sentences_30d"].get("verified", 0) == before["sentences_30d"].get("verified", 0) + 1
    )
    assert after["model_calls_7d"] > before["model_calls_7d"]
    assert after["call_seconds_p50_7d"] is not None


def test_members_need_a_session_and_other_organizations_see_nothing(
    app: FastAPI, database: Database, asker: tuple[TestClient, str, str]
) -> None:
    client, _, _ = asker
    assert overview(client)["knowledge_bases"] >= 1
    with TestClient(app) as anonymous:
        assert anonymous.get("/v1/overview").status_code == 401
    with outsider(app, database) as (other, _):
        theirs = overview(other)
    assert theirs["knowledge_bases"] == 0
    assert theirs["documents_by_status"] == {}
    assert theirs["runs_by_status"] == {}
    assert theirs["questions_7d"] == {}
    assert theirs["sentences_30d"] == {}
    assert theirs["model_calls_7d"] == 0
    assert theirs["activity"] == []
