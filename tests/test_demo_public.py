"""Public-mode guarantees of the hosted demo (demo/server.py --public, demo/Dockerfile).

The demo shows only the 24 competition recordings. Public mode serves them only once the organisers' permission for
public web hosting is recorded (it is not, so public mode refuses to start), and it never starts with no playable
scenario. Only Baseline and P3 are offered, each running its pinned code. The 50 Phase 3 research scenarios stay
preserved in the repository; the public-mode machinery (rights checks, exposure, limits) is tested on them.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "demo"))
sys.path.insert(0, str(KIT / "tools"))

import server as demo  # noqa: E402
from replay import replay  # noqa: E402
from ttbench.dataset import lay_out_stream, load_split  # noqa: E402
from ttbench.score import load_labels, score  # noqa: E402

from .test_demo import decisions_of, post, run_uneven, serving, status  # noqa: E402

EXTRA = KIT / "data" / "extra"
SPLITS = {"xdev": EXTRA / "xdev", "holdout": EXTRA / "holdout"}
REGISTER = json.loads((KIT / "demo" / "collections.json").read_text(encoding="utf-8"))
needs_audio = pytest.mark.skipif(not (EXTRA / "xdev" / "audio").is_dir(),
                                 reason="Phase 3 audio not built (python tools/build_extra.py)")


def public_app(**overrides) -> demo.DemoApp:
    settings = demo.Settings.for_public(port=1)
    for k, v in overrides.items():
        setattr(settings, k, v)
    return demo.DemoApp(None, demo.DEFAULT_MODELS, settings)


@pytest.fixture(scope="module")
def app():
    """A running public server for exposure tests: the machinery on the 50 research scenarios (not deployed)."""
    return public_app(collections=("xdev", "holdout"))


@pytest.fixture(scope="module")
def research():
    """Public-mode machinery on the 50 Phase 3 research scenarios (not deployed; tests the checks and limits)."""
    return public_app(collections=("xdev", "holdout"))


# ------------------------------------------------------------------------------------------------ data rights
def test_public_mode_refuses_the_competition_recordings_until_authorised():
    assert demo.Settings.for_public(port=1).collections == ("dev",)
    with pytest.raises(SystemExit, match="not authorised"):
        public_app()                                              # the deployable configuration, as of today
    with pytest.raises(SystemExit, match="only the collections"):
        demo.DemoApp(KIT / "data" / "dev", demo.DEFAULT_MODELS, demo.Settings.for_public(port=1))
    dev = next(c for c in REGISTER["collections"] if c["key"] == "dev")
    assert dev["public"] is False and not dev["authorization"] and dev["authorization_needed"]
    # marking it public without recording the permission is not enough
    with pytest.raises(SystemExit, match="not authorised"):
        demo.check_public_collection(demo.DevData(KIT / "data" / "dev", {**dev, "public": True}), REGISTER)


def test_once_authorised_exactly_the_kits_own_files_would_be_served():
    dev = next(c for c in REGISTER["collections"] if c["key"] == "dev")
    meta = {**dev, "public": True, "authorization": "test only"}
    demo.check_public_collection(demo.DevData(KIT / "data" / "dev", meta), REGISTER)   # passes: original files


def test_public_mode_never_starts_empty():
    with pytest.raises(SystemExit, match="no playable scenario"):
        public_app(collections=())


def test_an_unreviewed_or_changed_file_is_refused(tmp_path):
    from .fixtures import write_split
    col = demo.DevData(write_split(tmp_path / "s"), {"key": "x", "title": "x", "public": True})
    with pytest.raises(SystemExit, match="not a reviewed file"):
        demo.check_public_collection(col, REGISTER)


@needs_audio
def test_the_50_research_scenarios_are_preserved_intact():
    listed = REGISTER["public_wav_sha256"]
    assert len(listed) == 50
    for split, folder in SPLITS.items():
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert len(manifest["scenarios"]) == 25
        for e in manifest["scenarios"]:
            assert listed[e["scenario_id"]] == e["wav_sha256"]
            wav = folder / "audio" / f"{e['scenario_id']}.wav"
            assert hashlib.sha256(wav.read_bytes()).hexdigest() == e["wav_sha256"], wav
    dev_ids = {e["scenario_id"] for e in json.loads((KIT / "data" / "dev" / "manifest.json").read_text())["scenarios"]}
    assert not dev_ids & set(listed)


def test_every_audio_component_has_a_redistributable_licence():
    """Clip by clip: each sound named by a public scenario is CC0 or CC BY (ESC-50) or CC BY 4.0 (ASC voices)."""
    sources = json.loads((EXTRA / "sources.json").read_text(encoding="utf-8"))
    for key, src in sources.items():
        if key.startswith("asc:"):
            assert src["licence"] == "CC BY 4.0", key
        else:
            assert key.startswith("esc:") and src["licence"] in ("CC0", "CC-BY") and "freesound.org" in src["attribution"], key
    for folder in SPLITS.values():
        for label in (folder / "labels").glob("*.json"):
            lab = json.loads(label.read_text(encoding="utf-8"))
            for sid in lab["acoustic"].get("noise_source_ids", []):
                assert sid.startswith("SYN-") or ("esc:" + sid[len("ESC50-"):]) in sources, (label.name, sid)
    works = [a["work"] for a in REGISTER["attribution"]]
    assert any("Arabic Speech Commands" in w for w in works) and any("ESC-50" in w for w in works)


# ------------------------------------------------------------------------------------------------ real behaviour
@needs_audio
@pytest.mark.parametrize("model_id", list(demo.DEFAULT_MODELS))
def test_research_scenarios_decisions_equal_offline_replay(research, model_id):
    app = research
    model = app.models[model_id]
    assert model.available, model.error
    rng = random.Random(11)
    for sid, scen in app.data.scenarios.items():
        sess = demo.DetectorSession(model, scen)
        run_uneven(sess, rng)
        folder = app.data.by_id[sid].dir
        log, _ = replay(model.new_system, lay_out_stream(load_split(folder, [sid])))
        assert [(d["stream_ms"], d["decision"]) for d in sess.decisions] == decisions_of(log), sid


@needs_audio
def test_demo_p3_reproduces_the_recorded_phase3_xdev_result(app):
    """The P3 class the demo loads gives exactly the xdev score recorded in Phase 3 (not re-scoring the holdout)."""
    recorded = json.loads((KIT / "experiments" / "phase3-final" / "results.json").read_text(encoding="utf-8"))
    want = recorded["P3"]["result"]["xdev"]
    scenarios = lay_out_stream(load_split(SPLITS["xdev"]))
    log, meta = replay(app.models["p3"].new_system, scenarios)
    labels, th = load_labels(SPLITS["xdev"])
    got = score(labels, meta, log, th)
    assert got["core"]["core_score"] == pytest.approx(want["core"], abs=1e-9)
    assert got["rates"]["false_stops"] == want["false_stops"] and got["rates"]["misses"] == want["misses"]


def test_pinned_baseline_is_the_recorded_baseline_file():
    pinned = (KIT / "demo" / "models" / "baseline_bd2a3f0.py").read_bytes()
    runs = json.loads((KIT / "experiments" / "evidence" / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert hashlib.sha256(pinned).hexdigest() == runs["baseline-bd2a3f0-rt"]["my_system_sha256"]
    try:
        original = subprocess.run(["git", "show", "bd2a3f0:my_system.py"], cwd=KIT, capture_output=True, timeout=10)
    except OSError:
        pytest.skip("git not available")
    if original.returncode == 0:
        assert pinned == original.stdout


# ------------------------------------------------------------------------------------------------ exposure
@needs_audio
@needs_audio
def test_public_config_offers_two_detectors_and_hides_internals(app):
    cfg = app.config()
    assert cfg["mode"] == "public"
    assert [m["id"] for m in cfg["models"]] == ["p3", "baseline"]
    for m in cfg["models"]:
        assert set(m) == {"id", "name", "revision", "about", "dirty", "available"} and m["available"]
    assert {r["model"] for r in cfg["benchmark"]["rows"]} == {"baseline", "p3"}
    text = json.dumps(cfg, ensure_ascii=False)
    for secret in (str(KIT), "data/dev", "sha256", "GI-D01", "E01b", "e566234", "runs.json\": {"):
        assert secret not in text, secret


@needs_audio
def test_no_repository_file_is_reachable(app):
    """Every tracked file, under many spellings, must 404 (except the page's own static files; the public audio
    is reachable only through /api/audio/<id>.wav)."""
    tracked = subprocess.run(["git", "ls-files"], cwd=KIT, capture_output=True, text=True).stdout.split()
    if not tracked:
        tracked = ["my_system.py", "data/dev/manifest.json", "experiments/evidence/runs.json", "ttbench/score.py"]
    allowed = {"demo/static/index.html", "demo/static/app.js", "demo/static/style.css"}
    probes = []
    for f in tracked:
        if f in allowed:
            continue
        name = f.split("/")[-1]
        probes += [f"/{f}", f"/{name}", f"/static/{name}", f"/..%2F{f}", f"/%2e%2e/{f}"]
        if not (f.startswith("data/extra/") and f.endswith(".wav")):     # served in this research configuration
            probes += [f"/api/audio/{name}", f"/api/scenario/{name}"]
    probes += ["/.git/config", "/.git/HEAD", "/demo/server.py", "/server.py", "/.env", "/models/silero_vad.onnx",
               "/api/audio/GI-D01.wav", "/api/scenario/GI-D01", "/api/audio/GI-P01.wav",
               "/api/audio/..%2F..%2Fdev%2Faudio%2FGI-D01.wav", "//etc/passwd"]
    with serving(app) as base:
        reachable = [p for p in probes if status(base + p) != 404]
        assert reachable == []
        for ok in ("/", "/app.js", "/style.css", "/healthz", "/robots.txt", "/api/config"):
            assert status(base + ok) == 200, ok
        r = urllib.request.urlopen(base + "/")
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert r.headers["Server"].startswith("sarj-demo/1")       # no Python version banner


@needs_audio
def test_unauthorised_competition_recordings_are_unreachable(app):
    """Without the recorded permission no public server ever loads the 24 competition recordings."""
    ids = [e["scenario_id"] for e in json.loads((KIT / "data" / "dev" / "manifest.json").read_text())["scenarios"]]
    assert len(ids) == 24
    with serving(app) as base:
        for sid in ids:
            assert status(f"{base}/api/audio/{sid}.wav") == 404, sid
            assert status(f"{base}/api/scenario/{sid}") == 404, sid
        assert status(base + "/api/session", {"scenario": "GI-D01", "model": "p3"}) == 404


def test_image_contains_only_whitelisted_paths():
    copies = [line.split()[1:-1] for line in (KIT / "demo" / "Dockerfile").read_text(encoding="utf-8").splitlines()
              if line.startswith("COPY ")]
    flat = [p for c in copies for p in c]
    assert not any(p.startswith(("data", "experiments", "baseline", "tests", ".git", "demo/public_data")) for p in flat)
    assert "models/silero_vad.onnx" in flat and "models/SILERO_VAD_LICENSE" in flat and "neural_vad.py" in flat
    req = (KIT / "demo" / "requirements-public.txt").read_text(encoding="utf-8")
    assert "onnxruntime==1.29.0" in req and "numpy==2.5.5" in req
    render = (KIT / "render.yaml").read_text(encoding="utf-8")
    assert "branch: feat/demo-ui" in render and "healthCheckPath: /healthz" in render


# ------------------------------------------------------------------------------------------------ limits
@needs_audio
def test_capacity_eviction_and_rate_limits():
    app = public_app(max_sessions=2, evict_idle_s=60.0, sessions_per_min=600.0, collections=("xdev", "holdout"))
    a = app.new_session("GI-D51", "p3")["session_id"]
    app.new_session("GI-D51", "baseline")
    with pytest.raises(demo.Refused) as e:                       # both active: the next visitor is told to wait
        app.new_session("GI-D51", "p3")
    assert e.value.status == 503
    app.sessions[a].last_seen -= 120                             # one went idle: it can be replaced
    app.new_session("GI-D51", "p3")
    assert a not in app.sessions

    app = public_app(sessions_per_min=60.0, collections=("xdev", "holdout"))
    statuses = []
    for _ in range(40):
        try:
            app.new_session("GI-D51", "baseline")
            statuses.append(200)
        except demo.Refused as err:
            statuses.append(err.status)
    assert 429 in statuses and statuses[0] == 200


@needs_audio
def test_advance_is_paced_and_rate_limited():
    app = public_app(advance_per_s=5.0, collections=("xdev", "holdout"))
    sid = app.new_session("GI-D51", "p3")["session_id"]
    r = app.advance(sid, 1e9)                                   # cannot run the whole call at once
    assert not r["done"] and r["fed_ms"] <= app.settings.lookahead_ms + 1000 + 200
    with pytest.raises(demo.Refused) as e:
        for _ in range(50):
            app.advance(sid, 0)
    assert e.value.status == 429


@needs_audio
def test_reset_and_repeated_playback_give_identical_results(research):
    app = research
    """Each playback is a fresh detector (P3's VAD state included): replaying a call gives the same decisions."""
    runs = []
    for _ in range(3):
        for model in ("p3", "baseline"):
            sess = demo.DetectorSession(app.models[model], app.data.scenarios["GI-H54"])
            sess.advance(1e9)
            runs.append((model, [(d["stream_ms"], d["decision"]) for d in sess.decisions]))
    assert runs[0::2] == [runs[0]] * 3 and runs[1::2] == [runs[1]] * 3


@needs_audio
def test_http_limits(research):
    with serving(research) as base:
        big = {"to_ms": 1, "pad": "x" * 5000}
        assert status(base + "/api/session", big) == 413
        s = post(base, "/api/session", {"scenario": "GI-D51", "model": "p3"})
        assert status(base + f"/api/session/{s['session_id']}/close", {}) == 200
        assert status(base + f"/api/session/{s['session_id']}/advance", {"to_ms": 10}) == 404
        req = urllib.request.Request(base + "/healthz", method="HEAD")
        r = urllib.request.urlopen(req)
        assert r.status == 200 and r.read() == b""


def test_public_server_listens_on_all_interfaces_only_in_public_mode():
    assert demo.Settings().host == "127.0.0.1"
    assert demo.Settings.for_public(port=1).host == "0.0.0.0"
    assert demo.Settings().collections == ("dev",)
    shutil.which("git")                                         # (keeps the import used on systems without git)
