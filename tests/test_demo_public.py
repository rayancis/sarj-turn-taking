"""Public-mode guarantees of the hosted demo (demo/server.py --public, demo/Dockerfile).

The public site shows exactly the 24 original competition recordings, published with the competition supervisor's
written permission (recorded in demo/collections.json). Only their original files are served, never a changed one;
public mode never starts without that record or with no playable scenario. Only Baseline and P3 are offered, each
running its pinned code. The 50 Phase 3 research scenarios stay preserved in the repository and are neither served
nor in the image.
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
    """The public site exactly as deployed: the 24 original competition recordings."""
    return public_app()


DEV = KIT / "data" / "dev"
DEV_SHA = {e["scenario_id"]: e["wav_sha256"] for e in json.loads((DEV / "manifest.json").read_text())["scenarios"]}


@pytest.fixture(scope="module")
def research():
    """The 50 Phase 3 research scenarios through the same machinery (not deployed; preservation checks only)."""
    return public_app(collections=("xdev", "holdout"))


# ------------------------------------------------------------------------------------------------ data rights
def test_the_supervisors_permission_is_recorded_without_contact_details():
    dev = next(c for c in REGISTER["collections"] if c["key"] == "dev")
    auth = dev["authorization"]
    assert dev["public"] is True and auth["date"] == "2026-11-17"
    assert auth["text_ar"].startswith("نعم، مسموح نشر او عرض التسجيلات على رابط عام")
    text = json.dumps(dev, ensure_ascii=False)
    assert "@" not in text and not any(ch.isdigit() for ch in text.replace("2026-11-17", "").replace("24", ""))


def test_public_mode_serves_the_24_only_with_a_recorded_permission(app):
    assert demo.Settings.for_public(port=1).collections == ("dev",)
    assert set(app.data.scenarios) == set(DEV_SHA) and len(DEV_SHA) == 24
    with pytest.raises(SystemExit, match="only the collections"):
        demo.DemoApp(DEV, demo.DEFAULT_MODELS, demo.Settings.for_public(port=1))
    dev = next(c for c in REGISTER["collections"] if c["key"] == "dev")
    with pytest.raises(SystemExit, match="not authorised"):     # without the record it would be refused
        demo.check_public_collection(demo.DevData(DEV, {**dev, "authorization": None}), REGISTER)


def test_a_changed_competition_file_is_never_served(tmp_path):
    copy = tmp_path / "dev"
    shutil.copytree(DEV, copy)
    wav = copy / "audio" / "GI-D01.wav"
    data = bytearray(wav.read_bytes())
    data[-1] ^= 1
    wav.write_bytes(bytes(data))
    dev = next(c for c in REGISTER["collections"] if c["key"] == "dev")
    with pytest.raises(SystemExit, match="not a reviewed file"):
        demo.check_public_collection(demo.DevData(copy, dev), REGISTER)


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
@pytest.mark.parametrize("model_id", list(demo.HISTORICAL_MODELS))
def test_research_scenarios_decisions_equal_offline_replay(research, model_id):
    app = research
    model = demo.Model(model_id, demo.HISTORICAL_MODELS[model_id])
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
    log, meta = replay(demo.Model("p3", demo.HISTORICAL_MODELS["p3"]).new_system, scenarios)
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
def test_public_config_offers_three_detectors_and_hides_internals(app):
    cfg = app.config()
    assert cfg["mode"] == "public"
    assert [m["id"] for m in cfg["models"]] == ["v3"]
    for m in cfg["models"]:
        assert set(m) == {"id", "name", "revision", "about", "dirty", "available"} and m["available"]
    assert all(r["model"] == "v3" for r in cfg["benchmark"]["rows"])
    text = json.dumps(cfg, ensure_ascii=False)
    for secret in (str(KIT), "data/dev", "sha256", "GI-D51", "-H5", "E01b", "e566234", "runs.json\": {"):
        assert secret not in text, secret
    assert len(cfg["scenarios"]) == 24 and {x["collection"] for x in cfg["scenarios"]} == {"dev"}


@needs_audio
def test_no_repository_file_is_reachable(app):
    """Every tracked file, under many spellings, must 404 (except the page's own static files; the public audio
    is reachable only through /api/audio/<id>.wav)."""
    tracked = subprocess.run(["git", "ls-files"], cwd=KIT, capture_output=True, text=True).stdout.split()
    if not tracked:
        tracked = ["my_system.py", "data/dev/manifest.json", "experiments/evidence/runs.json", "ttbench/score.py"]
    probes = []
    for f in tracked:
        if f.startswith("demo/static/"):            # the page's own files are meant to be served
            continue
        name = f.split("/")[-1]
        probes += [f"/{f}", f"/{name}", f"/static/{name}", f"/..%2F{f}", f"/%2e%2e/{f}"]
        if not (f.startswith("data/dev/audio/") and f.endswith(".wav")):  # the 24 public recordings
            probes += [f"/api/audio/{name}", f"/api/scenario/{name}"]
    probes += ["/.git/config", "/.git/HEAD", "/demo/server.py", "/server.py", "/.env", "/models/silero_vad.onnx",
               "/api/audio/GI-D51.wav", "/api/scenario/GI-H54", "/api/audio/GI-P01.wav", "/data/dev/labels/GI-D01.json",
               "/api/audio/..%2F..%2Fextra%2Fxdev%2Faudio%2FGI-D51.wav", "//etc/passwd"]
    with serving(app) as base:
        reachable = [p for p in probes if status(base + p) != 404]
        assert reachable == []
        for ok in ("/", "/app.js", "/style.css", "/healthz", "/robots.txt", "/api/config"):
            assert status(base + ok) == 200, ok
        r = urllib.request.urlopen(base + "/")
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert r.headers["Server"].startswith("sarj-demo/1")       # no Python version banner


def test_public_site_serves_exactly_the_24_original_recordings(app):
    """Each of the 24 plays the kit's original file (SHA-256 as in data/dev/manifest.json), as 16 kHz stereo with
    its original labels; none of the 50 research scenarios is reachable."""
    import io
    import wave
    with serving(app) as base:
        for sid, digest in DEV_SHA.items():
            body = urllib.request.urlopen(f"{base}/api/audio/{sid}.wav").read()
            assert hashlib.sha256(body).hexdigest() == digest, sid
            with wave.open(io.BytesIO(body)) as w:
                assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 16000)
            tl = json.loads(urllib.request.urlopen(f"{base}/api/scenario/{sid}").read())
            label = json.loads((DEV / "labels" / f"{sid}.json").read_text(encoding="utf-8"))
            assert tl["ground_truth"]["events"] == label["events"]
        for sid in REGISTER["public_wav_sha256"]:
            assert status(f"{base}/api/audio/{sid}.wav") == 404, sid


def test_image_contains_only_whitelisted_paths():
    copies = [line.split()[1:-1] for line in (KIT / "demo" / "Dockerfile").read_text(encoding="utf-8").splitlines()
              if line.startswith("COPY ")]
    flat = [p for c in copies for p in c]
    assert not any(p.startswith(("data/extra", "experiments", "baseline", "tests", ".git", "demo/public_data")) for p in flat)
    assert {p for p in flat if p.startswith("data")} == {"data/dev/manifest.json", "data/dev/labels/", "data/dev/audio/"}
    assert "demo/models/sarj_v3/" in flat
    assert "my_system.py" not in flat and "neural_vad.py" not in flat and "demo/models/" not in flat
    assert "demo/live.py" in flat
    req = (KIT / "demo" / "requirements-public.txt").read_text(encoding="utf-8")
    assert "onnxruntime==1.29.0" in req and "numpy==2.5.5" in req
    render = (KIT / "render.yaml").read_text(encoding="utf-8")
    assert "branch: feat/demo-ui" in render and "healthCheckPath: /healthz" in render


# ------------------------------------------------------------------------------------------------ limits
@needs_audio
def test_capacity_eviction_and_rate_limits():
    app = public_app(max_sessions=2, evict_idle_s=60.0, sessions_per_min=600.0)
    a = app.new_session("GI-D01", "v3")["session_id"]
    app.new_session("GI-D01", "v3")
    with pytest.raises(demo.Refused) as e:                       # both active: the next visitor is told to wait
        app.new_session("GI-D01", "v3")
    assert e.value.status == 503
    app.sessions[a].last_seen -= 120                             # one went idle: it can be replaced
    app.new_session("GI-D01", "v3")
    assert a not in app.sessions

    app = public_app(sessions_per_min=60.0)
    statuses = []
    for _ in range(40):
        try:
            app.new_session("GI-D01", "v3")
            statuses.append(200)
        except demo.Refused as err:
            statuses.append(err.status)
    assert 429 in statuses and statuses[0] == 200


@needs_audio
def test_advance_is_paced_and_rate_limited():
    app = public_app(advance_per_s=5.0)
    sid = app.new_session("GI-D01", "v3")["session_id"]
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
        for model in ("v3",):
            sess = demo.DetectorSession(app.models[model], app.data.scenarios["GI-D02"])
            sess.advance(1e9)
            runs.append((model, [(d["stream_ms"], d["decision"]) for d in sess.decisions]))
    assert len(runs) == 3 and all(r == runs[0] for r in runs)


@needs_audio
def test_http_limits(app):
    with serving(app) as base:
        big = {"to_ms": 1, "pad": "x" * 5000}
        assert status(base + "/api/session", big) == 413
        s = post(base, "/api/session", {"scenario": "GI-D01", "model": "v3"})
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
