"""Both exactly-once paths agree with what was sent: the Kafka totals (eventplat.totals) and the
Postgres totals kept by the ledger. Records go through the gateway, with client retries mixed in.
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest

from eventplat.config import Settings
from eventplat.db import Database
from eventplat.totals import read_totals

pytestmark = pytest.mark.e2e

GATEWAY = "http://127.0.0.1:8100"
RECORDS = 600
RETRIES = 150


def test_kafka_and_database_totals_match_what_was_sent() -> None:
    settings = Settings.from_env()
    run = uuid.uuid4().hex[:8]
    tenants = [f"totals-{run}-{i}" for i in range(3)]
    records = [
        {
            "event_id": str(uuid.uuid4()),
            "tenant_id": tenants[i % 3],
            "model": "claude-haiku-5-5",
            "input_tokens": i,
            "output_tokens": 1,
            "occurred_at": "2026-10-27T10:00:00Z",
        }
        for i in range(RECORDS)
    ]
    sends = records + records[-RETRIES:]
    with httpx2.Client(base_url=GATEWAY, timeout=30) as client, ThreadPoolExecutor(16) as pool:
        statuses = list(pool.map(lambda r: client.post("/v1/usage", json=r).status_code, sends))
    assert statuses == [202] * len(sends)

    expected = {
        f"{t}|2026-10-27": (
            sum(i for i in range(RECORDS) if i % 3 == n),
            RECORDS // 3,
            RECORDS // 3,
        )
        for n, t in enumerate(tenants)
    }

    def kafka_totals() -> dict[str, tuple[int, int, int]]:
        found = read_totals(settings.kafka_bootstrap, settings.totals_topic)
        return {
            k: (v.input_tokens, v.output_tokens, v.events)
            for k, v in found.items()
            if k in expected
        }

    def db_totals() -> dict[str, tuple[int, int, int]]:
        with Database(settings) as db:
            rows = db.query(
                "SELECT tenant_id, day::text, input_tokens, output_tokens, events "
                "FROM usage_totals WHERE tenant_id = ANY(CAST(:t AS text[]))",
                t=tenants,
            )
        return {f"{t}|{d}": (i, o, e) for t, d, i, o, e in rows}

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and (kafka_totals() != expected or db_totals() != expected):
        time.sleep(1)
    assert kafka_totals() == expected
    assert db_totals() == expected
