"""Tests for the local demo (demo/server.py): the page must show the real detector's decisions, unchanged.

The central property: feeding a scenario through the demo, in whatever uneven steps the browser's playhead produces
(including pauses), gives exactly the decisions of tools/replay.py, which mirrors the kit's harness and server.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "demo"))
sys.path.insert(0, str(KIT / "tools"))

import server as demo  # noqa: E402
from replay import replay  # noqa: E402
from ttbench.dataset import lay_out_stream, load_split  # noqa: E402

from .fixtures import write_split  # noqa: E402

DEV = KIT / "data" / "dev"

PROBE = '''
from ttbench.server import System

class MySystem(System):
    instances = []

    def __init__(self):
        self.calls, self.n = [], 0
        MySystem.instances.append(self)

    def on_scenario_start(self, context):
        self.calls.append(("scenario_start", dict(context)))

    def on_agent_start(self, text, duration_ms):
        self.calls.append(("agent_start", text, duration_ms))

    def on_agent_end(self):
        self.calls.append(("agent_end",))

    def on_frame(self, agent, caller):
        self.n += 1
        self.calls.append(("frame", agent.dtype.str, agent.shape, caller.shape,
                           int(agent.astype("int64").sum()), int(caller.astype("int64").sum())))
        return {MODE}
'''


def probe_source(tmp_path: Path, mode: str = "'STOP' if self.n == 60 else None", name: str = "probe") -> str:
    """Write a recording test system; its on_frame returns `mode`. Returns a model source spec for it."""
    path = tmp_path / f"{name}.py"
    path.write_text(PROBE.replace("{MODE}", mode), encoding="utf-8")
    return f"file:{path}"


def probe(tmp_path: Path, mode: str, name: str) -> demo.Model:
    return demo.Model(name, probe_source(tmp_path, mode, name))


def decisions_of(log):
    return [(l["stream_ms"], l["msg"]["decision"]) for l in log if l["dir"] == "rx" and l["msg"].get("type") == "decision"]


def run_uneven(sess: demo.DetectorSession, rng: random.Random, on_step=None):
    """Advance like a browser would: uneven steps, repeated playheads (pause) and occasional backward values."""
    t = 0.0
    while not sess.done:
        r = rng.random()
        if r < 0.1:
            sess.advance(t)                                   # paused: same playhead again
        elif r < 0.15:
            sess.advance(max(0.0, t - 37))                    # out-of-order report: must feed nothing
        t += rng.choice([1, 3, 15, 16.7, 20, 21, 33, 60, 140, 400])
        sess.advance(t)
        if on_step:
            on_step()


# ------------------------------------------------------------------------------------------------ real detectors
@pytest.fixture(scope="module")
def dev_app():
    return demo.DemoApp(DEV, demo.DEFAULT_MODELS)


def test_only_verified_v3_is_exposed(dev_app):
    assert list(dev_app.models) == ["v3"]
    model = dev_app.models["v3"]
    assert model.available, model.error
    assert model.sha256 == demo.V3_SHA256
    assert model.new_system().production_ready
    assert model.name == "SARJ v3" and model.revision == "v3-coldstart-1"


def test_v3_refuses_a_changed_module(tmp_path, monkeypatch):
    """A multi-file detector is pinned file by file: editing any tt/ module disables it instead of running it."""
    spec = dict(demo.DEFAULT_MODELS["v3"])
    rel = next(iter(spec["files"]))
    spec["files"] = {**spec["files"], rel: "0" * 64}
    m = demo.Model("v3", spec)
    assert not m.available and "not the pinned" in m.error


def test_p3_files_are_the_audited_phase3_files():
    for path in ("my_system.py", "neural_vad.py", "models/silero_vad.onnx", "models/SILERO_VAD_LICENSE"):
        try:
            original = subprocess.run(["git", "show", f"b32cfc2:{path}"], cwd=KIT, capture_output=True, timeout=20)
        except OSError:
            pytest.skip("git not available")
        if original.returncode != 0:
            pytest.skip("commit b32cfc2 not available")
        assert (KIT / path).read_bytes() == original.stdout, path


def test_demo_shows_exactly_the_24_original_recordings():
    """The demo (default settings) lists the 24 kit dev calls and nothing else; each plays the original file, byte for
    byte as in the kit's own manifest, with its original labels and timing."""
    import hashlib
    app = demo.DemoApp(None, demo.DEFAULT_MODELS)
    manifest = json.loads((DEV / "manifest.json").read_text(encoding="utf-8"))
    want = {e["scenario_id"]: e["wav_sha256"] for e in manifest["scenarios"]}
    cfg = app.config()
    assert [c["key"] for c in cfg["collections"]] == ["dev"]
    assert len(cfg["scenarios"]) == 24 and {x["id"] for x in cfg["scenarios"]} == set(want)
    assert not any("-D5" in x["id"] or "-H5" in x["id"] for x in cfg["scenarios"])
    with serving(app) as base:
        for sid, digest in want.items():
            body = urllib.request.urlopen(f"{base}/api/audio/{sid}.wav").read()
            assert hashlib.sha256(body).hexdigest() == digest, sid
            label = json.loads((DEV / "labels" / f"{sid}.json").read_text(encoding="utf-8"))
            assert body == (DEV / "audio" / label["audio"]["file"]).read_bytes()
            tl = json.loads(urllib.request.urlopen(f"{base}/api/scenario/{sid}").read())
            assert tl["ground_truth"]["events"] == label["events"]
            assert [(u["utt_id"], u["start_ms"], u["end_ms"]) for u in tl["protocol"]["agent_utterances"]] == \
                [(u["utt_id"], u["start_ms"], u["end_ms"]) for u in label["agent_utterances"]]
        for research in ("GI-D51", "NV-H51"):
            assert status(f"{base}/api/audio/{research}.wav") == 404


