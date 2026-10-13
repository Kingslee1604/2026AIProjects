"""Worker main loop with graceful shutdown.

Release 1 M1 has no jobs yet; the loop proves the lifecycle (start, heartbeat, stop on
SIGTERM/SIGINT). Ingestion (R1-13) and the run engine (R1-24) plug into `_tick`.
"""

import asyncio
import contextlib
import signal
import time

from agentforge.core.logging import configure_logging, get_logger
from agentforge.core.settings import Settings, get_settings
from agentforge.core.telemetry import configure_tracing

log = get_logger("agentforge.worker")


class Worker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def _heartbeat(self) -> None:
        # The container health check reads this file's age.
        self.settings.worker_heartbeat_path.write_text(str(time.time()))

    async def _tick(self) -> None:
        """One unit of background work. Empty until job sources exist."""

    async def run(self) -> None:
        log.info("worker_started", poll_seconds=self.settings.worker_poll_seconds)
        while not self._stop.is_set():
            self._heartbeat()
            await self._tick()
            with contextlib.suppress(TimeoutError):  # timeout = next poll, not an error
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker_poll_seconds)
        self.settings.worker_heartbeat_path.unlink(missing_ok=True)
        log.info("worker_stopped")


def run_worker(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    configure_tracing(settings)
    worker = Worker(settings)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.request_stop)
        await worker.run()

    asyncio.run(main())
