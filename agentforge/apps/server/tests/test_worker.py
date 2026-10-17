import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HEARTBEAT_WAIT_SECONDS = 15
STOP_DEADLINE_SECONDS = 10  # R1-04 target: clean exit within 10 s of SIGTERM


def test_worker_heartbeats_and_stops_cleanly_on_sigterm(tmp_path: Path) -> None:
    heartbeat = tmp_path / "worker.heartbeat"
    env = {
        **os.environ,
        "AGENTFORGE_WORKER_HEARTBEAT_PATH": str(heartbeat),
        "AGENTFORGE_WORKER_POLL_SECONDS": "0.2",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "agentforge", "worker"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + HEARTBEAT_WAIT_SECONDS
        while not heartbeat.exists():
            assert process.poll() is None, process.communicate()[0]
            assert time.monotonic() < deadline, "worker never wrote a heartbeat"
            time.sleep(0.05)

        sent_at = time.monotonic()
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=STOP_DEADLINE_SECONDS)
        stopped_in = time.monotonic() - sent_at
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 0, output
    assert stopped_in < STOP_DEADLINE_SECONDS
    assert '"event": "worker_stopped"' in output
    assert not heartbeat.exists(), "heartbeat file should be removed on clean shutdown"