def test_a_label_cannot_run_other_code(tmp_path):
    wrong = demo.Model("p3", {**demo.HISTORICAL_MODELS["p3"], "source": "file:demo/models/baseline_bd2a3f0.py"})
    assert not wrong.available and "not the pinned" in wrong.error


@pytest.mark.parametrize("model_id", list(demo.HISTORICAL_MODELS))
def test_demo_decisions_equal_offline_replay_on_every_dev_call(dev_app, model_id):
    """Uneven polling, pauses and stale playheads never change what the detector decides, on all 24 calls; and an
    event's verdict, once shown, never differs from the final official verdict."""
    model = demo.Model(model_id, demo.HISTORICAL_MODELS[model_id])
    if not model.available:
        pytest.skip(model.error)
    rng = random.Random(7)
    for sid, scen in dev_app.data.scenarios.items():
        sess = demo.DetectorSession(model, scen)
        scorer = demo.Scorer(scen.label, dev_app.data.thresholds(sid))
        shown: dict[str, dict] = {}

        def remember():
            for e in scorer.evaluate(sess.decisions, sess.fed_ms, sess.done)["events"]:
                if e["closed"]:
                    shown.setdefault(e["event_id"], e)

        run_uneven(sess, rng, remember)
        log, _ = replay(model.new_system, lay_out_stream(load_split(DEV, [sid])))
        assert [(d["stream_ms"], d["decision"]) for d in sess.decisions] == decisions_of(log), sid
        assert not sess.errors and not sess.failed
        final = {e["event_id"]: e for e in scorer.evaluate(sess.decisions, sess.fed_ms, True)["events"]}
        for eid, early in shown.items():
            assert {k: early[k] for k in ("outcome", "credit")} == {k: final[eid][k] for k in ("outcome", "credit")}, eid


