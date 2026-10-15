"""DBOS adapter for the RunEngine port (ADR-0002), as validated by the M3 conformance suite.

- API process: DBOSClient, which talks only to DBOS's system tables (enqueue, send, cancel).
- Worker process: `launch_worker` starts DBOS itself, which runs the workflows registered by
  importing `agentforge.runs.workflows`, and recovers this worker's unfinished runs on start.

ADR-0002 conditions applied here: system tables in the `engine` schema with the engine role;
a capped thread pool; a stable executor id per worker.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from dbos import DBOS, DBOSClient
from pydantic import SecretStr

from agentforge.core.settings import Settings

if TYPE_CHECKING:
    from agentforge.runs.questionnaire import QuestionnaireSteps

RUN_QUEUE = "runs"
ITEM_QUEUE = "run_items"  # partitioned by run id: per-run parallelism (gate H3)
SYSTEM_SCHEMA = "engine"
APP_NAME = "agentforge"


def run_workflow_id(run_id: UUID) -> str:
    return f"run-{run_id}"


def _url(settings: Settings) -> str:
    url: SecretStr | None = settings.engine_database_url
    if url is None:
        raise RuntimeError("AGENTFORGE_ENGINE_DATABASE_URL is not configured")
    return url.get_secret_value()


class DbosEngine:
    """Client side: enqueue, signal, cancel. Safe to construct before any worker has created
    DBOS's tables: the client connects on first use."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: DBOSClient | None = None

    @property
    def client(self) -> DBOSClient:
        if self._client is None:
            self._client = DBOSClient(
                system_database_url=_url(self._settings),
                dbos_system_schema=SYSTEM_SCHEMA,
                application_name=APP_NAME,
            )
        return self._client

    async def start(self, org_id: UUID, run_id: UUID, template_key: str) -> None:
        # Same workflow id on every call: a repeated start attaches to the existing workflow.
        await self.client.enqueue_async(
            {
                "workflow_name": f"{template_key}.run",
                "queue_name": RUN_QUEUE,
                "workflow_id": run_workflow_id(run_id),
            },
            str(org_id),
            str(run_id),
        )

    async def cancel(self, org_id: UUID, run_id: UUID) -> None:
        await self.client.cancel_workflow_async(run_workflow_id(run_id), cancel_children=True)

    async def signal_review(self, org_id: UUID, run_id: UUID) -> None:
        await self.client.send_async(
            run_workflow_id(run_id), "reviewed", "review", idempotency_key=f"review-{run_id}"
        )

    async def retry_export(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        await self.client.enqueue_async(
            {
                "workflow_name": "questionnaire.export",
                "queue_name": RUN_QUEUE,
                "workflow_id": f"{run_workflow_id(run_id)}-export-{epoch}",
            },
            str(org_id),
            str(run_id),
            epoch,
        )

    async def retry_item(self, org_id: UUID, run_id: UUID, item_index: int, epoch: int) -> None:
        await self.client.enqueue_async(
            {
                "workflow_name": "questionnaire.retry_item",
                "queue_name": ITEM_QUEUE,
                "workflow_id": f"{run_workflow_id(run_id)}-retry-{item_index}-e{epoch}",
                "queue_partition_key": str(run_id),
            },
            str(org_id),
            str(run_id),
            item_index,
            epoch,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.destroy()


async def launch_worker(
    settings: Settings,
    steps: "QuestionnaireSteps",
    *,
    item_parallelism: int = 4,
    worker_concurrency: int = 8,
) -> None:
    """Start DBOS in the worker process (creates or migrates its tables in `engine`)."""
    from agentforge.runs import workflows  # registers the workflows before launch

    workflows.configure(steps)
    DBOS(
        config={
            "name": APP_NAME,
            "system_database_url": _url(settings),
            "dbos_system_schema": SYSTEM_SCHEMA,
            "executor_id": settings.engine_executor_id,
            "max_executor_threads": settings.engine_max_threads,
            "log_level": "WARNING",
        }
    )
    DBOS.launch()
    await DBOS.register_queue_async(RUN_QUEUE)
    # Per run: at most `item_parallelism` items at once (gate H3). Per worker, across runs: at
    # most `worker_concurrency`, so 50 runs never mean 200 model calls in flight.
    await DBOS.register_queue_async(
        ITEM_QUEUE,
        partition_concurrency=item_parallelism,
        worker_concurrency=worker_concurrency,
        polling_interval_sec=0.5,
    )


def shutdown_worker() -> None:
    DBOS.destroy(workflow_completion_timeout_sec=0)
