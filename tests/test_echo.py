"""Echo handling (echo_canceller.py) on generated signals only: no dev labels or dev audio.

Real failure modes covered:
- a call WITHOUT echo must pass through untouched, even while the caller talks over the agent (an always-on AEC3
  attenuated genuine interruptions by 4-16 dB; the gate exists to prevent that);
- a call WITH echo must have the echo removed once the path is found;
- a caller talking over the echo must survive the cancellation;
- a new call must not inherit the previous call's echo path.
"""
import numpy as np
import pytest

pytest.importorskip("livekit")

import echo_canceller as ec

SR, FRAME = 16000, 320


def talker(seconds, seed, db=-20.0):
    """Speech-like signal: coloured noise with a 4 Hz syllable envelope and pauses."""
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    x = np.convolve(rng.standard_normal(n), np.hanning(12), mode="same")
    t = np.arange(n) / SR
    env = np.clip(np.sin(2 * np.pi * 4.0 * t + rng.uniform(0, 6)), 0, None) ** 0.5
    env *= (np.sin(2 * np.pi * 0.35 * t + rng.uniform(0, 6)) > -0.6)      # occasional pauses
    x *= env
    return x / np.sqrt(np.mean(x ** 2)) * 10 ** (db / 20) * 32768


def room_echo(agent, delay_ms=200, loss_db=-18.0, rt60=0.4, seed=7):
    rng = np.random.default_rng(seed)
    n = int(SR * rt60)
    rir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)
    rir /= np.sqrt(np.sum(rir ** 2))
    y = np.convolve(agent, rir)[:len(agent)]
    d = SR * delay_ms // 1000
    y = np.concatenate([np.zeros(d), y[:-d]])
    return y * 10 ** (loss_db / 20) * np.sqrt(np.mean(agent ** 2)) / np.sqrt(np.mean(y ** 2) + 1e-12)


def run(agent, caller):
    c = ec.EchoCanceller()
    out, active = [], []
    for i in range(len(agent) // FRAME):
        s = slice(i * FRAME, (i + 1) * FRAME)
        out.append(c.process(agent[s].astype(np.int16), np.clip(caller[s], -32768, 32767).astype(np.int16)))
        active.append(c.active)
    return np.concatenate(out).astype(float), np.array(active)


def db(x):
    return 10 * np.log10(np.mean(np.square(x)) + 1e-9)


def noise(n, db_fs=-60.0, seed=3):
    return np.random.default_rng(seed).standard_normal(n) * 10 ** (db_fs / 20) * 32768


def test_no_echo_call_passes_through_untouched_even_during_an_interruption():
    agent = talker(10, seed=1)
    caller = noise(len(agent))
    caller[6 * SR:8 * SR] += talker(2, seed=2, db=-22.0)           # caller talks over the agent, no echo anywhere
    out, active = run(agent, caller)
    assert not active.any()
    assert np.array_equal(out, np.clip(caller[:len(out)], -32768, 32767).astype(np.int16).astype(float))


def test_echo_is_detected_and_removed():
    agent = talker(10, seed=4)
    caller = room_echo(agent) + noise(len(agent))
    out, active = run(agent, caller)
    first = int(np.argmax(active)) * FRAME
    assert active.any() and first < 4 * SR                           # found within 4 s of agent speech
    late = slice(first + SR, len(out))
    assert db(caller[late]) - db(out[late]) >= 10.0                   # at least 10 dB of the echo removed


def test_caller_talking_over_the_echo_survives():
    agent = talker(12, seed=5)
    voice = np.zeros(len(agent))
    voice[9 * SR:11 * SR] = talker(2, seed=6, db=-22.0)
    caller = room_echo(agent) + noise(len(agent)) + voice
    out, active = run(agent, caller)
    assert active[9 * SR // FRAME:11 * SR // FRAME].all()            # the canceller is in use during the interruption
    seg = slice(9 * SR, 11 * SR)
    assert db(out[seg]) - db(voice[seg]) >= -6.0                       # the caller is not suppressed


def test_reset_forgets_the_echo_path():
    agent = talker(6, seed=8)
    caller = room_echo(agent) + noise(len(agent))
    c = ec.EchoCanceller()
    for i in range(len(agent) // FRAME):
        s = slice(i * FRAME, (i + 1) * FRAME)
        c.process(agent[s].astype(np.int16), caller[s].astype(np.int16))
    assert c.active
    c.reset()
    assert not c.active and not c.detector.echo and not np.any(c.detector.W)