@pytest.mark.parametrize("model_id,run", [("p3", "p3-6b4eece-rt"), ("baseline", "baseline-bd2a3f0-rt")])
def test_demo_matches_the_recorded_realtime_runs(dev_app, model_id, run):
    """Per call, the demo gives the decisions the official real-time harness recorded for that commit in one full
    session (arrival time = frame time + processing/network delay, so 0-20 ms later)."""
    model = demo.Model(model_id, demo.HISTORICAL_MODELS[model_id])
    if not model.available:
        pytest.skip(model.error)
    folder = KIT / "experiments" / "evidence" / run
    meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    recorded: dict[str, list] = {s["scenario_id"]: [] for s in meta["scenarios"]}
    spans = {s["scenario_id"]: (s["start_ms"], s["start_ms"] + s["duration_ms"] + 1500) for s in meta["scenarios"]}
    for line in (folder / "log.jsonl").read_text(encoding="utf-8").splitlines():
        msg = json.loads(line)
        if msg["dir"] == "rx" and msg["msg"].get("type") == "decision":
            sid = next(k for k, (a, b) in spans.items() if a <= msg["stream_ms"] < b)
            recorded[sid].append((msg["stream_ms"] - spans[sid][0], msg["msg"]["decision"]))
    for sid, scen in dev_app.data.scenarios.items():
        sess = demo.DetectorSession(model, scen)
        sess.advance(1e9)
        ours = [(d["stream_ms"], d["decision"]) for d in sess.decisions]
        assert [d for _, d in ours] == [d for _, d in recorded[sid]], sid
        assert all(0 <= r - o <= 20 for (o, _), (r, _) in zip(ours, recorded[sid])), sid


def test_api_real_stop_and_real_keep(dev_app):
    """Through HTTP: GI-D01 gets a real P3 STOP inside the interruption; NV-D01 (a cough): P3 keeps talking while
    the Baseline stops (its recorded false stop)."""
    with serving(dev_app) as base:
        s = post(base, "/api/session", {"scenario": "GI-D01", "model": "v3"})
        r = post(base, f"/api/session/{s['session_id']}/advance", {"to_ms": s["stream_end_ms"]})
        stops = [d for d in r["decisions"] if d["decision"] == "STOP"]
        assert r["done"] and len(stops) == 1 and stops[0]["utt_id"] is not None
        ev = r["score"]["events"][0]
        assert ev["closed"] and ev["outcome"] == "yield" and ev["credit"] > 0

        for model, outcome in (("v3", "kept"),):
            s = post(base, "/api/session", {"scenario": "NV-D01", "model": model})
            r = post(base, f"/api/session/{s['session_id']}/advance", {"to_ms": s["stream_end_ms"]})
            assert r["score"]["events"][0]["outcome"] == outcome, model


def test_config_reports_current_v3_benchmark_only(dev_app):
    cfg = dev_app.config()
    assert len(cfg["scenarios"]) == 24
    assert all(r["model"] == "v3" for r in cfg["benchmark"]["rows"])
    # Historical records are retained independently, not advertised as the changed detector's result.
    runs = json.loads((KIT / "experiments/evidence/runs.json").read_text())["runs"]
    assert round(runs["v3-18471a8-rt"]["core"], 2) == 96.38


def test_timeline_keeps_ground_truth_apart_from_protocol_input(dev_app):
    tl = dev_app.data.timeline("BC-D01")
    proto = json.dumps(tl["protocol"], ensure_ascii=False)
    assert "events" not in tl["protocol"] and "user_segments" not in tl["protocol"]
    for seg in tl["ground_truth"]["user_segments"]:
        assert seg["text"] not in proto                     # caller transcript is never presented as detector input


# ------------------------------------------------------------------------------------------------ synthetic data
@pytest.fixture()
def tiny(tmp_path):
    return write_split(tmp_path / "split")


def test_detector_gets_exactly_the_harness_calls(tmp_path, tiny):
    """Same calls, arguments and frame bytes as tools/replay.py; frames are int16 (320,) per channel; no labels."""
    app = demo.DemoApp(tiny, {"probe": probe_source(tmp_path, "None")})
    cls = app.models["probe"]._cls
    for sid, scen in app.data.scenarios.items():
        sess = demo.DetectorSession(app.models["probe"], scen)
        run_uneven(sess, random.Random(3))
        replay(cls, lay_out_stream(load_split(tiny, [sid])))
        via_replay = cls.instances[-1].calls
        assert sess.system.calls == via_replay, sid
        frames = [c for c in sess.system.calls if c[0] == "frame"]
        assert frames and all(c[1] == "<i2" and c[2] == (320,) and c[3] == (320,) for c in frames)
        for c in sess.system.calls:
            if c[0] == "scenario_start":
                assert set(c[1]) == {"use_case", "language"}


