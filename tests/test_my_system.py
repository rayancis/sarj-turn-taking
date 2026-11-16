"""Behaviour of my_system.py (experiment 1: adaptive noise floor), on synthetic signals only.

No dev labels or dev audio are used here: the signals are generated, so the tests describe the intended
behaviour (steady noise is not speech, a voice onset is) rather than the outcome on particular dev calls.
"""
import numpy as np
import pytest

import my_system as ms

SR = 16000
FRAME = 320


def db_to_amp(db):
    return 10 ** (db / 20) * 32768


def white(ms_, db, seed=0):
    x = np.random.default_rng(seed).standard_normal(SR * ms_ // 1000)
    return x / np.sqrt(np.mean(x ** 2)) * db_to_amp(db)


def rumble(ms_, db, seed=1):
    """Car-like noise: integrated (brown) noise, most energy below 300 Hz."""
    x = np.cumsum(np.random.default_rng(seed).standard_normal(SR * ms_ // 1000))
    x -= np.convolve(x, np.ones(400) / 400, mode="same")          # remove drift
    return x / np.sqrt(np.mean(x ** 2)) * db_to_amp(db)


def voice(ms_, db, f0=140.0):
    """Speech-like: harmonic complex up to 3.4 kHz with a 4 Hz syllabic envelope (never fully silent)."""
    t = np.arange(SR * ms_ // 1000) / SR
    x = sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(1, int(3400 // f0) + 1))
    x *= 0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)
    return x / np.sqrt(np.mean(x ** 2)) * db_to_amp(db)


def mix(total_ms, *parts):
    """parts: (start_ms, signal). Returns int16 caller channel."""
    out = np.zeros(SR * total_ms // 1000)
    for start, sig in parts:
        a = SR * start // 1000
        out[a:a + len(sig)] += sig[:max(0, len(out) - a)]
    return np.clip(out, -32768, 32767).astype(np.int16)


def run_call(caller, agent_spans, system=None, new_call=True):
    """Feed a caller signal frame by frame; agent talks during agent_spans [(start_ms, end_ms)].
    Returns [(time_ms, decision)], with time = start of the frame that produced the decision."""
    s = system or ms.MySystem()
    if new_call:
        s.on_scenario_start({"use_case": "test", "language": "ar"})
    agent = np.zeros(FRAME, dtype=np.int16)
    out = []
    for n in range(len(caller) // FRAME):
        t = n * 20
        for a, b in agent_spans:
            if t == b:
                s.on_agent_end()
            if t == a:
                s.on_agent_start("نص", b - a)
        d = s.on_frame(agent, caller[n * FRAME:(n + 1) * FRAME])
        if d is not None:
            out.append((t, d))
    return out


def stops(decisions):
    return [t for t, d in decisions if d == "STOP"]


# ------------------------------------------------------------------ steady noise is not a reason to stop
@pytest.mark.parametrize("noise", [white(12000, -34), rumble(12000, -30)], ids=["white-34dB", "rumble-30dB"])
def test_steady_noise_above_the_old_fixed_level_never_stops(noise):
    caller = mix(12000, (0, noise))
    # the failure mode being fixed: this noise is louder than the baseline's fixed -42 dBFS speech level
    assert ms.loudness_db(caller[:FRAME]) > ms.LOUD_DB
    assert stops(run_call(caller, [(300, 5000), (6500, 12000)])) == []


def test_noise_from_the_first_frame_is_tolerated_when_the_agent_starts_early():
    """Cold start: only 100-300 ms of history when the agent starts talking."""
    caller = mix(6000, (0, white(6000, -36)))
    assert stops(run_call(caller, [(100, 6000)])) == []


# ------------------------------------------------------------------ a real voice still stops the agent quickly
@pytest.mark.parametrize("snr_db", [12, 20])
def test_voice_onset_in_noise_stops_within_latency_budget(snr_db):
    noise_db = -38
    caller = mix(9000, (0, white(9000, noise_db)), (5000, voice(2000, noise_db + snr_db)))
    s = stops(run_call(caller, [(300, 9000)]))
    assert len(s) == 1
    assert 5000 <= s[0] <= 5000 + 300, s        # 200 ms of evidence + at most a few frames


def test_voice_onset_in_quiet_room_stops():
    caller = mix(6000, (0, white(6000, -65)), (3000, voice(1500, -25)))
    s = stops(run_call(caller, [(300, 6000)]))
    assert len(s) == 1 and 3000 <= s[0] <= 3000 + 260


def test_sound_shorter_than_200_ms_does_not_stop():
    caller = mix(5000, (0, white(5000, -65)), (2500, voice(140, -20)))
    assert stops(run_call(caller, [(300, 5000)])) == []


def test_voice_below_the_baseline_absolute_level_is_ignored():
    """A voice far above a quiet floor but below the baseline's absolute -42 dBFS is still ignored."""
    caller = mix(6000, (0, white(6000, -70)), (2000, voice(2000, -48)))
    assert stops(run_call(caller, [(300, 6000)])) == []


def test_dips_between_syllables_do_not_reset_the_evidence():
    """Two 120 ms syllables with a 60 ms dip: no 200 ms consecutive stretch, but 240 ms of speech in one run."""
    floor = white(6000, -65)
    caller = mix(6000, (0, floor), (2000, voice(120, -22)), (2180, voice(120, -22)))
    s = stops(run_call(caller, [(300, 6000)]))
    assert len(s) == 1 and 2180 <= s[0] <= 2300


def test_separate_bursts_with_longer_gaps_do_not_add_up():
    """Cough- or knock-like bursts of 120 ms every 270 ms: gaps of 150 ms end each run."""
    parts = [(2000 + k * 270, voice(120, -20)) for k in range(6)]
    caller = mix(6000, (0, white(6000, -65)), *parts)
    assert stops(run_call(caller, [(300, 6000)])) == []


# ------------------------------------------------------------------ silence and digital zeros
def test_digital_silence_is_safe():
    caller = np.zeros(SR * 4, dtype=np.int16)
    assert run_call(caller, [(300, 4000)]) == []
    assert np.isfinite(ms.band_level_db(caller[:FRAME]))


def test_voice_after_digital_silence_still_stops():
    caller = mix(5000, (2000, voice(1500, -25)))           # exact zeros, then a voice
    s = stops(run_call(caller, [(300, 5000)]))
    assert len(s) == 1 and 2000 <= s[0] <= 2260


# ------------------------------------------------------------------ state and reset behaviour
def test_no_stop_while_the_agent_is_silent():
    caller = mix(6000, (0, white(6000, -65)), (2000, voice(2000, -20)))
    assert stops(run_call(caller, [(4500, 6000)])) == []    # the voice ends before the agent starts


def test_one_stop_per_utterance_and_a_new_utterance_can_stop_again():
    caller = mix(10000, (0, white(10000, -65)), (1000, voice(2000, -20)), (6000, voice(2000, -20)))
    s = stops(run_call(caller, [(300, 4000), (5000, 10000)]))
    assert len(s) == 2 and 1000 <= s[0] <= 1260 and 6000 <= s[1] <= 6260


def test_scenario_start_forgets_the_previous_calls_noise_floor():
    """A quiet call followed by a noisy one: without the reset, the old quiet floor would make the new call's
    steady noise look like speech for the first seconds."""
    system = ms.MySystem()
    assert stops(run_call(mix(4000, (0, white(4000, -65))), [(300, 3500)], system)) == []
    noisy = mix(6000, (0, white(6000, -34, seed=5)))
    assert stops(run_call(noisy, [(300, 6000)], system, new_call=True)) == []


def test_without_the_reset_the_carried_over_floor_would_fire():
    """Guards the previous test: it only passes because of on_scenario_start, not by accident."""
    system = ms.MySystem()
    run_call(mix(4000, (0, white(4000, -65))), [(300, 3500)], system)
    noisy = mix(6000, (0, white(6000, -34, seed=5)))
    assert stops(run_call(noisy, [(300, 6000)], system, new_call=False)) != []


def test_scenario_start_clears_a_stop_left_from_the_previous_call():
    system = ms.MySystem()
    run_call(mix(3000, (0, white(3000, -65)), (1000, voice(1000, -20))), [(300, 3000)], system)
    assert system.stopped                                   # call ended mid-utterance, no agent_utterance_end
    system.on_scenario_start({})
    assert not system.stopped and not system.agent_talking and system.speech_ms == 0 and system.gap_ms == 0
    assert len(system.floor.levels) == 0


def test_on_frame_returns_only_valid_decisions():
    caller = mix(8000, (0, white(8000, -36)), (3000, voice(2000, -20)))
    for _, d in run_call(caller, [(300, 5000)]):
        assert d in ("STOP", "RESUME", "TAKE_TURN")
