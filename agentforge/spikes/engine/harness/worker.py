"""Worker process for one engine: `python -m spikes.engine.harness.worker <engine>`.

Configuration comes from the environment (CONF_APP_URL, CONF_ENGINE_URL, CONF_LLM_LATENCY,
CONF_WORKER_ID, CONF_LEASE_SECONDS). Besides the engine's own loop it runs the start
reconciler (gate H7): runs left in 'queued' (their start was lost in a crash) are started.
"""

import asyncio
import contextlib
import importlib
import os
import signal
import sys

from agentforge.db.engine import create_app_engine, create_sessionmaker
from spikes.engine.harness.port import EngineConfig, RunEngine
from spikes.engine.harness.steps import Steps

ADAPTERS = {
    "stub": "spikes.engine.adapters.stub:StubEngine",
    "custom": "spikes.engine.adapters.custom:CustomEngine",
    "dbos": "spikes.engine.adapters.dbos_engine:DbosEngine",
    "langgraph": "spikes.engine.adapters.langgraph_engine:LangGraphEngine",
}
RECONCILE_EVERY = 1.0
RECONCILE_AFTER = 3.0  # seconds a run may sit in 'queued' before its start is presumed lost


def config_from_env() -> EngineConfig:
    return EngineConfig(
        app_url=os.environ["CONF_APP_URL"],
        engine_url=os.environ["CONF_ENGINE_URL"],
        worker_id=os.environ.get("CONF_WORKER_ID", "worker-1"),
        lease_seconds=float(os.environ.get("CONF_LEASE_SECONDS", "6")),
    )


def load_engine(name: str, config: EngineConfig) -> RunEngine:
    module_name, _, class_name = ADAPTERS[name].partition(":")
    engine_class = getattr(importlib.import_module(module_name), class_name)
    engine: RunEngine = engine_class(config)
    return engine


async def reconcile(engine: RunEngine, steps: Steps, stop: asyncio.Event) -> None:
    while not stop.is_set():
        for org_id, run_id in await steps.stale_queued_runs(RECONCILE_AFTER):
            await engine.start(org_id, run_id)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), RECONCILE_EVERY)


async def main(name: str) -> None:
    config = config_from_env()
    app_engine = create_app_engine(config.app_url, pool_size=5)
    steps = Steps(
        create_sessionmaker(app_engine),
        llm_latency=float(os.environ.get("CONF_LLM_LATENCY", "0.05")),
    )
    engine = load_engine(name, config)
    await engine.open()
    prepare = getattr(engine, "prepare_worker", None)
    if prepare is not None:  # e.g. DBOS: launch and create its system tables before "ready"
        await prepare(steps)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    print("worker ready", flush=True)  # the harness waits for this line
    try:
        await asyncio.gather(engine.run_worker(steps, stop), reconcile(engine, steps, stop))
    finally:
        await engine.close()
        await app_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
