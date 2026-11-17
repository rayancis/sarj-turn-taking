"""Public-mode guarantees of the hosted demo (demo/server.py --public, demo/Dockerfile).

What must hold before the site is exposed: only the original, publicly licensed demo scenarios are served; no
repository file, competition label, evaluation artifact or detector source is reachable over HTTP; the image never
contains them; limits hold; and the detectors behave exactly as in the kit's replay on the public scenarios too.
"""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
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

from .test_demo import decisions_of, post, run_uneven, serving, status  # noqa: E402

PUBLIC = KIT / "demo" / "public_data"


def public_app(**overrides) -> demo.DemoApp:
    settings = demo.Settings.for_public(port=1)
    for k, v in overrides.items():
        setattr(settings, k, v)
    return demo.DemoApp(None, demo.DEFAULT_MODELS, settings)


@pytest.fixture(scope="module")
def app():
    return public_app()


# ------------------------------------------------------------------------------------------------ data rights
def test_public_mode_refuses_competition_data(tmp_path):
    for bad in (KIT / "data" / "dev", KIT / "data"):
        settings = demo.Settings.for_public(port=1)
        with pytest.raises(SystemExit, match="no public licence"):
            demo.DemoApp(bad, demo.DEFAULT_MODELS, settings)
    # a folder that does not declare a public licence is refused too
    from .fixtures import write_split
    with pytest.raises(SystemExit, match="does not declare"):
        demo.DemoApp(write_split(tmp_path / "s"), demo.DEFAULT_MODELS, demo.Settings.for_public(port=1))