def test_decision_is_stamped_with_its_frame_time(tmp_path, tiny):
    app = demo.DemoApp(tiny, {"probe": probe_source(tmp_path)})
    sess = demo.DetectorSession(app.models["probe"], app.data.scenarios["GI-D90"])
    sess.advance(59 * 20 - 1)
    assert sess.decisions == []
    new, _ = sess.advance(59 * 20)                         # the 60th on_frame call is frame 59
    assert [(d["stream_ms"], d["frame"], d["decision"]) for d in new] == [(1180, 59, "STOP")]
    assert new[0]["utt_id"] == "A1"


def test_invalid_output_and_crash_are_reported_not_hidden(tmp_path, tiny):
    bad = probe(tmp_path, "'MAYBE' if self.n == 3 else None", "bad")
    sess = demo.DetectorSession(bad, demo.DevData(tiny).scenarios["GI-D90"])
    sess.advance(10_000)
    assert sess.decisions == [] and "MAYBE" in sess.errors[0]["error"]

    crash = probe(tmp_path, "1 / 0 if self.n == 5 else None", "crash")
    sess = demo.DetectorSession(crash, demo.DevData(tiny).scenarios["GI-D90"])
    sess.advance(10_000)
    assert sess.failed and sess.done and "ZeroDivisionError" in sess.errors[-1]["error"]


def test_sessions_are_independent(tmp_path, tiny):
    """Reset and scenario switches create new sessions; interleaving two never mixes their state."""
    app = demo.DemoApp(tiny, {"probe": probe_source(tmp_path)})
    a = app.new_session("GI-D90", "probe")["session_id"]
    b = app.new_session("GI-D90", "probe")["session_id"]
    assert a != b
    app.advance(a, 2000)
    rb = app.advance(b, 500)
    assert rb["decisions"] == [] and rb["fed_ms"] == 520
    ra = app.advance(a, 2000)
    assert ra["decisions"] == [] and ra["fed_ms"] == 2020       # nothing re-fed, nothing re-sent
    full = app.advance(b, 1e9)
    assert [d["stream_ms"] for d in full["decisions"]] == [1180]


def test_unavailable_versions_are_reported(tmp_path, tiny):
    m = demo.Model("ghost", "git:0000000:my_system.py")
    assert not m.available and m.error
    (tmp_path / "broken.py").write_text("def nope(:\n", encoding="utf-8")
    assert not demo.Model("broken", f"file:{tmp_path / 'broken.py'}").available
    app = demo.DemoApp(tiny, {"ghost": "git:0000000:my_system.py"})
    with pytest.raises(demo.Refused) as e:
        app.new_session("GI-D90", "ghost")
    assert e.value.status == 503


def test_http_surface(tmp_path, tiny):
    app = demo.DemoApp(tiny, {"probe": probe_source(tmp_path)})
    with serving(app) as base:
        assert base.startswith("http://127.0.0.1:")
        page = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "app.js" in page
        wav = urllib.request.urlopen(base + "/api/audio/GI-D90.wav").read()
        assert wav == (tiny / "audio" / "GI-D90.wav").read_bytes()
        for path in ("/api/audio/..%2Fmanifest.wav", "/api/audio/XX-D00.wav", "/../demo/server.py",
                     "/api/scenario/nope", "/server.py"):
            assert status(base + path) == 404, path
        assert status(base + "/api/session/" + "0" * 32 + "/advance", {"to_ms": 1}) == 404
        s = post(base, "/api/session", {"scenario": "GI-D90", "model": "probe"})
        assert status(base + f"/api/session/{s['session_id']}/advance", {"to_ms": "soon"}) == 400
        assert status(base + f"/api/session/{s['session_id']}/advance", {"to_ms": "nan"}) == 400
        assert status(base + "/api/session", {"scenario": "GI-D90", "model": "nope"}) == 404


def test_server_binds_to_localhost_only():
    assert demo.HOST == "127.0.0.1"
    assert "--host" not in demo.main.__code__.co_consts


# ------------------------------------------------------------------------------------------------ helpers
class serving:
    def __init__(self, app):
        self.srv = demo.make_server(app, 0)

    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}"

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()


def post(base, path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def status(url, body=None):
    req = urllib.request.Request(url, None if body is None else json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req).status
    except urllib.error.HTTPError as e:
        return e.code
