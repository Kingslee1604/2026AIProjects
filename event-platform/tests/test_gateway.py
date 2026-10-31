import json
import pathlib
import uuid

import pytest
from confluent_kafka import KafkaError, KafkaException
from fastapi.testclient import TestClient

from eventplat.events import UsageEvent
from eventplat.gateway import create_app


class FakePublisher:
    def __init__(self, error: Exception | None = None) -> None:
        self.published: list[UsageEvent] = []
        self.error = error

    def publish(self, event: UsageEvent) -> None:
        if self.error is not None:
            raise self.error
        self.published.append(event)

    def close(self) -> None:
        pass


def body(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "event_id": str(uuid.uuid4()),
        "tenant_id": "acme",
        "model": "claude-sonnet-5-5",
        "input_tokens": 1200,
        "output_tokens": 300,
        "occurred_at": "2026-10-27T10:00:00Z",
    }
    return record | overrides


def test_stored_record_returns_202() -> None:
    publisher = FakePublisher()
    record = body()
    with TestClient(create_app(lambda: publisher)) as client:
        response = client.post("/v1/usage", json=record)
    assert response.status_code == 202
    assert response.json() == {"event_id": record["event_id"], "status": "stored"}
    assert [str(e.event_id) for e in publisher.published] == [record["event_id"]]


@pytest.mark.parametrize(
    "bad",
    [
        {"input_tokens": -1},
        {"tenant_id": ""},
        {"occurred_at": "2026-10-27T10:00:00"},  # no time zone
        {"surprise": "field"},
    ],
)
def test_invalid_record_is_rejected(bad: dict[str, object]) -> None:
    publisher = FakePublisher()
    with TestClient(create_app(lambda: publisher)) as client:
        response = client.post("/v1/usage", json=body(**bad))
    assert response.status_code == 422
    assert publisher.published == []


@pytest.mark.parametrize(
    "error",
    [BufferError(), TimeoutError(), KafkaException(KafkaError(KafkaError._MSG_TIMED_OUT))],
)
def test_unstored_record_returns_503(error: Exception) -> None:
    with TestClient(create_app(lambda: FakePublisher(error))) as client:
        response = client.post("/v1/usage", json=body())
    assert response.status_code == 503


def test_record_round_trips_through_bytes() -> None:
    event = UsageEvent.model_validate(body())
    assert UsageEvent.from_bytes(event.to_bytes()) == event
    assert event.key == b"acme"


def test_readers_ignore_fields_added_by_newer_producers() -> None:
    record = body()
    newer = {**record, "cached_tokens": 42}
    assert UsageEvent.model_validate(newer) == UsageEvent.model_validate(record)


def test_schema_change_is_deliberate() -> None:
    """Changing UsageEvent means updating schemas/usage-event.json in the same change.

    Safe: add a field with a default. Breaking: remove, rename or retype a field, or add one
    without a default; that needs a new topic (ADR-0003).
    """
    snapshot = pathlib.Path(__file__).parents[1] / "schemas" / "usage-event.json"
    assert UsageEvent.model_json_schema() == json.loads(snapshot.read_text())
