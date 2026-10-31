"""HTTP gateway: authorizes calls against budgets, and stores the usage records they produce.

POST /v1/authorize, before a model call: 200 if the tenant's budget covers the call's maximum
tokens, 402 if not (eventplat.allowance). POST /v1/usage, after the call: a 202 means the
record is in Kafka on every in-sync replica (acks=all). Usage is always stored, even over
budget, because it is the bill; budgets are enforced only at authorize. Any failure answers
503, and the caller retries with the same event_id, so a retry can never double-count.

Run: uvicorn --factory eventplat.gateway:create_app
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager
from typing import Protocol
from uuid import UUID

from confluent_kafka import KafkaError, KafkaException, Message, Producer
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from eventplat.allowance import Allowances, BudgetUnavailable, HttpBudgetClient
from eventplat.config import Settings
from eventplat.events import UsageEvent, UsageRequest

PUBLISH_TIMEOUT_S = 10.0


class Publisher(Protocol):
    def publish(self, event: UsageEvent) -> None: ...
    def close(self) -> None: ...


class KafkaPublisher:
    """Thread-safe: request threads produce, one background thread serves delivery reports."""

    def __init__(self, bootstrap: str, topic: str) -> None:
        self._topic = topic
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap,
                "enable.idempotence": True,  # no duplicates from the producer's own retries
                "acks": "all",
                "linger.ms": 5,
            }
        )
        self._stop = threading.Event()
        self._poller = threading.Thread(target=self._poll, name="kafka-delivery", daemon=True)
        self._poller.start()

    def _poll(self) -> None:
        while not self._stop.is_set():
            self._producer.poll(0.1)

    def publish(self, event: UsageEvent) -> None:
        delivered: Future[None] = Future()

        def on_delivery(err: KafkaError | None, _msg: Message) -> None:
            if err is None:
                delivered.set_result(None)
            else:
                delivered.set_exception(KafkaException(err))

        self._producer.produce(
            self._topic, key=event.key, value=event.to_bytes(), on_delivery=on_delivery
        )
        delivered.result(timeout=PUBLISH_TIMEOUT_S)

    def close(self) -> None:
        self._producer.flush(PUBLISH_TIMEOUT_S)
        self._stop.set()
        self._poller.join()


def kafka_publisher_from_env() -> Publisher:
    settings = Settings.from_env()
    return KafkaPublisher(settings.kafka_bootstrap, settings.usage_topic)


def allowances_from_env() -> Allowances:
    settings = Settings.from_env()
    return Allowances(HttpBudgetClient(settings.budget_url), settings.allowance_tokens)


class AuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID  # the same id the call's usage record will carry
    tenant_id: str = Field(min_length=1, max_length=64)
    max_tokens: int = Field(gt=0, le=10_000_000)


def create_app(
    publisher_factory: Callable[[], Publisher] | None = None,
    allowances_factory: Callable[[], Allowances] | None = None,
) -> FastAPI:
    make_publisher = publisher_factory or kafka_publisher_from_env
    make_allowances = allowances_factory or allowances_from_env

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.publisher = make_publisher()
        app.state.allowances = make_allowances()
        yield
        app.state.allowances.release()
        app.state.publisher.close()

    app = FastAPI(title="eventplat gateway", lifespan=lifespan)

    @app.post("/v1/authorize")
    def authorize(req: AuthorizeRequest, request: Request) -> dict[str, str | bool]:
        allowances: Allowances = request.app.state.allowances
        try:
            approved = allowances.authorize(req.event_id, req.tenant_id, req.max_tokens)
        except BudgetUnavailable as exc:
            raise HTTPException(503, "budget service unavailable, retry") from exc
        if not approved:
            raise HTTPException(402, "budget exceeded")
        return {"event_id": str(req.event_id), "authorized": True}

    @app.post("/v1/usage", status_code=202)
    def record_usage(event: UsageRequest, request: Request) -> dict[str, str]:
        publisher: Publisher = request.app.state.publisher
        try:
            publisher.publish(event)
        except BufferError as exc:  # local send queue is full: push back on the caller
            raise HTTPException(503, "gateway busy, retry with the same event_id") from exc
        except (KafkaException, TimeoutError) as exc:
            raise HTTPException(503, "not stored, retry with the same event_id") from exc
        allowances: Allowances = request.app.state.allowances
        allowances.settle(event)
        return {"event_id": str(event.event_id), "status": "stored"}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
