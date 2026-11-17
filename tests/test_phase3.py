"""Phase 3 detector (default configuration): neural speech frames + caller-relative loudness, on REAL speech.

The signals are caller-channel excerpts of the kit's dev calls (TTS speech) re-mixed into small calls: a greeting
while the agent is silent, then one event while the agent talks. No label is read; times are sample positions.
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

import my_system as ms
import neural_vad

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data" / "dev"
pytestmark = pytest.mark.skipif(not (DEV / "audio" / "AM-D02.wav").exists(), reason="dev audio not present")
EVENT_AT, AGENT = 6000, (3000, 11500)


def clip(scenario, start_ms, end_ms):
    with wave.open(str(DEV / "audio" / f"{scenario}.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)
    return x[start_ms * 16:end_ms * 16, 1].astype(np.float64)


GREETING = lambda: clip("AM-D02", 5530, 6040)          # noqa: E731  «إيه نعم.»
QUESTION = lambda: clip("AM-D02", 11730, 14230)        # noqa: E731  «طيب؟ … بس كم المبلغ بالضبط؟» (2.5 s)
TALK_OVER = lambda: clip("OV-D03", 12240, 17250)       # noqa: E731  loud, sustained (5 s)


def call(event, rel_db=0.0, gain_db=0.0, greeting=None, total_ms=12000):
    x = np.random.default_rng(0).standard_normal(total_ms * 16) * 10 ** (-66 / 20) * 32768     # room tone
    if greeting is not None:
        x[500 * 16:500 * 16 + len(greeting)] += greeting
    e = event * 10 ** (rel_db / 20)
    x[EVENT_AT * 16:EVENT_AT * 16 + len(e)] += e[:len(x) - EVENT_AT * 16]
    return np.clip(np.round(x * 10 ** (gain_db / 20)), -32768, 32767).astype(np.int16)


def stops(caller, system=None, new_call=True):
    """STOP times in ms after the event onset."""
    s = system or ms.MySystem()
    if new_call:
        s.on_scenario_start({"use_case": "test", "language": "ar"})
    out, silence = [], np.zeros(320, np.int16)
    for n in range(len(caller) // 320):
        t = n * 20
        if t == AGENT[0]:
            s.on_agent_start("x", AGENT[1] - AGENT[0])
        if t == AGENT[1]:
            s.on_agent_end()
        if s.on_frame(silence, caller[n * 320:(n + 1) * 320]) == "STOP":
            out.append(t - EVENT_AT)
    return out


# ------------------------------------------------------------------ the three tiers
def test_speech_at_the_callers_own_level_stops_fast():
    s = stops(call(QUESTION(), 0, greeting=GREETING()))
    assert len(s) == 1 and 0 < s[0] <= 400


def test_short_soft_speech_is_kept():
    """600 ms, 12 dB below the caller's greeting: an acknowledgement-like sound must not stop the agent."""
    assert stops(call(QUESTION()[:600 * 16], -12, greeting=GREETING())) == []


def test_soft_but_sustained_speech_still_stops_later():
    """The same voice 12 dB down but going on for 2.5 s: not missed, only later (the price of the soft tier)."""
    s = stops(call(QUESTION(), -12, greeting=GREETING()))
    assert len(s) == 1 and 1000 <= s[0] <= 2000


def test_speech_far_below_the_callers_level_never_stops():
    """20 dB under the caller's own level is treated as background (TV, another talker, echo)."""
    assert stops(call(TALK_OVER(), -20, greeting=clip("OV-D03", 4870, 5450))) == []


# ------------------------------------------------------------------ gain: the Phase 3 claim, and its limits
@pytest.mark.parametrize("gain_db", [-20, -12, 6])
def test_microphone_gain_does_not_change_the_fast_decision(gain_db):
    """Same decision at any gain; its time may move by a few frames (the VAD's own output is not exactly scale-free)."""
    greeting = clip("OV-D03", 4870, 5450)
    nominal = stops(call(TALK_OVER(), 0, 0, greeting))
    scaled = stops(call(TALK_OVER(), 0, gain_db, greeting))
    assert len(nominal) == len(scaled) == 1 and abs(scaled[0] - nominal[0]) <= 60 and scaled[0] <= 400


@pytest.mark.parametrize("gain_db", [-12, 6])
def test_microphone_gain_does_not_turn_a_kept_sound_into_a_stop(gain_db):
    assert stops(call(QUESTION()[:600 * 16], -12, gain_db, GREETING())) == []


