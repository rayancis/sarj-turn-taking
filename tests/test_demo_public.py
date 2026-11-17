"""Public-mode guarantees of the hosted demo (demo/server.py --public, demo/Dockerfile).

What must hold before the site is exposed: only the publicly licensed Phase 3 collections are served, each WAV
byte-identical to a reviewed file; the competition recordings and labels, evaluation artifacts and source files are
unreachable over HTTP and absent from the image; only Baseline and P3 are offered, each running its pinned code;
limits hold; and both detectors behave exactly as in the kit's replay and as recorded in Phase 3.
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
    return public_app()


# ------------------------------------------------------------------------------------------------ data rights
def test_competition_recordings_are_never_public(tmp_path):
    settings = demo.Settings.for_public(port=1)
    settings.collections = ("dev",)
    with pytest.raises(SystemExit, match="not marked public"):
        demo.DemoApp(None, demo.DEFAULT_MODELS, settings)
    with pytest.raises(SystemExit, match="only the collections"):
        demo.DemoApp(KIT / "data" / "dev", demo.DEFAULT_MODELS, demo.Settings.for_public(port=1))
    # even if someone marked the kit data public, it is refused
    meta = {**next(c for c in REGISTER["collections"] if c["key"] == "dev"), "public": True}
    with pytest.raises(SystemExit, match="no public licence"):
        demo.check_public_collection(demo.DevData(KIT / "data" / "dev", meta), REGISTER)


def test_an_unreviewed_or_changed_file_is_refused(tmp_path):
    from .fixtures import write_split
    col = demo.DevData(write_split(tmp_path / "s"), {"key": "x", "title": "x", "public": True})
    with pytest.raises(SystemExit, match="not a reviewed file"):
        demo.check_public_collection(col, REGISTER)


@needs_audio
def test_register_covers_exactly_the_50_reviewed_files():
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
def test_public_scenarios_decisions_equal_offline_replay(app, model_id):
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
def test_public_config_offers_two_detectors_and_hides_internals(app):
    cfg = app.config()
    assert cfg["mode"] == "public"
    assert [m["id"] for m in cfg["models"]] == ["p3", "baseline"]
    for m in cfg["models"]:
        assert set(m) == {"id", "name", "revision", "about", "dirty", "available"} and m["available"]
    assert [c["key"] for c in cfg["collections"]] == ["xdev", "holdout"]
    assert len(cfg["scenarios"]) == 50 and all(s["title"] and s["explanation"] for s in cfg["scenarios"])
    assert {s["category"] for s in cfg["scenarios"]} == {"GI", "OV", "BC", "NV", "BG"}
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
        if not (f.startswith("data/extra/") and f.endswith(".wav")):
            probes += [f"/api/audio/{name}", f"/api/scenario/{name}"]
    probes += ["/.git/config", "/.git/HEAD", "/demo/server.py", "/server.py", "/.env", "/models/silero_vad.onnx",
               "/api/audio/GI-D01.wav", "/api/scenario/GI-D01", "/api/audio/GI-P01.wav",
               "/api/audio/..%2F..%2Fdev%2Faudio%2FGI-D01.wav", "//etc/passwd"]
    with serving(app) as base:
        reachable = [p for p in probes if status(base + p) != 404]
        assert reachable == []
        for ok in ("/", "/app.js", "/style.css", "/healthz", "/robots.txt", "/api/config", "/api/audio/GI-D51.wav",
                   "/api/scenario/GI-H54", "/api/audio/OV-H52.wav"):
            assert status(base + ok) == 200, ok
        r = urllib.request.urlopen(base + "/")
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert r.headers["Server"].startswith("sarj-demo/1")       # no Python version banner


@needs_audio
def test_every_public_audio_file_is_served_and_playable(app):
    import wave
    import io
    with serving(app) as base:
        for sid, scen in app.data.scenarios.items():
            body = urllib.request.urlopen(f"{base}/api/audio/{sid}.wav").read()
            assert hashlib.sha256(body).hexdigest() == REGISTER["public_wav_sha256"][sid]
            with wave.open(io.BytesIO(body)) as w:
                assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 16000)
                assert w.getnframes() * 1000 // 16000 >= scen.label["audio"]["duration_ms"] - 1
            tl = json.loads(urllib.request.urlopen(f"{base}/api/scenario/{sid}").read())
            assert tl["ground_truth"]["events"] == scen.label["events"] and tl["credits"]


def test_image_contains_only_whitelisted_paths():
    copies = [line.split()[1:-1] for line in (KIT / "demo" / "Dockerfile").read_text(encoding="utf-8").splitlines()
              if line.startswith("COPY ")]
    flat = [p for c in copies for p in c]
    assert not any(p.startswith(("data/dev", "experiments", "baseline", "tests", ".git", "demo/public_data")) for p in flat)
    assert "data/extra/xdev/audio/" in flat and "data/extra/holdout/audio/" in flat
    assert "models/silero_vad.onnx" in flat and "models/SILERO_VAD_LICENSE" in flat and "neural_vad.py" in flat
    req = (KIT / "demo" / "requirements-public.txt").read_text(encoding="utf-8")
    assert "onnxruntime==1.29.0" in req and "numpy==2.5.5" in req
    render = (KIT / "render.yaml").read_text(encoding="utf-8")
    assert "branch: feat/demo-ui" in render and "healthCheckPath: /healthz" in render


# ------------------------------------------------------------------------------------------------ limits
@needs_audio
def test_capacity_eviction_and_rate_limits():
    app = public_app(max_sessions=2, evict_idle_s=60.0, sessions_per_min=600.0)
    a = app.new_session("GI-D51", "p3")["session_id"]
    app.new_session("GI-D51", "baseline")
    with pytest.raises(demo.Refused) as e:                       # both active: the next visitor is told to wait
        app.new_session("GI-D51", "p3")
    assert e.value.status == 503
    app.sessions[a].last_seen -= 120                             # one went idle: it can be replaced
    app.new_session("GI-D51", "p3")
    assert a not in app.sessions

    app = public_app(sessions_per_min=60.0)
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
    app = public_app(advance_per_s=5.0)
    sid = app.new_session("GI-D51", "p3")["session_id"]
    r = app.advance(sid, 1e9)                                   # cannot run the whole call at once
    assert not r["done"] and r["fed_ms"] <= app.settings.lookahead_ms + 1000 + 200
    with pytest.raises(demo.Refused) as e:
        for _ in range(50):
            app.advance(sid, 0)
    assert e.value.status == 429


@needs_audio
def test_reset_and_repeated_playback_give_identical_results(app):
    """Each playback is a fresh detector (P3's VAD state included): replaying a call gives the same decisions."""
    runs = []
    for _ in range(3):
        for model in ("p3", "baseline"):
            sess = demo.DetectorSession(app.models[model], app.data.scenarios["GI-H54"])
            sess.advance(1e9)
            runs.append((model, [(d["stream_ms"], d["decision"]) for d in sess.decisions]))
    assert runs[0::2] == [runs[0]] * 3 and runs[1::2] == [runs[1]] * 3


@needs_audio
def test_http_limits(app):
    with serving(app) as base:
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
    assert demo.Settings.for_public(port=1).collections == ("xdev", "holdout")
    shutil.which("git")                                         # (keeps the import used on systems without git)
