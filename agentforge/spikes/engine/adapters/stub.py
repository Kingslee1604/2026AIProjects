"""A deliberately naive engine: proves the conformance tests can fail (R1-20 acceptance).

It runs each run as an in-memory asyncio task in the worker, started by the reconciler. It is
expected to FAIL: H1 (a killed worker's runs never resume), H2 (waiting runs do not survive a
restart), H3 (all items at once), H4-retry (retry is ignored), H5 (cancel is ignored). It is
expected to PASS: H6 (no engine tables), H7 (the reconciler starts lost runs), H8 (events).
"""

import asyncio
from uuid import UUID

from spikes.engine.harness.port import EngineConfig
from spikes.engine.harness.steps import Steps


class StubEngine:
    name = "stub"

    def __init__(self, config: EngineConfig) -> None:
        self._config = config
        self._steps: Steps | None = None  # set in the worker process only
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()

    async def start(self, org_id: UUID, run_id: UUID) -> None:
        # Client side: nothing durable is recorded, so the run is only picked up when the
        # worker's reconciler notices it sitting in 'queued'.
        if self._steps is None or run_id in self._tasks:
            return
        self._tasks[run_id] = asyncio.create_task(self._flow(self._steps, org_id, run_id))

    async def _flow(self, steps: Steps, org_id: UUID, run_id: UUID) -> None:
        if not await steps.begin(org_id, run_id):
            return
        count = await steps.plan(org_id, run_id)
        await asyncio.gather(*(steps.process_item(org_id, run_id, i) for i in range(count)))
        await steps.enter_review(org_id, run_id)
        while await steps.review_decision(org_id, run_id) is None:
            await asyncio.sleep(0.5)
        await steps.leave_review(org_id, run_id)
        _, epoch, _, _ = await steps.run_info(org_id, run_id)
        await steps.export(org_id, run_id, epoch)
        await steps.finish(org_id, run_id)

    async def cancel(self, org_id: UUID, run_id: UUID) -> None:
        return None  # ignored on purpose

    async def signal_review(self, org_id: UUID, run_id: UUID, decision: str) -> None:
        return None  # the flow polls the run row

    async def retry_export(self, org_id: UUID, run_id: UUID, epoch: int) -> None:
        return None  # ignored on purpose

    async def run_worker(self, steps: Steps, stop: asyncio.Event) -> None:
        self._steps = steps
        await stop.wait()
