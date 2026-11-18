"""SARJ v3 when it has heard nothing before the agent starts speaking (Gemini Live greets first).

Regression for the BG-D02 cold-start false STOP: the noise floor started at -90 dB and may
only rise 3 dB/s, so steady street noise read as strong speech for many seconds. The fix
seeds the floor from the line when the agent starts before the caller's level is known.
These tests check the failure mode with synthetic audio, that real interruptions are still
detected, and on the dev calls that warm (benchmark) decisions are unchanged (acoustics
only; the words layer needs real time and is measured by the official run).
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
    assert run(street(6), cold_start_noise_seed=False)


def test_real_interruption_in_noise_still_stops_a_cold_detector():
    noise = street(5)
    caller = noise.copy()
    caller[2 * SR:4 * SR] += voice(2)
    stops = run(caller)
    assert stops and 2000 <= stops[0] <= 3000


def test_caller_already_talking_when_the_agent_starts_still_stops():
    """Seeding on a speech frame must not deafen the detector (E01b finding 1: voice from the first frame).

    Words of 0.7 s with 0.2 s pauses: the first pause sets the floor, then the usual evidence is
    needed. Known cost: the stop comes ~0.7 s later than with the -90 dB floor, which reached
    0.7 s only by treating any sound, street noise included, as speech. A voice with no pause
    at all cannot be told from steady noise by level alone and is not asserted here."""
    v = voice(5)
    caller = street(5) + v * ((np.arange(len(v)) / SR % 0.9) < 0.7)
    stops = run(caller)
    assert stops and stops[0] <= 1600


def test_floor_recovers_when_the_call_opens_with_loud_speech():
    """Seeding high must not deafen the detector if the first frame is speech."""
    caller = street(5)
    caller[:int(0.4 * SR)] += voice(0.4, db=-8)
    caller[3 * SR:5 * SR] += voice(2)
    stops = [t for t in run(caller) if t >= 1000]
    assert stops and 3000 <= stops[0] <= 4000


def test_seed_only_when_the_caller_level_is_unknown():
    d = Decider(Config())
    d.agent_start("x", 1000)
    assert d.fe.seed_noise                      # cold: nothing heard without the agent
    d = Decider(Config())
    d.caller_ref_db = -30.0
    d.agent_start("x", 1000)
    assert not d.fe.seed_noise                  # warm: the tuned floor is left alone
    d = Decider(Config(cold_start_noise_seed=False))
    d.agent_start("x", 1000)
    assert not d.fe.seed_noise


def test_a_new_call_starts_unseeded():
    from tt.frontend import Frontend
    fe = Frontend(Config())
    fe.seed_noise = True
    fe.process(np.zeros(N, dtype=np.int16), i16(street(0.02, db=-20)))
    assert not fe.seed_noise


needs_dev = pytest.mark.skipif(not (ROOT / "data/dev/audio/BG-D02.wav").exists(), reason="dev audio missing")


@needs_dev
def test_dev_calls_cold_and_warm_keep_every_real_interruption():
    import v3_cold_start as cs
    new = cs.run(Config())
    old = cs.run(Config(cold_start_noise_seed=False))
    # BG-D02 (street noise, nothing said) no longer stops when the detector starts cold
    assert new["BG-D02"]["cold"]["stops"] == []
    def total(rows, mode, key):
        return sum(r[mode][key] for r in rows.values())
    for mode in ("warm", "cold"):
        # real interruptions: no fewer timely stops and no more misses in total
        assert total(new, mode, "hit") >= total(old, mode, "hit"), mode
        assert total(new, mode, "missed") <= total(old, mode, "missed"), mode
    # calls heard before the agent speaks (the benchmark) keep exactly the tuned decisions
    for sid in new:
        assert new[sid]["warm"] == old[sid]["warm"], sid
    assert total(new, "cold", "false") < total(old, "cold", "false")
