"""'Test your own audio' (POST /api/custom): the visitor's audio becomes the caller channel of a scenario whose agent
channel and timing come from one of the 24 recordings. It must run the real detectors exactly as on any scenario, be
kept in memory only, stay private (unguessable, unlisted, expiring) and respect size and rate limits."""
from __future__ import annotations

import builtins
import io
import json
import re
import sys
import urllib.request
import wave
from pathlib import Path

import numpy as np
import pytest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "demo"))

import server as demo  # noqa: E402

from .test_demo import post, serving, status  # noqa: E402


@pytest.fixture(scope="module")
def app():
    settings = demo.Settings.for_public(port=1)
    settings.custom_per_min = 0.0                   # functional tests; the rate limit has its own test below
    return demo.DemoApp(None, demo.DEFAULT_MODELS, settings)


def caller_pcm(app, sid) -> bytes:
    return app.data.scenarios[sid].audio[:, 1].astype("<i2").tobytes()


def decisions(app, model, scen):
    sess = demo.DetectorSession(app.models[model], scen)
    sess.advance(1e9)
    return [(d["stream_ms"], d["decision"]) for d in sess.decisions]


@pytest.mark.parametrize("sid", ["NV-D01", "GI-D01", "BG-D01"])
def test_own_audio_equal_to_the_original_caller_gives_the_original_decisions(app, sid):
    """Uploading a scenario's own caller channel at offset 0 rebuilds that scenario exactly: same audio, same
    agent timing, so both detectors must decide exactly as on the original."""
    r = app.create_custom(sid, 0, caller_pcm(app, sid))
    own, orig = app.scenario(r["scenario_id"]), app.data.scenarios[sid]
    assert np.array_equal(own.audio, orig.audio)
    assert own.label["agent_utterances"] == orig.label["agent_utterances"]
    for model in ("baseline", "p3"):
        assert decisions(app, model, own) == decisions(app, model, orig), (sid, model)


def test_own_audio_is_placed_at_its_offset_with_the_original_agent(app):
    tone = (np.sin(np.arange(16000) * 2 * np.pi * 200 / 16000) * 8000).astype("<i2").tobytes()
    r = app.create_custom("GI-D01", 5000, tone)
    own, orig = app.scenario(r["scenario_id"]), app.data.scenarios["GI-D01"]
    assert (r["offset_ms"], r["caller_ms"], r["agent_from"]) == (5000, 1000, "GI-D01")
    assert np.array_equal(own.audio[:len(orig.audio), 0], orig.audio[:, 0])
    assert not own.audio[:5000 * 16, 1].any() and own.audio[5000 * 16:6000 * 16, 1].any()
    assert own.label["events"] == [] and own.label["custom"]["agent_from"] == "GI-D01"


def test_http_round_trip_and_privacy(app):
    with serving(app) as base:
        req = urllib.request.Request(f"{base}/api/custom?agent=NV-D01&offset_ms=0", caller_pcm(app, "NV-D01"),
                                     {"Content-Type": "application/octet-stream"})
        r = json.loads(urllib.request.urlopen(req).read())
        cid = r["scenario_id"]
        assert re.fullmatch(r"U[0-9a-f]{32}", cid)                       # unguessable
        cfg = json.loads(urllib.request.urlopen(base + "/api/config").read())
        assert cid not in json.dumps(cfg) and len(cfg["scenarios"]) == 24  # unlisted
        res = urllib.request.urlopen(f"{base}/api/audio/{cid}.wav")
        assert res.headers["Cache-Control"] == "no-store"
        with wave.open(io.BytesIO(res.read())) as w:
            assert (w.getnchannels(), w.getframerate()) == (2, 16000)
        tl = json.loads(urllib.request.urlopen(f"{base}/api/scenario/{cid}").read())
        assert tl["ground_truth"]["events"] == [] and tl["custom"]["agent_from"] == "NV-D01"
        s = post(base, "/api/session", {"scenario": cid, "model": "baseline"})
        assert s["scenario_id"] == cid
        home = urllib.request.urlopen(base + "/")
        assert "microphone=(self)" in home.headers["Permissions-Policy"]
        assert urllib.request.urlopen(base + "/recorder.js").headers["Content-Type"].startswith("text/javascript")


def test_limits_and_bad_input(app):
    with serving(app) as base:
        def up(q, body):
            return status(f"{base}/api/custom?{q}", None) if body is None else _post_raw(f"{base}/api/custom?{q}", body)
        assert up("agent=GI-D01&offset_ms=0", b"\0" * int(60.5 * 32000)) == 413      # longer than 60 s
        assert up("agent=GI-D01&offset_ms=0", b"\0" * 6401) == 400                  # not 16-bit samples
        assert up("agent=GI-D01&offset_ms=0", b"\0" * 1000) == 400                  # under 0.2 s
        assert up("agent=GI-D51&offset_ms=0", b"\0" * 32000) == 404                 # research scenario: not offered
        assert up("agent=GI-D01&offset_ms=nan", b"\0" * 32000) == 400
        assert status(f"{base}/api/audio/U{'0' * 32}.wav") == 404                    # unknown id


def test_expiry_capacity_and_rate():
    app = demo.DemoApp(None, demo.DEFAULT_MODELS, demo.Settings.for_public(port=1))
    pcm = b"\0" * 32000
    a = app.create_custom("GI-D01", 0, pcm)["scenario_id"]
    app.custom[a] = (*app.custom[a][:2], app.custom[a][2] - app.settings.custom_ttl_s - 1)   # 15 minutes later
    with pytest.raises(KeyError):
        app.scenario(a)
    app.settings.custom_max = 3
    ids = [app.create_custom("GI-D01", 0, pcm)["scenario_id"] for _ in range(4)]
    assert ids[0] not in app.custom and len(app.custom) == 3                     # the oldest is dropped
    app.settings.custom_per_min = 6.0
    app.custom_bucket = demo.TokenBucket(0.1, 3)
    with pytest.raises(demo.Refused) as e:
        for _ in range(10):
            app.create_custom("GI-D01", 0, pcm)
    assert e.value.status == 429


def test_own_audio_is_never_written_to_disk(app, monkeypatch):
    real_open = builtins.open

    def no_writes(file, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            raise AssertionError(f"tried to write {file}")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_writes)
    monkeypatch.setattr(Path, "write_bytes", lambda *a, **k: (_ for _ in ()).throw(AssertionError("write_bytes")))
    r = app.create_custom("NV-D01", 0, caller_pcm(app, "NV-D01"))
    assert app.audio_bytes(r["scenario_id"])[:4] == b"RIFF"
    assert decisions(app, "p3", app.scenario(r["scenario_id"])) == decisions(app, "p3", app.data.scenarios["NV-D01"])


def _post_raw(url, body):
    req = urllib.request.Request(url, body, {"Content-Type": "application/octet-stream"})
    try:
        return urllib.request.urlopen(req).status
    except urllib.error.HTTPError as e:
        return e.code
