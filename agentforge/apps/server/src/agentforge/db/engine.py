"""SQLAlchemy engines. The driver is psycopg 3 (ADR-0013), used async for the app."""

from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DRIVER_SCHEME = "postgresql+psycopg"


def sqlalchemy_url(url: SecretStr | str) -> str:
    """Accept postgresql:// (or postgres://) URLs and select the psycopg driver."""
    raw = url.get_secret_value() if isinstance(url, SecretStr) else url
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return DRIVER_SCHEME + "://" + raw.removeprefix(prefix)
    raise ValueError("database URL must start with postgresql://")


def create_app_engine(url: SecretStr | str, *, pool_size: int = 5) -> AsyncEngine:
    return create_async_engine(sqlalchemy_url(url), pool_size=pool_size, pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def create_sync_engine(url: SecretStr | str) -> Engine:
    """Synchronous engine for migrations, seeding, and schema checks."""
    return create_engine(sqlalchemy_url(url), pool_pre_ping=True)
