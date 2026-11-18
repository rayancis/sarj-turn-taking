"""The score the demo shows must belong to the exact v3 source it serves."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_server():
    import sys
    sys.path.insert(0, str(ROOT / "demo"))
    import server
    return server


def test_displayed_v3_score_is_the_recorded_run_of_the_pinned_source():
    server = load_server()
    bench = json.loads((ROOT / "demo/benchmark.json").read_text(encoding="utf-8"))
    runs = json.loads((ROOT / "experiments/evidence/runs.json").read_text(encoding="utf-8"))["runs"]
    spec = server.DEFAULT_MODELS["v3"]
    assert [r["model"] for r in bench["rows"]] == ["v3"]
    row = bench["rows"][0]
    rec = runs[row["run"]]
    assert row["revision"] == spec["revision"]
    assert row["my_system_sha256"] == rec["my_system_sha256"] == spec["sha256"]
    # every pinned detector file of the served source was the one measured
    for path, digest in rec["files_pinned"].items():
        assert spec["files"][path] == digest, path
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, path
    assert round(rec["core"], 2) == row["core"] and rec["misses"] == row["misses"]
    assert rec["completed"] and rec["frames_sent"] == 29651 and rec["protocol_errors"] == 0


def test_rejected_and_historical_records_are_kept():
    runs = json.loads((ROOT / "experiments/evidence/runs.json").read_text(encoding="utf-8"))["runs"]
    for name in ("baseline-bd2a3f0-rt", "p3-6b4eece-rt", "v3-18471a8-rt", "v3-feba8ee-rt", "v3-feba8ee-legacyfloor-rt"):
        assert name in runs
    assert round(runs["v3-18471a8-rt"]["core"], 2) == 96.38
    assert "REJECTED" in runs["v3-feba8ee-rt"]["note"]
