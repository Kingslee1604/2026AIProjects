import uuid

from eventplat.events import UsageEvent
from eventplat.totals import DedupeWindow, PartitionState, Total


def event(
    tenant: str = "acme", occurred_at: str = "2026-10-27T10:00:00Z", **kw: object
) -> UsageEvent:
    return UsageEvent.model_validate(
        {
            "event_id": str(kw.get("event_id", uuid.uuid4())),
            "tenant_id": tenant,
            "model": "claude-sonnet-5-5",
            "input_tokens": kw.get("input_tokens", 10),
            "output_tokens": kw.get("output_tokens", 5),
            "occurred_at": occurred_at,
        }
    )


def test_window_catches_retry_within_size() -> None:
    window = DedupeWindow(size=3)
    first = uuid.uuid4()
    window.record(0, first)
    window.record(1, uuid.uuid4())
    window.record(2, uuid.uuid4())
    assert window.seen(first)


def test_window_forgets_ids_older_than_size() -> None:
    """The known limit (ADR-0002): a retry this late is not caught by the Kafka path."""
    window = DedupeWindow(size=3)
    first = uuid.uuid4()
    for offset, event_id in enumerate([first, uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]):
        window.record(offset, event_id)
    assert not window.seen(first)
    assert len(window) == 3


def test_rebuilt_window_matches_live_window() -> None:
    """Restoring replays the last `size` offsets; the result must equal the live window."""
    ids = [uuid.uuid4() for _ in range(6)]
    stream = [(o, ids[o % 4]) for o in range(40)]  # plenty of repeats
    live = DedupeWindow(size=7)
    for offset, event_id in stream:
        live.record(offset, event_id)

    rebuilt = DedupeWindow(size=7)
    committed = len(stream)
    for offset, event_id in stream[committed - 7 :]:
        rebuilt.record(offset, event_id)

    assert {i for i in ids if live.seen(i)} == {i for i in ids if rebuilt.seen(i)}
    assert len(live) == len(rebuilt)


def test_state_adds_tokens_and_drops_duplicates() -> None:
    state = PartitionState(DedupeWindow(size=100))
    a = event(input_tokens=100, output_tokens=1)
    b = event(input_tokens=200, output_tokens=2)
    assert state.apply(0, a) is not None
    assert state.apply(1, b) is not None
    assert state.apply(2, a) is None  # a retry of a
    [total] = state.totals.values()
    assert (total.input_tokens, total.output_tokens, total.events) == (300, 3, 2)
    assert state.duplicates == 1


def test_day_is_utc() -> None:
    state = PartitionState(DedupeWindow(size=10))
    total = state.apply(0, event(occurred_at="2026-10-27T23:30:00-05:00"))
    assert total is not None
    assert total.day == "2026-10-28"


def test_total_round_trips_through_bytes() -> None:
    total = Total("acme", "2026-10-27", 1, 2, 3)
    assert Total.from_bytes(total.to_bytes()) == total
    assert total.key == "acme|2026-10-27"
