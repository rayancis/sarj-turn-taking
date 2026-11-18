"""Live AI conversation (demo/live.py): Gemini Live speaks, SARJ decides barge-in.

What must hold:
1. The live loop runs the real detectors exactly as the kit does: on the frames of a dev scenario, P3's first STOP
   lands on the same frame as in the offline replay.
2. Only the controlling detector's STOP stops the agent; at that frame the page is told to flush, Gemini receives
   activityStart and then the caller's audio, starting with a bounded pre-roll. Baseline in shadow never controls.
3. While the agent speaks and there is no STOP (backchannel, cough), Gemini receives nothing from the caller.
4. Gemini is configured so it cannot interrupt on its own (automatic activity detection disabled), the key never
   leaves the server, and the usage limits hold.
"""
from __future__ import annotations

import base64
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "demo"))
sys.path.insert(0, str(KIT / "tools"))

import live  # noqa: E402
import server as demo  # noqa: E402
from replay import replay  # noqa: E402
from ttbench.dataset import lay_out_stream, load_split  # noqa: E402

from .fake_gemini import FakeGemini, agent_audio_24k  # noqa: E402
from .test_demo import serving  # noqa: E402

DEV = KIT / "data" / "dev"


@pytest.fixture(scope="module")
def models():
    return {mid: demo.Model(mid, spec) for mid, spec in demo.DEFAULT_MODELS.items()}


class StubWS:
    def __init__(self):
        self.json, self.binary = [], []

    def send_json(self, obj):
        self.json.append(obj)

    def send_bytes(self, b):
        self.binary.append(b)


class StubGemini:
    def __init__(self, session):
        self.s, self.sent = session, []

    def send(self, obj):
        self.sent.append((self.s.n, obj))

    def audio(self, pcm):
        self.sent.append((self.s.n, {"audio_bytes": len(pcm)}))