def test_before_the_caller_has_spoken_the_absolute_gate_still_applies():
    """Known limit, stated as behaviour: with no reference yet, a very quiet line (-30 dB) is not heard,
    while the same line is heard once the caller has spoken a greeting."""
    assert len(stops(call(TALK_OVER(), 0, 0, greeting=None))) == 1
    assert stops(call(TALK_OVER(), 0, -30, greeting=None)) == []
    assert len(stops(call(TALK_OVER(), 0, -30, greeting=clip("OV-D03", 4870, 5450)))) == 1


# ------------------------------------------------------------------ what the VAD is for
def test_a_loud_harmonic_buzz_is_not_speech():
    """E01b's periodicity test accepts any periodic sound; the neural VAD does not (horns, engines, hum)."""
    t = np.arange(16000 * 2) / 16000
    buzz = sum(np.sin(2 * np.pi * k * 140 * t) / k for k in range(1, 20))
    buzz = buzz / np.sqrt(np.mean(buzz ** 2)) * 10 ** (-20 / 20) * 32768
    assert stops(call(buzz, 0, greeting=GREETING())) == []


def test_a_loud_noise_burst_is_not_speech():
    burst = np.random.default_rng(1).standard_normal(16000) * 10 ** (-18 / 20) * 32768
    assert stops(call(burst, 0, greeting=GREETING())) == []


# ------------------------------------------------------------------ state
def test_the_reference_is_forgotten_at_scenario_start():
    """A loud caller in one call must not make the next caller look soft."""
    loud_greeting, event = GREETING() * 10 ** (14 / 20), QUESTION()
    system = ms.MySystem()
    stops(call(event, 0, greeting=loud_greeting), system)                       # call 1 sets a high reference
    fresh = stops(call(event, 0, greeting=None), system, new_call=True)         # call 2: reference cleared
    assert len(fresh) == 1 and fresh[0] <= 400
    system = ms.MySystem()
    stops(call(event, 0, greeting=loud_greeting), system)
    carried = stops(call(event, 0, greeting=None), system, new_call=False)      # without the reset: judged soft
    assert carried == [] or carried[0] >= 1000


def test_one_stop_per_utterance_and_the_window_is_cleared_between_utterances():
    system = ms.MySystem()
    caller = call(TALK_OVER(), 0, greeting=clip("OV-D03", 4870, 5450))
    assert len(stops(caller, system)) == 1
    assert len(stops(caller, system, new_call=False)) == 1                      # a later utterance can stop again


# ------------------------------------------------------------------ the VAD wrapper
def test_vad_reset_makes_a_call_reproducible():
    vad = neural_vad.load("silero")
    frames = call(QUESTION(), 0, greeting=GREETING())[:16000 * 8].reshape(-1, 320)
    first = [vad(f) for f in frames]
    vad.reset()
    assert [vad(f) for f in frames] == first
    assert max(first) > 0.9 and np.mean(first[:20]) < 0.1                      # speech found, room tone is not speech


def test_vad_uses_only_complete_32_ms_windows():
    vad = neural_vad.load("silero")
    speech = QUESTION().astype(np.int16)
    assert vad(speech[4000:4320]) == 0.0            # 320 samples: no complete 512-sample window yet
    assert len(vad.pending) == 320
    vad(speech[4320:4640])                          # 640 samples: one window consumed, 128 left for the next
    assert len(vad.pending) == 128


def test_a_wrong_model_file_is_refused(monkeypatch):
    monkeypatch.setattr(neural_vad.SileroStream, "_session", None)
    monkeypatch.setattr(neural_vad, "MODEL_SHA256", "0" * 64)
    with pytest.raises(RuntimeError):
        neural_vad.load("silero")


# ------------------------------------------------------------------ E01b is still in the file
def test_the_e01b_options_reproduce_the_committed_e01b_run(monkeypatch):
    """With the acoustic front end selected, the detector makes the decisions recorded in the E01b real-time run."""
    sys.path.insert(0, str(ROOT / "tools"))
    import replay
    from ttbench.dataset import lay_out_stream, load_split
    for name, value in {"VAD": None, "CALLER_REF": False, "EVIDENCE_WINDOW_MS": 300}.items():
        monkeypatch.setattr(ms, name, value)
    log, meta = replay.replay(ms.MySystem, lay_out_stream(load_split(DEV)))
    mine = [(int(l["stream_ms"]) // 20, l["msg"]["decision"]) for l in log
            if l["dir"] == "rx" and l["msg"].get("type") == "decision"]
    recorded = [json.loads(l) for l in (ROOT / "experiments" / "evidence" / "e01b-e566234-rt" / "log.jsonl")
                .read_text(encoding="utf-8").splitlines() if l.strip()]
    theirs = [(int(l["stream_ms"]) // 20, l["msg"]["decision"]) for l in recorded
              if l["dir"] == "rx" and l["msg"].get("type") == "decision"]
    assert meta["errors"] == [] and len(mine) == 37 and mine == theirs
