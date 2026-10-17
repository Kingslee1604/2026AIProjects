"""The RunEngine port (architecture §7.4). The run service depends on this, not on DBOS, so the
fallback engine (ADR-0002) could replace DBOS without touching the API or the UI."""

from typing import Protocol
from uuid import UUID


class RunEngine(Protocol):
    async def start(self, org_id: UUID, run_id: UUID, template_key: str) -> None:
        """Begin executing a run whose row exists in status 'queued'. Idempotent: the start
        reconciler may call it again for a run that has not started (gate H7)."""
        ...

    async def cancel(self, org_id: UUID, run_id: UUID) -> None:
        """Stop scheduling new steps (gate H5). The service marks the run cancelled."""
        ...

    async def signal_review(self, org_id: UUID, run_id: UUID) -> None:
        """Resume a run waiting for review; the decisions are already in the app tables."""
        ...

    async def retry_export(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        """User-initiated retry of the export with a new epoch (gate H4)."""
        ...

    async def retry_item(self, org_id: UUID, run_id: UUID, item_index: int, epoch: int) -> None:
        """User-initiated retry of one item: only that item is answered again."""
        ...


class NoEngine:
    """Used when no engine database is configured (tests, API-only setups): runs can be read
    but not started."""

    async def start(self, org_id: UUID, run_id: UUID, template_key: str) -> None:
        raise RuntimeError("no run engine configured (AGENTFORGE_ENGINE_DATABASE_URL)")

    async def cancel(self, org_id: UUID, run_id: UUID) -> None:
        return None

    async def signal_review(self, org_id: UUID, run_id: UUID) -> None:
        raise RuntimeError("no run engine configured")

    async def retry_export(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        raise RuntimeError("no run engine configured")

    async def retry_item(self, org_id: UUID, run_id: UUID, item_index: int, epoch: int) -> None:
        raise RuntimeError("no run engine configured")
