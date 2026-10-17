"""The RunEngine port (architecture §7.4) as the conformance harness exercises it.

Client side (the API process): start, cancel, signal_review, retry_export.
Worker side (a separate process the tests kill with SIGKILL): run_worker.
Run status is read from the app table `conformance.runs`, never from engine internals.
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from spikes.engine.harness.steps import Steps


@dataclass(frozen=True, slots=True)
class EngineConfig:
    app_url: str  # agentforge_app: steps (RLS applies)
    engine_url: str  # agentforge_engine: engine-owned tables in the `engine` schema
    worker_id: str = "worker-1"
    lease_seconds: float = 6.0  # how soon a crashed worker's work is picked up again


class RunEngine(Protocol):
    name: str

    async def open(self) -> None:
        """Connect (and create engine tables if needed). Called once per process."""

    async def close(self) -> None: ...

    async def start(self, org_id: UUID, run_id: UUID) -> None:
        """Begin executing a run whose row exists in status 'queued'. Must be idempotent:
        the reconciler may call it again for a run that has not started yet (H7)."""

    async def cancel(self, org_id: UUID, run_id: UUID) -> None:
        """Stop scheduling new steps for the run (H5) and mark it cancelled."""

    async def signal_review(self, org_id: UUID, run_id: UUID, decision: str) -> None:
        """Resume a run waiting for review. The decision is already stored on the run row."""

    async def retry_export(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        """User-initiated retry of the export step with a new epoch (H4)."""

    async def run_worker(self, steps: Steps, stop: asyncio.Event) -> None:
        """Execute work until `stop` is set."""
