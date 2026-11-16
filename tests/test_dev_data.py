"""If the Arabic dev set is present: every label is consistent, and a real scenario streams and scores end to end."""
import asyncio
import json
from pathlib import Path

import pytest

from tests.test_end_to_end import free_port
from ttbench.baselines import AnySound, serve_policy
from ttbench.dataset import lay_out_stream, load_split
from ttbench.harness import run_session
from ttbench.score import score_run_dir

DEV = Path(__file__).resolve().parents[1] / "data" / "dev"
pytestmark = pytest.mark.skipif(not (DEV / "manifest.json").exists(), reason="dev set not built/unpacked")


def test_dev_labels_are_consistent():
    man = json.loads((DEV / "manifest.json").read_text())
    assert len(man["scenarios"]) == 24
    cats = set()
    for e in man["scenarios"]:
        l = json.loads((DEV / "labels" / f"{e['scenario_id']}.json").read_text())
        cats.add(l["category"])
        dur = l["audio"]["duration_ms"]
        for ev in l["events"]:
            assert 0 <= ev["onset_ms"] < dur
            if ev["expected"] == "STOP":
                assert ev["onset_ms"] <= ev["evident_ms"] <= ev["offset_ms"] + 50
            if ev["kind"] == "end_of_turn":
                assert dur - ev["turn_end_ms"] >= 3000
    assert cats == {"GI", "BC", "HT", "BG", "NV", "AM", "OV", "DV"}


def test_arabic_scenarios_stream_and_score(tmp_path):
    async def go():
        port, ready, stop = free_port(), asyncio.Event(), asyncio.Event()
        server = asyncio.create_task(serve_policy(AnySound, port=port, ready=ready, stop=stop))
        await ready.wait()
        scen = lay_out_stream(load_split(DEV, only=["GI-D01", "BC-D01"]))
        meta = await run_session(f"ws://127.0.0.1:{port}", scen, tmp_path, speed=10.0, quiet=True)
        stop.set()
        await server
        return meta
    meta = asyncio.run(go())
    assert meta["completed"] and not meta["errors"]
    r = score_run_dir(DEV, tmp_path)
    assert {e["event_id"] for e in r["events"]} == {"GI-D01.e1", "BC-D01.e1"}
