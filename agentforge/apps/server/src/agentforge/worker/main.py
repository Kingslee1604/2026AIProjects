"""Worker main loop with graceful shutdown.

Processes ingestion (R1-13) and answer (R1-17) jobs, and hosts the run engine (DBOS,
ADR-0002) when AGENTFORGE_ENGINE_DATABASE_URL is set, plus its start reconciler. The heartbeat
runs as its own task, so a long job (embedding a big document on CPU) never makes the
container look dead.
"""

import asyncio
import contextlib
import signal
import time

from agentforge.core.logging import configure_logging, get_logger
from agentforge.core.settings import Settings, get_settings
from agentforge.core.telemetry import configure_tracing
from agentforge.runs.dbos_engine import launch_worker, shutdown_worker
from agentforge.services import Services, build_services

log = get_logger("agentforge.worker")
MAX_JOBS_PER_TICK = 10
RECONCILE_EVERY_SECONDS = 5.0
RECONCILE_AFTER_SECONDS = 10.0  # a run 'queued' this long had its start lost


class Worker:
    def __init__(self, settings: Settings, services: Services | None = None) -> None:
        self.settings = settings
        self.services = services
        self._stop = asyncio.Event()
        self._engine_running = False
        self._last_reconcile = 0.0

    def request_stop(self) -> None:
        self._stop.set()

    def _heartbeat(self) -> None:
        # The container health check reads this file's age.
        self.settings.worker_heartbeat_path.write_text(str(time.time()))

    async def _heartbeats(self) -> None:
        while not self._stop.is_set():
            self._heartbeat()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker_poll_seconds)

    async def _tick(self) -> None:
        """Process queued work until the queue is empty, a batch is done, or stop is requested."""
        if self.services is None:
            return
        handlers = {
            "ingestion": self.services.knowledge.process_next_job,
            "answer": self.services.qa.process_next_job,
        }
        idle: set[str] = set()
        for _ in range(MAX_JOBS_PER_TICK):
            # Alternate between queues so a big upload batch never starves questions.
            for name, handler in handlers.items():
                if self._stop.is_set():
                    return
                if name in idle:
                    continue
                try:
                    if not await handler():
                        idle.add(name)
                except Exception:  # one bad job must not stop the worker; the lease will expire
                    log.exception("job_crashed", queue=name)
                    idle.add(name)
            if len(idle) == len(handlers):
                return

    async def _reconcile_runs(self) -> None:
        """Start runs whose start was lost (committed row, crash before the enqueue: gate H7)."""
        if self.services is None or not self._engine_running:
            return
        now = time.monotonic()
        if now - self._last_reconcile < RECONCILE_EVERY_SECONDS:
            return
        self._last_reconcile = now
        try:
            for org_id, run_id, template_key in await self.services.runs.stale_queued(
                RECONCILE_AFTER_SECONDS
            ):
                await self.services.run_engine.start(org_id, run_id, template_key)
        except Exception:
            log.exception("run_reconcile_failed")

    async def run(self) -> None:
        log.info("worker_started", poll_seconds=self.settings.worker_poll_seconds)
        self._heartbeat()
        heartbeat = asyncio.create_task(self._heartbeats())
        if self.services is not None and self.settings.engine_database_url is not None:
            await launch_worker(self.settings)  # DBOS resumes this worker's unfinished runs
            self._engine_running = True
            log.info("run_engine_started", executor_id=self.settings.engine_executor_id)
        try:
            while not self._stop.is_set():
                await self._reconcile_runs()
                await self._tick()
                with contextlib.suppress(TimeoutError):  # timeout = next poll, not an error
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.settings.worker_poll_seconds
                    )
        finally:
            self._stop.set()
            await heartbeat
            if self._engine_running:
                shutdown_worker()
            self.settings.worker_heartbeat_path.unlink(missing_ok=True)
            if self.services is not None:
                await self.services.close()
        log.info("worker_stopped")


def run_worker(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    configure_tracing(settings)

    async def main() -> None:
        services = build_services(settings, pool_size=3) if settings.database_url else None
        worker = Worker(settings, services)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.request_stop)
        await worker.run()

    asyncio.run(main())
