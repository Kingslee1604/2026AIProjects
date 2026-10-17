"""Candidate 1 (R1-21): DBOS Transact.

The run is one DBOS workflow; each item is a child workflow on a queue partitioned by run id
with partition_concurrency=4 (per-run fan-out limit, H3). The review wait is DBOS.recv; the
signal is DBOSClient.send. DBOS's system tables live in our `engine` schema, written with the
agentforge_engine role. Steps return IDs and small integers only (H6).

The API process uses DBOSClient (talks only to the system tables); the worker process runs
DBOS itself. Crash recovery: on launch, DBOS resumes PENDING workflows of its executor id.
"""

import asyncio
from uuid import UUID

from dbos import DBOS, DBOSClient, SetEnqueueOptions, SetWorkflowID

from spikes.engine.harness.port import EngineConfig
from spikes.engine.harness.steps import RunGoneError, Steps

RUN_QUEUE = "runs"
ITEM_QUEUE = "items"
_steps: Steps | None = None  # set in the worker process


def _s() -> Steps:
    assert _steps is not None, "steps run only in the worker"
    return _steps


def run_wf_id(run_id: str) -> str:
    return f"run-{run_id}"


# --- steps: thin wrappers so DBOS checkpoints each result (IDs and integers only) ------------


@DBOS.step(name="begin")
async def s_begin(org: str, run: str) -> bool:
    try:
        return await _s().begin(UUID(org), UUID(run))
    except RunGoneError:
        return False


@DBOS.step(name="plan")
async def s_plan(org: str, run: str) -> int:
    return await _s().plan(UUID(org), UUID(run))


@DBOS.step(name="item")
async def s_item(org: str, run: str, idx: int) -> int:
    return await _s().process_item(UUID(org), UUID(run), idx)


@DBOS.step(name="enter_review")
async def s_enter_review(org: str, run: str) -> None:
    await _s().enter_review(UUID(org), UUID(run))


@DBOS.step(name="leave_review")
async def s_leave_review(org: str, run: str) -> None:
    await _s().leave_review(UUID(org), UUID(run))


@DBOS.step(name="epoch")
async def s_epoch(org: str, run: str) -> int:
    _, epoch, _, _ = await _s().run_info(UUID(org), UUID(run))
    return epoch


@DBOS.step(name="export")
async def s_export(org: str, run: str, epoch: int) -> str:
    return await _s().export(UUID(org), UUID(run), epoch)


@DBOS.step(name="finish")
async def s_finish(org: str, run: str) -> None:
    await _s().finish(UUID(org), UUID(run))


# --- workflows ------------------------------------------------------------------------------


@DBOS.workflow(name="item_wf")
async def item_wf(org: str, run: str, idx: int) -> int:
    return await s_item(org, run, idx)


@DBOS.workflow(name="run_wf")
async def run_wf(org: str, run: str) -> None:
    if not await s_begin(org, run):
        return
    count = await s_plan(org, run)
    queue = await DBOS.retrieve_queue_async(ITEM_QUEUE)
    assert queue is not None
    handles = []
    for idx in range(count):
        with (
            SetWorkflowID(f"{run_wf_id(run)}-item-{idx}"),
            SetEnqueueOptions(queue_partition_key=run),
        ):
            handles.append(await queue.enqueue_async(item_wf, org, run, idx))
    for handle in handles:
        await handle.get_result()
    await s_enter_review(org, run)
    decision = None
    while decision is None:  # recv returns None on timeout; waiting holds no step or lease
        decision = await DBOS.recv_async("review", timeout_seconds=3600)
    await s_leave_review(org, run)
    epoch = await s_epoch(org, run)
    await s_export(org, run, epoch)
    await s_finish(org, run)


@DBOS.workflow(name="export_wf")
async def export_wf(org: str, run: str, epoch: int) -> None:
    await s_export(org, run, epoch)
    await s_finish(org, run)


class DbosEngine:
    name = "dbos"

    def __init__(self, config: EngineConfig) -> None:
        self._config = config
        self._client: DBOSClient | None = None
        self._app_sessions = None

    @property
    def client(self) -> DBOSClient:
        if self._client is None:  # the worker creates the system tables on first launch
            self._client = DBOSClient(
                system_database_url=self._config.engine_url,
                dbos_system_schema="engine",
                application_name="agentforge-conformance",
            )
        return self._client

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        if self._client is not None:
            self._client.destroy()

    async def start(self, org_id: UUID, run_id: UUID) -> None:
        # Same workflow id on every call: a repeated start (reconciler, H7) attaches to the
        # existing workflow instead of starting a second one.
        await self.client.enqueue_async(
            {
                "workflow_name": "run_wf",
                "queue_name": RUN_QUEUE,
                "workflow_id": run_wf_id(str(run_id)),
            },
            str(org_id),
            str(run_id),
        )

    async def cancel(self, org_id: UUID, run_id: UUID) -> None:
        await self.client.cancel_workflow_async(run_wf_id(str(run_id)), cancel_children=True)
        await self._steps_client().mark_cancelled(org_id, run_id)

    async def signal_review(self, org_id: UUID, run_id: UUID, decision: str) -> None:
        await self.client.send_async(
            run_wf_id(str(run_id)), decision, "review", idempotency_key=f"review-{run_id}"
        )

    async def retry_export(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        await self.client.enqueue_async(
            {
                "workflow_name": "export_wf",
                "queue_name": RUN_QUEUE,
                "workflow_id": f"{run_wf_id(str(run_id))}-export-{epoch}",
            },
            str(org_id),
            str(run_id),
            epoch,
        )

    def _steps_client(self) -> Steps:
        from agentforge.db.engine import create_app_engine, create_sessionmaker

        if self._app_sessions is None:
            self._app_sessions = create_sessionmaker(
                create_app_engine(self._config.app_url, pool_size=1)
            )
        return Steps(self._app_sessions, llm_latency=0)

    async def prepare_worker(self, steps: Steps) -> None:
        global _steps
        _steps = steps
        DBOS(
            config={
                "name": "agentforge-conformance",
                "system_database_url": self._config.engine_url,
                "dbos_system_schema": "engine",
                "executor_id": self._config.worker_id,
                "log_level": "WARNING",
                # The default is unbounded (sys.maxsize): the pool grows with every burst of
                # concurrent database calls and never shrinks (653 threads seen in gate H2).
                "max_executor_threads": 32,
            }
        )
        DBOS.launch()
        await DBOS.register_queue_async(RUN_QUEUE)
        await DBOS.register_queue_async(
            ITEM_QUEUE, partition_concurrency=4, polling_interval_sec=0.2
        )

    async def run_worker(self, steps: Steps, stop: asyncio.Event) -> None:
        try:
            await stop.wait()
        finally:
            DBOS.destroy(workflow_completion_timeout_sec=0)
