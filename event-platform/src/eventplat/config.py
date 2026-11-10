"""Settings read from the environment, with defaults for a stack started by `make up`."""

import os
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit


@dataclass(frozen=True)
class PostgresTarget:
    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_url(cls, url: str) -> "PostgresTarget":
        parts = urlsplit(url)
        return cls(
            host=parts.hostname or "localhost",
            port=parts.port or 5432,
            user=unquote(parts.username or "postgres"),
            password=unquote(parts.password or ""),
            database=parts.path.lstrip("/") or "postgres",
        )


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap: str
    usage_topic: str
    dead_letter_topic: str
    totals_topic: str
    database_url: str
    budget_url: str
    allowance_tokens: int
    # Another group (and database) rebuilds a second ledger from the start of the log.
    ledger_group: str
    # How long a silent consumer keeps its partitions; a crashed one that restarts within it
    # (1-2 s in the crash runs) keeps them. A consumer removed for good can hold its partitions
    # this long: with Kafka's default of 45 s, a scale-down sometimes stalled both groups for
    # ~47 s; with 10 s, never more than ~13 s (ADR-0004). The cost: a consumer that freezes
    # for more than 10 s is removed and has to restore its state.
    consumer_session_timeout_ms: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            kafka_bootstrap=os.environ.get("KAFKA_BOOTSTRAP", "localhost:29092"),
            usage_topic=os.environ.get("USAGE_TOPIC", "usage.events"),
            dead_letter_topic=os.environ.get("DEAD_LETTER_TOPIC", "usage.dlq"),
            totals_topic=os.environ.get("TOTALS_TOPIC", "usage.totals"),
            # The password comes from .env (created by `make up`; the Makefile exports it).
            database_url=os.environ.get("DATABASE_URL")
            or "postgresql://eventplat:{}@localhost:55432/eventplat".format(
                quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
            ),
            budget_url=os.environ.get("BUDGET_URL", "http://localhost:8110"),
            allowance_tokens=int(os.environ.get("ALLOWANCE_TOKENS", "10000")),
            ledger_group=os.environ.get("LEDGER_GROUP", "ledger"),
            consumer_session_timeout_ms=int(os.environ.get("CONSUMER_SESSION_TIMEOUT_MS", "10000")),
        )

    @property
    def postgres(self) -> PostgresTarget:
        return PostgresTarget.from_url(self.database_url)
