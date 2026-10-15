"""Process configuration, read from AGENTFORGE_* environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTFORGE_", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    service_name: str = "agentforge"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    otel_exporter: Literal["none", "console"] = "none"

    # Loopback by default (security baseline S1). Containers override this to 0.0.0.0 and rely
    # on Compose publishing the port on 127.0.0.1 only.
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    worker_heartbeat_path: Path = Path("/tmp/agentforge-worker.heartbeat")  # noqa: S108
    worker_poll_seconds: float = Field(default=1.0, gt=0)

    ollama_base_url: str = "http://127.0.0.1:11434"

    # Model gateway (architecture §8). References look like `provider:model`.
    # model_provider=fake routes every call to the deterministic fake provider (tests, CI, UI).
    model_provider: Literal["auto", "fake"] = "auto"
    chat_model: str = "ollama:qwen3:4b"
    chat_fallback_models: list[str] = Field(default_factory=lambda: [])
    embedding_model: str = "ollama:nomic-embed-text:v1.5"
    model_timeout_seconds: float = Field(default=180.0, gt=0)
    fake_latency_ms: float = Field(default=0.0, ge=0)
    # Fault injection for durability tests: this share of fake chat calls times out, is
    # unavailable, or returns invalid JSON (seeded, so a failing run can be replayed).
    fake_fault_rate: float = Field(default=0.0, ge=0, le=0.5)
    fake_fault_seed: int = 0
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None

    # Grounded answers (architecture §10.2). On CPU, `low_confidence_only` sends only claims
    # whose quote was not found to the LLM verifier; `all` verifies every claim.
    qa_top_k: int = Field(default=6, ge=1, le=20)
    qa_max_distance: float = Field(default=0.5, gt=0, le=2)
    qa_verifier_mode: Literal["all", "low_confidence_only", "off"] = "low_confidence_only"
    qa_max_llm_calls: int = Field(default=12, ge=2, le=100)

    # Knowledge: where uploaded files are stored, and request size limits (baseline S5).
    blob_root: Path = Path("data/blobs")
    max_upload_mb: int = Field(default=25, ge=1, le=500)
    max_request_kb: int = Field(default=1024, ge=16)  # every non-upload request

    # Postgres URLs (postgresql://user:password@host:port/db). Secret: never logged.
    # database_url uses the agentforge_app role (RLS applies); migration_database_url uses the
    # schema owner and is only needed by `agentforge migrate`.
    database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    # The run engine (DBOS, ADR-0002) with the agentforge_engine role: only the `engine` schema.
    engine_database_url: SecretStr | None = None
    # Stable per worker (the Compose service name), so a restarted worker recovers its own runs.
    engine_executor_id: str = "worker-1"
    engine_max_threads: int = Field(default=32, ge=4, le=256)

    # Questionnaire runs: items answered at once per run (gate H3), and default run budgets
    # (a run that exceeds them fails as budget_exceeded; architecture §7.6).
    run_item_parallelism: int = Field(default=4, ge=1, le=16)
    # Items one worker answers at once across all runs: what the model server can serve, and
    # the most work a worker crash can lose (in-flight answers are redone after recovery).
    run_worker_concurrency: int = Field(default=8, ge=1, le=64)
    run_max_llm_calls: int = Field(default=3000, ge=10)
    run_max_seconds: float = Field(default=6 * 3600, gt=0)

    # Sessions (security baseline S3). Cookies are Secure whenever served over TLS; production
    # refuses to start without it.
    cookie_secure: bool = False
    session_idle_minutes: int = Field(default=120, ge=5)
    session_absolute_hours: int = Field(default=12, ge=1)

    # Brute-force protection: failed logins within the window, per email and per client IP.
    login_window_minutes: int = Field(default=15, ge=1)
    login_max_failures_per_email: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def _production_requires_secure_cookies(self) -> "Settings":
        if self.env == "production" and not self.cookie_secure:
            raise ValueError("AGENTFORGE_COOKIE_SECURE must be true in production (TLS required)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
