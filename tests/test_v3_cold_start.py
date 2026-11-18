"""SARJ v3 when it has heard nothing before the agent starts speaking (Gemini Live greets first).

Regression for the BG-D02 cold-start false STOP: the noise floor started at -90 dB and may
only rise 3 dB/s, so steady street noise read as strong speech for many seconds. The fix
starts the floor at the first caller frame. These tests check the failure mode with
synthetic audio, that real interruptions are still detected, and on the dev calls that no
real interruption the old start detected is lost (acoustics only; the words layer needs
real time and is measured by the official run).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo/models/sarj_v3"))
sys.path.insert(0, str(ROOT / "tools"))
from tt.config import Config  # noqa: E402
from tt.decider import Decider, STOP  # noqa: E402

SR, N = 16000, 320
rng = np.random.default_rng(7)


def street(seconds, db=-30.0):
    """Broadband traffic-like noise with a slow level drift, at about `db` dBFS."""
    x = rng.standard_normal(int(seconds * SR))
    x = np.convolve(x, np.ones(4) / 4, mode="same")          # a little low-pass tilt
    x *= 1 + 0.2 * np.sin(2 * np.pi * 0.3 * np.arange(len(x)) / SR)
    return x / np.sqrt(np.mean(x ** 2)) * 10 ** (db / 20)


def voice(seconds, db=-14.0, f0=125.0):
    """Voiced, syllable-modulated harmonic signal: speech-like for an energy/voicing frontend."""
    t = np.arange(int(seconds * SR)) / SR
    f = f0 * (1 + 0.05 * np.sin(2 * np.pi * 3 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = sum(np.sin(k * ph) / k for k in range(1, 20))
    x *= 0.55 + 0.45 * np.sin(2 * np.pi * 4.5 * t) ** 2        # ~4-5 syllables per second
    return x / np.sqrt(np.mean(x ** 2)) * 10 ** (db / 20)


def i16(x):
    return np.clip(x * 32768, -32768, 32767).astype(np.int16)


def run(caller, agent=None, **cfg):
    """Cold start: the agent starts speaking on the very first frame. Returns STOP times (ms)."""
    d = Decider(Config(**cfg))
    if agent is None:
        # an agent phrase pattern: 1.2 s of speech, 0.4 s pause
        agent = voice(len(caller) / SR, db=-35, f0=210)
        agent *= (np.arange(len(agent)) / SR % 1.6) < 1.2
    caller, agent = i16(caller), i16(agent)
    d.agent_start("هلا والله، معك المساعد. كيف أقدر أخدمك اليوم؟", len(caller) / SR * 1000)
    stops = []
    for i in range(0, len(caller) - N + 1, N):
        if d.frame(agent[i:i + N], caller[i:i + N]) == STOP:
            stops.append(i / SR * 1000)
    return stops


def test_steady_street_noise_does_not_stop_a_cold_detector():
    assert run(street(6)) == []


def test_the_legacy_floor_reproduces_the_false_stop():
    # guards the test itself: with the original -90 dB start the same audio stops the agent
    assert run(street(6), noise_init_first_frame=False)


def test_real_interruption_in_noise_still_stops_a_cold_detector():
    noise = street(5)
    caller = noise.copy()
    caller[2 * SR:4 * SR] += voice(2)
    stops = run(caller)
    assert stops and 2000 <= stops[0] <= 3000


def test_floor_recovers_when_the_call_opens_with_loud_speech():
    """Starting at the first frame must not deafen the detector if that frame is speech."""
    caller = street(5)
    caller[:int(0.4 * SR)] += voice(0.4, db=-8)
    caller[3 * SR:5 * SR] += voice(2)
    stops = [t for t in run(caller) if t >= 1000]
    assert stops and 3000 <= stops[0] <= 4000


def test_floor_is_reset_for_every_call():
    from tt.frontend import Frontend
    fe = Frontend(Config())
    loud, quiet = i16(street(0.2, db=-20)), i16(street(0.2, db=-50))
    z = np.zeros(N, dtype=np.int16)
    fe.process(z, loud[:N])
    high = fe.noise.sum()
    fe.reset_call()
    fe.process(z, quiet[:N])
    assert fe.noise.sum() < high / 100


needs_dev = pytest.mark.skipif(not (ROOT / "data/dev/audio/BG-D02.wav").exists(), reason="dev audio missing")


@needs_dev
def test_dev_calls_cold_and_warm_keep_every_real_interruption():
    import v3_cold_start as cs
    new = cs.run(Config())
    old = cs.run(Config(noise_init_first_frame=False))
    # BG-D02 (street noise, nothing said) no longer stops when the detector starts cold
    assert new["BG-D02"]["cold"]["stops"] == []
    def total(rows, mode, key):
        return sum(r[mode][key] for r in rows.values())
    for mode in ("warm", "cold"):
        # real interruptions: no fewer timely stops and no more misses in total
        assert total(new, mode, "hit") >= total(old, mode, "hit"), mode
        assert total(new, mode, "missed") <= total(old, mode, "missed"), mode
    # every call whose interruption the old start caught warm is still caught warm
    for sid in new:
        if any(k == "hit" for _, k in old[sid]["warm"]["stops"]):
            assert any(k == "hit" for _, k in new[sid]["warm"]["stops"]), sid
    assert total(new, "warm", "false") <= total(old, "warm", "false")
    assert total(new, "cold", "false") < total(old, "cold", "false")
