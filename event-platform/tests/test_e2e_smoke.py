"""End to end through the running stack: gateway -> Kafka -> ledger -> Postgres.

Sends N records, then re-sends some of them (a client retrying), and checks that Postgres
holds exactly N rows with exactly the tokens sent.
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest

from eventplat.config import Settings
from eventplat.db import Database

pytestmark = pytest.mark.e2e

GATEWAY = "http://127.0.0.1:8100"
RECORDS = 1000
RETRIES = 100
TENANTS = [f"tenant-{i}" for i in range(5)]


def record(i: int, event_id: str) -> dict[str, object]:
    return {
        "event_id": event_id,
        "tenant_id": TENANTS[i % len(TENANTS)],
        "model": "claude-haiku-5-5",
        "input_tokens": 100 + i,
        "output_tokens": i,
        "occurred_at": "2026-10-27T10:00:00Z",
    }


def test_every_record_lands_once() -> None:
    ids = [str(uuid.uuid4()) for _ in range(RECORDS)]
    records = [record(i, event_id) for i, event_id in enumerate(ids)]
    sends = records + records[:RETRIES]
    expected_tokens = sum(100 + 2 * i for i in range(RECORDS))

    with httpx2.Client(base_url=GATEWAY, timeout=30) as client, ThreadPoolExecutor(16) as pool:
        statuses = list(pool.map(lambda r: client.post("/v1/usage", json=r).status_code, sends))
    assert statuses == [202] * len(sends)

    rows, tokens = 0, 0
    with Database(Settings.from_env()) as db:
        deadline = time.monotonic() + 30
        while rows < RECORDS and time.monotonic() < deadline:
            time.sleep(0.5)
            [[rows, tokens]] = db.query(
                "SELECT count(*), coalesce(sum(input_tokens + output_tokens), 0) "
                "FROM usage_events WHERE event_id = ANY(CAST(:ids AS uuid[]))",
                ids=ids,
            )

    assert rows == RECORDS
    assert tokens == expected_tokens
