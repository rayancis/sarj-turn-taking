"""Harness <-> system over a real WebSocket: the reference systems and the student example."""
import asyncio
import importlib.util
import json
import socket
from pathlib import Path

import pytest

from tests.fixtures import write_split
from ttbench.baselines import AlwaysStop, AnySound, NeverStop, serve_policy
from ttbench.dataset import lay_out_stream, load_split
from ttbench.harness import run_session
from ttbench.score import score_run_dir

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _run_against(policy, data, out, speed):
    port, ready, stop = free_port(), asyncio.Event(), asyncio.Event()
    server = asyncio.create_task(serve_policy(policy, port=port, ready=ready, stop=stop))
    await ready.wait()
    scen = lay_out_stream(load_split(data))
    meta = await run_session(f"ws://127.0.0.1:{port}", scen, out, speed=speed, quiet=True)
    stop.set()
    await server
    return meta


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return write_split(tmp_path_factory.mktemp("split"))


@pytest.mark.parametrize("policy", [NeverStop, AnySound, AlwaysStop])
def test_reference_systems_end_to_end(policy, data, tmp_path):
    meta = asyncio.run(_run_against(policy, data, tmp_path, speed=8.0))
    assert meta["completed"] and not meta["errors"]
    r = score_run_dir(data, tmp_path)
    assert (tmp_path / "report.md").exists() and (tmp_path / "score.json").exists()
    if policy is NeverStop:
        assert r["core"]["R"] == 0
    if policy is AlwaysStop:
        assert r["core"]["F"] == 0
    if policy is AnySound:
        assert r["events"][0]["outcome"] == "yield"          # GI burst triggers it
        assert r["eot"]["attempted"]


def test_realtime_latency_is_measured(data, tmp_path):
    """Speed 1.0 on one scenario: B1 needs 200 ms of sound after onset, so it must yield ~0-300 ms after evident."""
    async def go():
        port, ready, stop = free_port(), asyncio.Event(), asyncio.Event()
        server = asyncio.create_task(serve_policy(AnySound, port=port, ready=ready, stop=stop))
        await ready.wait()
        scen = lay_out_stream(load_split(data, only=["GI-D90"]))
        meta = await run_session(f"ws://127.0.0.1:{port}", scen, tmp_path, speed=1.0, quiet=True)
        stop.set()
        await server
        return meta
    meta = asyncio.run(go())
    assert meta["official"] and meta["max_send_lag_ms"] < 50
    r = score_run_dir(data, tmp_path)
    e = r["events"][0]
    assert e["outcome"] == "yield"
    # onset 2000 + 200 ms of sound = 2200 < evident 2300 -> latency 0 (plus jitter)
    assert e["latency_ms"] <= 60


def test_student_example_server(data, tmp_path):
    spec = importlib.util.spec_from_file_location("example_server", ROOT / "examples" / "example_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from websockets.asyncio.server import serve

    async def go():
        port = free_port()
        async with serve(mod.handler, "127.0.0.1", port, max_size=2 ** 20, ping_interval=None):
            scen = lay_out_stream(load_split(data))
            return await run_session(f"ws://127.0.0.1:{port}", scen, tmp_path, speed=8.0, quiet=True)
    meta = asyncio.run(go())
    assert meta["completed"] and not meta["errors"] and meta["hello"]["team"] == "T00-example"
    r = score_run_dir(data, tmp_path)
    kinds = {json.loads(l)["msg"].get("decision") for l in (tmp_path / "log.jsonl").read_text().splitlines()}
    assert {"STOP", "TAKE_TURN"} <= kinds
    assert 0 <= r["core"]["core_score"] <= 100