def test_public_scenarios_are_original_licensed_and_intact():
    manifest = json.loads((PUBLIC / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["distribution"] == "public" and manifest["license"]
    dev_hashes = {e["wav_sha256"] for e in json.loads((KIT / "data" / "dev" / "manifest.json").read_text(
        encoding="utf-8"))["scenarios"]}
    import jsonschema
    schema = json.loads((KIT / "schema" / "scenario.schema.json").read_text(encoding="utf-8"))
    schema["properties"]["scenario_id"]["pattern"] = "^(GI|BC|HT|BG|NV|AM|OV|DV)-P[0-9]{2}$"
    for e in manifest["scenarios"]:
        wav = PUBLIC / "audio" / f"{e['scenario_id']}.wav"
        label = PUBLIC / "labels" / f"{e['scenario_id']}.json"
        assert hashlib.sha256(wav.read_bytes()).hexdigest() == e["wav_sha256"]
        assert hashlib.sha256(label.read_bytes()).hexdigest() == e["label_sha256"]
        assert e["wav_sha256"] not in dev_hashes
        lab = json.loads(label.read_text(encoding="utf-8"))
        jsonschema.validate(lab, schema)
        assert "-P" in lab["scenario_id"] and lab["provenance"]["production_mode"] == "synthetic"


# ------------------------------------------------------------------------------------------------ real behaviour
@pytest.mark.parametrize("model_id", list(demo.DEFAULT_MODELS))
def test_public_scenarios_decisions_equal_offline_replay(app, model_id):
    model = app.models[model_id]
    assert model.available, model.error
    rng = random.Random(11)
    for sid, scen in app.data.scenarios.items():
        sess = demo.DetectorSession(model, scen)
        run_uneven(sess, rng)
        log, _ = replay(model.new_system, lay_out_stream(load_split(PUBLIC, [sid])))
        assert [(d["stream_ms"], d["decision"]) for d in sess.decisions] == decisions_of(log), sid


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
def test_public_config_hides_paths_hashes_and_evaluation_results(app):
    cfg = app.config()
    assert cfg["mode"] == "public" and cfg["benchmark"] is None
    assert cfg["dataset"]["public"] and cfg["dataset"]["license"]
    for m in cfg["models"]:
        assert set(m) == {"id", "name", "revision", "dirty", "available"}
    text = json.dumps(cfg)
    for secret in ("runs.json", "experiments", str(KIT), "data/dev", "sha256", "GI-D0"):
        assert secret not in text
    names = {m["id"]: m["name"] for m in cfg["models"]}
    assert names["baseline"] == "Baseline"
    if hashlib.sha256((KIT / "my_system.py").read_bytes()).hexdigest() in demo.KNOWN_SOURCES:
        assert names["current"] in {"E01", "E01b", "Baseline"}


def test_no_repository_file_is_reachable(app):
    """Every tracked file, under many spellings, must 404 (except the page's own static files and public audio)."""
    tracked = subprocess.run(["git", "ls-files"], cwd=KIT, capture_output=True, text=True).stdout.split()
    if not tracked:
        tracked = ["my_system.py", "data/dev/manifest.json", "experiments/evidence/runs.json", "ttbench/score.py"]
    allowed = {"demo/static/index.html", "demo/static/app.js", "demo/static/style.css"}
    probes = []
    for f in tracked:
        if f in allowed or f.startswith("demo/public_data/audio/"):
            continue
        name = f.split("/")[-1]
        probes += [f"/{f}", f"/{name}", f"/static/{name}", f"/..%2F{f}", f"/%2e%2e/{f}", f"/api/audio/{name}",
                   f"/api/scenario/{name}"]
    probes += ["/.git/config", "/.git/HEAD", "/demo/server.py", "/server.py", "/.env", "/api/audio/GI-D01.wav",
               "/api/scenario/GI-D01", "/api/audio/..%2F..%2Fdata%2Fdev%2Faudio%2FGI-D01.wav", "//etc/passwd"]
    with serving(app) as base:
        reachable = [p for p in probes if status(base + p) != 404]
        assert reachable == []
        for ok in ("/", "/app.js", "/style.css", "/healthz", "/robots.txt", "/api/config", "/api/audio/GI-P01.wav",
                   "/api/scenario/GI-P01"):
            assert status(base + ok) == 200, ok
        r = urllib.request.urlopen(base + "/")
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert r.headers["Server"].startswith("sarj-demo/1")       # no Python version banner


def test_image_contains_only_whitelisted_paths():
    copies = [line.split()[1] for line in (KIT / "demo" / "Dockerfile").read_text(encoding="utf-8").splitlines()
              if line.startswith("COPY ")]
    assert copies == ["demo/requirements-public.txt", "ttbench/", "my_system.py", "demo/server.py", "demo/static/",
                      "demo/models/", "demo/public_data/manifest.json", "demo/public_data/labels/",
                      "demo/public_data/audio/"]
    assert "--public" in (KIT / "demo" / "Dockerfile").read_text(encoding="utf-8")
    render = (KIT / "render.yaml").read_text(encoding="utf-8")
    assert "branch: feat/demo-ui" in render and "plan: free" in render and "healthCheckPath: /healthz" in render


# ------------------------------------------------------------------------------------------------ limits
def test_capacity_eviction_and_rate_limits():
    app = public_app(max_sessions=2, evict_idle_s=60.0, sessions_per_min=600.0)
    a = app.new_session("GI-P01", "current")["session_id"]
    app.new_session("GI-P01", "current")
    with pytest.raises(demo.Refused) as e:                       # both active: the third visitor is told to wait
        app.new_session("GI-P01", "current")
    assert e.value.status == 503
    app.sessions[a].last_seen -= 120                             # one went idle: it can be replaced
    app.new_session("GI-P01", "current")
    assert a not in app.sessions

    app = public_app(sessions_per_min=60.0)
    statuses = []
    for _ in range(40):
        try:
            app.new_session("GI-P01", "current")
            statuses.append(200)
        except demo.Refused as err:
            statuses.append(err.status)
    assert 429 in statuses and statuses[0] == 200


def test_advance_is_paced_and_rate_limited():
    app = public_app(advance_per_s=5.0)
    sid = app.new_session("GI-P01", "current")["session_id"]
    r = app.advance(sid, 1e9)                                   # cannot run the whole call at once
    assert not r["done"] and r["fed_ms"] <= app.settings.lookahead_ms + 1000 + 200
    with pytest.raises(demo.Refused) as e:
        for _ in range(50):
            app.advance(sid, 0)
    assert e.value.status == 429


def test_http_limits(app):
    with serving(app) as base:
        big = {"to_ms": 1, "pad": "x" * 5000}
        assert status(base + "/api/session", big) == 413
        s = post(base, "/api/session", {"scenario": "GI-P01", "model": "current"})
        assert status(base + f"/api/session/{s['session_id']}/close", {}) == 200
        assert status(base + f"/api/session/{s['session_id']}/advance", {"to_ms": 10}) == 404
        req = urllib.request.Request(base + "/healthz", method="HEAD")
        r = urllib.request.urlopen(req)
        assert r.status == 200 and r.read() == b""


def test_public_server_listens_on_all_interfaces_only_in_public_mode():
    assert demo.Settings().host == "127.0.0.1"
    assert demo.Settings.for_public(port=1).host == "0.0.0.0"
    assert demo.Settings.for_public(port=1).data_dir == demo.PUBLIC_DATA
