"""Settings read from the environment, with defaults for a stack started by `make up`."""

import os
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


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

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            kafka_bootstrap=os.environ.get("KAFKA_BOOTSTRAP", "localhost:29092"),
            usage_topic=os.environ.get("USAGE_TOPIC", "usage.events"),
            dead_letter_topic=os.environ.get("DEAD_LETTER_TOPIC", "usage.dlq"),
            totals_topic=os.environ.get("TOTALS_TOPIC", "usage.totals"),
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql://eventplat:eventplat-dev@localhost:55432/eventplat"
            ),
        )

    @property
    def postgres(self) -> PostgresTarget:
        return PostgresTarget.from_url(self.database_url)
