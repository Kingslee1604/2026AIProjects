"""HTTP gateway: accepts usage records and answers only after Kafka has stored them.

A 202 means the record is in Kafka on every in-sync replica (acks=all). Any failure answers
503, and the caller retries with the same event_id, so a retry can never double-count.

Run: uvicorn --factory eventplat.gateway:create_app
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager
from typing import Protocol

from confluent_kafka import KafkaError, KafkaException, Message, Producer
from fastapi import FastAPI, HTTPException, Request

from eventplat.config import Settings
from eventplat.events import UsageEvent

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


def create_app(publisher_factory: Callable[[], Publisher] | None = None) -> FastAPI:
    make_publisher = publisher_factory or kafka_publisher_from_env

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.publisher = make_publisher()
        yield
        app.state.publisher.close()

    app = FastAPI(title="eventplat gateway", lifespan=lifespan)

    @app.post("/v1/usage", status_code=202)
    def record_usage(event: UsageEvent, request: Request) -> dict[str, str]:
        publisher: Publisher = request.app.state.publisher
        try:
            publisher.publish(event)
        except BufferError as exc:  # local send queue is full: push back on the caller
            raise HTTPException(503, "gateway busy, retry with the same event_id") from exc
        except (KafkaException, TimeoutError) as exc:
            raise HTTPException(503, "not stored, retry with the same event_id") from exc
        return {"event_id": str(event.event_id), "status": "stored"}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