def drive(models, sid, controller="p3"):
    """Feed a dev scenario to LiveSession as the browser would: agent channel = what is played, flags from the
    agent utterances; the agent stops being played after a barge-in flush."""
    scen = load_split(DEV, [sid])[0]
    ws = StubWS()
    gate = live.LiveGate(live.LiveSettings(api_key="x", verify_model=False))
    s = live.LiveSession(ws, models, gate, controller)
    s.model = "test"
    s.gemini = StubGemini(s)
    utts = [(u["start_ms"] // 20, math.ceil(u["end_ms"] / 20)) for u in scen.label["agent_utterances"]]
    audio = scen.audio
    chunk = {"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000",
                                                                        "data": base64.b64encode(b"\0\0").decode()}}]}}}
    silenced_until = -1
    for n in range(len(audio) // 320):
        for a, b in utts:
            if n == max(0, a - 1):               # Gemini's response arrives just before it is played
                if s.user_open:
                    s._close_user("test: response")
                s.on_gemini(chunk)
                s.on_gemini({"serverContent": {"turnComplete": True}})
        u = next(((a, b) for a, b in utts if a <= n < b), None)
        playing = u is not None and silenced_until < u[0]
        flags = (1 if playing else 0) | (0 if playing else 2)
        frame = audio[n * 320:(n + 1) * 320].copy()
        if not playing:
            frame[:, 0] = 0                       # nothing played: the agent channel is silent
        before = s.stats["barge_ins"]
        s.on_frame(bytes([flags]) + frame.astype("<i2").tobytes())
        if s.stats["barge_ins"] > before:
            silenced_until = u[0] if u else n
    return s, ws


def first_stop_frame(model, sid):
    log, _ = replay(model.new_system, lay_out_stream(load_split(DEV, [sid])))
    stops = [l["stream_ms"] // 20 for l in log if l["dir"] == "rx" and l["msg"].get("decision") == "STOP"]
    return stops[0] if stops else None


@pytest.mark.parametrize("sid", ["GI-D01", "GI-D02", "GI-D03", "OV-D01", "AM-D02", "DV-D03"])
def test_live_loop_stops_on_the_same_frame_as_the_offline_replay(models, sid):
    want = first_stop_frame(models["p3"], sid)
    assert want is not None
    s, ws = drive(models, sid)
    stops = [m for m in ws.json if m.get("type") == "decision" and m["model"] == "p3" and m["decision"] == "STOP"]
    assert stops and stops[0]["frame"] == want and stops[0]["controls"], (sid, want, stops[:1])
    flush = [m for m in ws.json if m.get("type") == "flush"]
    assert flush and flush[0]["frame"] == want and flush[0]["by"] == "p3"
    # Gemini: activityStart at that very frame, then the pre-roll (up to 1.5 s of the caller before the decision)
    at = [(n, m) for n, m in s.gemini.sent if n == want]
    assert at[0][1] == {"realtimeInput": {"activityStart": {}}}
    assert at[1][1]["audio_bytes"] == min(want + 1, live.PRE_ROLL_BARGE_MS // 20) * 640
    # nothing of the caller reached Gemini while the agent was speaking before the STOP
    a, b = next((u["start_ms"] // 20, math.ceil(u["end_ms"] / 20)) for u in load_split(DEV, [sid])[0].label["agent_utterances"]
                if u["start_ms"] // 20 <= want < math.ceil(u["end_ms"] / 20))
    assert not [m for n, m in s.gemini.sent if a <= n < want and ("audio_bytes" in m or "activityStart" in
                                                                  m.get("realtimeInput", {}))]


@pytest.mark.parametrize("sid", ["NV-D01", "BC-D01", "NV-D02", "AM-D01"])
def test_backchannels_and_noises_never_reach_gemini_while_the_agent_speaks(models, sid):
    assert first_stop_frame(models["p3"], sid) is None
    s, ws = drive(models, sid)
    assert s.stats["barge_ins"] == 0 and not [m for m in ws.json if m.get("type") == "flush"]
    utts = [(u["start_ms"] // 20, math.ceil(u["end_ms"] / 20)) for u in load_split(DEV, [sid])[0].label["agent_utterances"]]
    during = [m for n, m in s.gemini.sent if any(a <= n < b for a, b in utts)
              and ("audio_bytes" in m or "activityStart" in m.get("realtimeInput", {}))]
    assert during == [], during[:3]


def test_baseline_in_shadow_never_controls_and_can_be_made_the_controller(models):
    s, ws = drive(models, "NV-D01")                         # the cough: Baseline stops, P3 does not
    base = [m for m in ws.json if m.get("type") == "decision" and m["model"] == "baseline" and m["decision"] == "STOP"]
    assert base and not base[0]["controls"] and s.stats["barge_ins"] == 0
    s, ws = drive(models, "NV-D01", controller="baseline")
    flush = [m for m in ws.json if m.get("type") == "flush"]
    assert flush and flush[0]["by"] == "baseline" and flush[0]["frame"] == first_stop_frame(models["baseline"], "NV-D01")


# ------------------------------------------------------------------------------------------------ over the wire
def start_app(fake_url, **live_kw):
    app = demo.DemoApp(None, demo.DEFAULT_MODELS, demo.Settings.for_public(port=1))
    app.live_settings = live.LiveSettings(api_key="test-key-123", url=fake_url, model="gemini-3.8-live", verify_model=False,
                                          **live_kw)
    app.live_gate = live.LiveGate(app.live_settings)
    return app


def converse(base, caller16k, seconds=12.0, speed=4.0, controller="p3"):
    """A simulated browser: plays what it receives (24 -> 16 kHz), sends [played agent, caller] frames."""
    from websockets.sync.client import connect
    cm = connect(base.replace("http", "ws") + "/api/live", origin=base, proxy=None, open_timeout=10)
    ws = cm.__enter__()
    ws.send(json.dumps({"type": "start", "controller": controller}))
    msgs, queue, gen, ready = [], np.zeros(0), 0, False
    t_end = time.monotonic() + 30
    while not ready and time.monotonic() < t_end:
        m = json.loads(ws.recv(timeout=10))
        msgs.append(m)
        ready = m.get("type") == "ready"
        if m.get("type") == "error":
            ws.close()
            return msgs
    n = 0
    while n < seconds * 50:
        while True:                                      # everything the server sent so far
            try:
                raw = ws.recv(timeout=0)
            except TimeoutError:
                break
            if isinstance(raw, bytes):
                if raw[0] >= gen:
                    gen = raw[0]
                    x = np.frombuffer(raw[1:], "<i2").astype(float)
                    queue = np.concatenate([queue, np.interp(np.arange(0, len(x), 1.5), np.arange(len(x)), x)])
            else:
                m = json.loads(raw)
                msgs.append(m)
                if m.get("type") == "flush":
                    queue, gen = np.zeros(0), m["gen"] & 0xFF
        played = queue[:320]
        queue = queue[320:]
        agent = np.zeros(320)
        agent[:len(played)] = played
        caller = caller16k[n * 320:(n + 1) * 320]
        caller = np.pad(caller, (0, 320 - len(caller)))
        flags = (1 if len(played) else 0) | (2 if len(queue) == 0 else 0)
        ws.send(bytes([flags]) + np.stack([agent, caller], 1).round().astype("<i2").tobytes())
        n += 1
        time.sleep(0.02 / speed)
    ws.send(json.dumps({"type": "end"}))
    try:
        while True:
            raw = ws.recv(timeout=5)
            if isinstance(raw, str):
                msgs.append(json.loads(raw))
                if msgs[-1].get("type") == "ended":
                    break
    except (TimeoutError, Exception):  # noqa: BLE001
        pass
    ws.close()
    return msgs


def caller_from(sid, from_s):
    scen = load_split(DEV, [sid])[0]
    return scen.audio[int(from_s * 16000):, 1].astype(float)


def agent_from(sid):
    lab = load_split(DEV, [sid])[0].label
    a1 = max(lab["agent_utterances"], key=lambda u: u["end_ms"] - u["start_ms"])
    return a1["start_ms"] / 1000, (a1["end_ms"] - a1["start_ms"]) / 1000


def test_real_protocol_barge_in_is_sarjs_decision():
    """GI-D01 over the wire: the agent's reply is GI-D01's agent turn; the caller interrupts during it."""
    start_s, dur = agent_from("GI-D01")
    fake = FakeGemini(agent_audio_24k(DEV / "audio" / "GI-D01.wav", start_s, dur))
    app = start_app(fake.start())
    with serving(app) as base:
        msgs = converse(base, caller_from("GI-D01", start_s), seconds=dur + 1)
    # Gemini was configured so that it cannot detect speech or interrupt on its own; the key stayed in a header
    rc = fake.setup["realtimeInputConfig"]
    assert rc["automaticActivityDetection"]["disabled"] is True and rc["activityHandling"] == "START_OF_ACTIVITY_INTERRUPTS"
    assert fake.headers.get("x-goog-api-key") == "test-key-123"
    assert all("test-key-123" not in json.dumps(m, ensure_ascii=False) for m in msgs)
    stops = [m for m in msgs if m.get("type") == "decision" and m["decision"] == "STOP" and m["controls"]]
    flush = [m for m in msgs if m.get("type") == "flush"]
    assert stops and flush and stops[0]["frame"] == flush[0]["frame"]
    summary = next(m for m in msgs if m.get("type") == "ended")["summary"]
    assert summary["barge_ins"] == 1 and summary["gemini_interrupted_unrequested"] == 0
    assert summary["playback_stop_after_stop_ms"]["max"] <= 60          # the played agent audio stopped at once
    assert fake.audio_outside_activity == 0 and fake.audio_in_activity > 0
    assert "activityStart" in fake.kinds() and fake.kinds().count("setup") == 1


def test_real_protocol_backchannel_keeps_the_agent_talking():
    start_s, dur = agent_from("BC-D01")
    fake = FakeGemini(agent_audio_24k(DEV / "audio" / "BC-D01.wav", start_s, dur))
    app = start_app(fake.start())
    with serving(app) as base:
        msgs = converse(base, caller_from("BC-D01", start_s), seconds=dur + 1)
    summary = next(m for m in msgs if m.get("type") == "ended")["summary"]
    assert summary["barge_ins"] == 0 and not [m for m in msgs if m.get("type") == "flush"]
    starts = [e for e in fake.log if e["kind"] == "activityStart"]
    done = next(e["t"] for e in fake.log if e["kind"] == "response_complete")
    assert all(not e["generating"] for e in starts)                       # never during the agent's reply
    assert fake.audio_outside_activity == 0
    assert all(e["t"] >= done for e in starts) or not starts


def test_limits_errors_and_origin():
    fake = FakeGemini(agent_audio_24k(DEV / "audio" / "GI-D01.wav", 7.9, 2.0))
    url = fake.start()
    # no key: disabled, explained, nothing connects
    app = demo.DemoApp(None, demo.DEFAULT_MODELS, demo.Settings.for_public(port=1))
    app.live_settings = live.LiveSettings(api_key=None, url=url, verify_model=False)
    app.live_gate = live.LiveGate(app.live_settings)
    assert app.config()["live"]["enabled"] is False and "not configured" in app.config()["live"]["reason"]
    assert "key" not in json.dumps(app.config()["live"]).replace("no Gemini API key", "")
    with serving(app) as base:
        msgs = converse(base, np.zeros(16000), seconds=0.2)
        assert msgs[0]["type"] == "error" and "not configured" in msgs[0]["message"]
        # another site cannot open the socket
        import urllib.error
        import urllib.request
        req = urllib.request.Request(base + "/api/live", headers={"Upgrade": "websocket", "Connection": "Upgrade",
                                     "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version": "13",
                                     "Origin": "https://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403
    # concurrency and hourly limits
    gate = live.LiveGate(live.LiveSettings(api_key="k", url=url, verify_model=False, max_concurrent=1, per_hour=2))
    gate.acquire()
    with pytest.raises(live.LiveRefused, match="busy"):
        gate.acquire()
    gate.release()
    gate.acquire()
    gate.release()
    with pytest.raises(live.LiveRefused, match="hourly"):
        gate.acquire()
    # exhausted quota: the feature pauses itself
    gate = live.LiveGate(live.LiveSettings(api_key="k", url=url, verify_model=False))
    gate.quota_exhausted("1011 RESOURCE_EXHAUSTED")
    assert gate.status()["enabled"] is False and "quota" in gate.status()["reason"]


def test_an_unrequested_interruption_would_be_reported(models):
    """The evidence counter works: an 'interrupted' that SARJ did not cause is flagged."""
    s = live.LiveSession(StubWS(), models, live.LiveGate(live.LiveSettings(api_key="x", verify_model=False)))
    s.gemini = StubGemini(s)
    s.on_gemini({"serverContent": {"interrupted": True}})                 # no activityStart was ever sent
    assert s.summary()["gemini_interrupted_unrequested"] == 1 and not s.summary()["sarj_controlled_every_barge_in"]
    s._send_activity("activityStart", "barge_in")
    s.on_gemini({"serverContent": {"interrupted": True}})                 # this one SARJ asked for
    assert s.summary()["gemini_interrupted_unrequested"] == 1 and s.summary()["gemini_interrupted"] == 2
