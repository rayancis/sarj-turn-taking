"""tools/replay.py must reproduce what the real harness sees from my_system.py over a WebSocket."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_end_to_end import free_port
from ttbench.dataset import lay_out_stream, load_split
from ttbench.harness import run_session

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data" / "dev"
sys.path.insert(0, str(ROOT / "tools"))
import replay  # noqa: E402

pytestmark = pytest.mark.skipif(not (DEV / "manifest.json").exists(), reason="dev set not present")
CALLS = ["GI-D02", "BC-D02"]       # one noisy call, one quiet call with several caller sounds


def decisions(log):
    return [(l["stream_ms"], l["msg"]["decision"]) for l in log
            if l["dir"] == "rx" and l["msg"].get("type") == "decision"]


def test_replay_matches_real_harness_decisions(tmp_path):
    make, team = replay.load_system(ROOT / "my_system.py")
    offline_log, meta = replay.replay(make, lay_out_stream(load_split(DEV, CALLS)), team)
    assert meta["errors"] == []
    offline = decisions(offline_log)
    assert offline, "the comparison is only meaningful if the system decides something"

    port = free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen([sys.executable, str(ROOT / "my_system.py"), "--port", str(port), "--quiet"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                import socket
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        meta_rt = asyncio.run(run_session(f"ws://127.0.0.1:{port}", lay_out_stream(load_split(DEV, CALLS)),
                                          tmp_path, speed=4.0, quiet=True))
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    assert meta_rt["completed"] and not meta_rt["errors"]
    live = decisions([json.loads(l) for l in (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()])

    assert [d for _, d in live] == [d for _, d in offline]
    # at 4x speed, 1 ms of wall-clock delay is 4 ms of stream time; one frame is 20 ms
    assert all(abs(a - b) < 40 for (a, _), (b, _) in zip(live, offline)), list(zip(live, offline))
