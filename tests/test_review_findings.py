"""Deterministic reproductions of the independent review findings on E01 (8f08311).

Each test states the behaviour we want. While a finding is open the test is marked xfail(strict=True), so it must
fail; the marker is removed by the change that fixes it. Signals are generated (no dev audio or labels).
"""
import numpy as np
import pytest

from tests.test_my_system import mix, rumble, run_call, stops, voice, white

SR = 16000


def steady_voice(ms_, db, f0=140.0):
    """A sustained vowel: harmonic complex without a syllabic envelope."""
    t = np.arange(SR * ms_ // 1000) / SR
    x = sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, int(3400 // f0) + 1))
    return x / np.sqrt(np.mean(x ** 2)) * 10 ** (db / 20) * 32768


OPEN_F1 = pytest.mark.xfail(strict=True, reason="finding 1 open: floor initialised from speech")
OPEN_F3 = pytest.mark.xfail(strict=True, reason="finding 3 open: level step read as speech")


def bursts(on_ms, off_ms, n, db, start_ms):
    return [(start_ms + k * (on_ms + off_ms), voice(on_ms, db)) for k in range(n)]


# ------------------------------------------------------------------ finding 1: speech-contaminated initialisation
@OPEN_F1
@pytest.mark.parametrize("make", [steady_voice, voice], ids=["sustained", "syllabic"])
def test_f1_voice_from_the_first_frame_of_a_call_stops(make):
    caller = mix(6000, (0, white(6000, -65)), (0, make(6000, -25)))
    s = stops(run_call(caller, [(300, 6000)]))
    assert len(s) == 1 and s[0] <= 300 + 300, s


def test_f1_control_same_voice_after_one_second_of_noise_stops():
    caller = mix(6000, (0, white(6000, -65)), (1000, steady_voice(5000, -25)))
    s = stops(run_call(caller, [(300, 6000)]))
    assert len(s) == 1 and 1000 <= s[0] <= 1300, s


# ------------------------------------------------------------------ finding 2: sparse bursts accumulate
@pytest.mark.parametrize("on_ms,off_ms", [(60, 100), (40, 100)])
def test_f2_sparse_bursts_do_not_accumulate_into_a_stop(on_ms, off_ms):
    caller = mix(6000, (0, white(6000, -65)), *bursts(on_ms, off_ms, 10, -20, 2000))
    assert stops(run_call(caller, [(300, 6000)])) == []


# ------------------------------------------------------------------ finding 3: abrupt background level change
@OPEN_F3
@pytest.mark.parametrize("make", [white, rumble], ids=["white", "rumble"])
def test_f3_noise_step_without_speech_does_not_stop(make):
    noise = np.concatenate([make(3000, -65), make(5000, -30, seed=7)])
    assert stops(run_call(mix(8000, (0, noise)), [(300, 8000)])) == []


@OPEN_F3
@pytest.mark.parametrize("make", [white, rumble], ids=["white", "rumble"])
def test_f3_control_voice_after_a_noise_step_still_stops(make):
    """The fix must not simply go deaf after a level change: a caller 15 dB above the new noise still counts."""
    noise = np.concatenate([make(3000, -65), make(7000, -35, seed=7)])
    caller = mix(10000, (0, noise), (6500, voice(2000, -20)))
    s = stops(run_call(caller, [(300, 10000)]))
    assert len(s) == 1 and 6500 <= s[0] <= 6500 + 400, s
