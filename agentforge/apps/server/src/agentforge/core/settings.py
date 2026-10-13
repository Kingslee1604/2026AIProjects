"""Process configuration, read from AGENTFORGE_* environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
