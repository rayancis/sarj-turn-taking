"""Turn-taking system, experiment 1: adaptive noise floor + relative speech evidence for STOP.

    python -m ttbench.play --calls all         # official real-time run on all 24 dev calls
    python tools/replay.py --system my_system.py   # fast offline replay (diagnostic only)

Hypothesis. The kit's loudness baseline treats any caller-channel energy above a fixed -42 dBFS as speech, so steady
background noise above that level (car, street) makes the agent stop about 200 ms into every utterance. Judging
caller sound relative to an estimate of the noise floor, instead of only against a fixed level, should remove
those stops without making the system slower to react to a real caller.

STOP rule, per 20 ms caller frame (only audio already received is used; no labels, ids or transcripts):
  1. band level: energy in 300-3400 Hz (the telephone speech band; also ignores low rumble and hum), in dB.
  2. noise floor: the 10th percentile of the band levels of the last 150 *aperiodic* frames of this call (3 s when
     nothing periodic is heard). Periodic (voiced) frames never enter it, so a voice cannot become the "noise",
     even when it is there from the first frame. The floor is trusted once it holds 5 aperiodic frames.
     Periodicity: normalised autocorrelation of the last 40 ms (300-3400 Hz, magnitude compressed to the power
     0.67), peak over 75-500 Hz pitch periods, voiced when >= 0.45.
  3. speech frame, with hysteresis: becomes active when the band level is >= 10 dB above the floor, stays active
     while it is >= 6 dB above it. Every speech frame must also be louder than the baseline's absolute -42 dBFS,
     so in quiet rooms the system is never more sensitive than the baseline was. Until the floor is trusted, a
     frame is speech only if it is periodic and louder than -42 dBFS (the baseline rule, voiced frames only).
  4. bounded evidence: STOP once the last 300 ms of agent speech hold at least 200 ms of caller speech frames
     (the baseline's amount of evidence, at >= 2/3 occupancy). Dips between syllables (up to 100 ms) are
     tolerated, but sparse bursts cannot add up over time. Of those speech frames, at least 40 ms (2 frames)
     must be periodic: a level change without any voicing (a noise step, a cough, a door) can never stop the
     agent, however loud. Aperiodic noise practically never reaches the voicing threshold, while about half of
     natural speech frames do, so 2 voiced frames out of >= 10 is a low-risk requirement for real speech. One STOP per agent utterance. No RESUME.
State: the noise history is cleared at each scenario_start (a new call is a new acoustic environment); the
per-utterance counter and stop flag are cleared at each agent_utterance_start.

The end-of-turn path (TAKE_TURN, a separate leaderboard) is the kit baseline, unchanged.
"""
from collections import deque

import numpy as np

from ttbench.server import System, run

TEAM = "my-team"            # placeholder until the official team name is known

SAMPLE_RATE = 16000
FRAME_MS = 20               # each on_frame call covers 20 ms of audio
FRAME_SAMPLES = 320
LOUD_DB = -42               # baseline absolute level: still a minimum for speech, and the TAKE_TURN rule
STOP_AFTER_MS = 200         # speech for this long while the agent talks -> STOP (as in the baseline) ...
EVIDENCE_WINDOW_MS = 300    # ... counted within this many most recent ms of agent speech
VOICED_EVIDENCE_MS = 40     # ... of which at least this much (2 frames) must be periodic
TAKE_TURN_AFTER_MS = 700    # quiet for this long after the caller spoke -> TAKE_TURN (baseline)

BAND_HZ = (300, 3400)       # telephone speech band
FLOOR_WINDOW_MS = 3000      # noise-floor memory
FLOOR_PERCENTILE = 10       # low percentile of recent band levels = noise floor
FLOOR_MIN_FRAMES = 5        # need 100 ms of history before any frame can count as speech
SPEECH_ON_DB = 10.0         # band level this far above the floor starts a speech run
SPEECH_OFF_DB = 6.0         # ... and this far above keeps it going (hysteresis)
MIN_LEVEL_DB = -120.0       # clamp for digital silence (log of zero)

VOICING_THRESHOLD = 0.45    # periodicity at or above this = voiced frame
PITCH_HZ = (75, 500)        # pitch periods searched
VOICING_EXPONENT = 0.67     # spectral magnitude compression before autocorrelation (generalised autocorrelation)

_WINDOW = np.hanning(FRAME_SAMPLES)
_FREQS = np.fft.rfftfreq(FRAME_SAMPLES, 1 / SAMPLE_RATE)
_BAND = (_FREQS >= BAND_HZ[0]) & (_FREQS <= BAND_HZ[1])
_NORM = FRAME_SAMPLES * np.sum(_WINDOW ** 2) / 2      # a full-scale sine in the band reads about -3 dB

_V_LEN, _V_FFT = 2 * FRAME_SAMPLES, 1024                # 40 ms analysis, zero-padded (no circular wrap-around)
_V_WINDOW = np.hanning(_V_LEN)
_V_BAND = ((lambda f: (f >= BAND_HZ[0]) & (f <= BAND_HZ[1]))(np.fft.rfftfreq(_V_FFT, 1 / SAMPLE_RATE)))
_V_LAGS = slice(SAMPLE_RATE // PITCH_HZ[1], SAMPLE_RATE // PITCH_HZ[0] + 1)
_V_WIN_AC = np.fft.irfft(np.abs(np.fft.rfft(_V_WINDOW, _V_FFT)) ** 2, _V_FFT)
_V_WIN_AC = _V_WIN_AC[_V_LAGS] / _V_WIN_AC[0]          # window autocorrelation, to undo its taper (Boersma 1993)


def loudness_db(samples: np.ndarray) -> float:
    """Loudness of 20 ms of 16-bit audio, in dB below full scale (silence is about -90, speech -40 to -15)."""
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2)) / 32768
    return 20 * np.log10(rms + 1e-12)


def band_level_db(samples: np.ndarray) -> float:
    """Energy of one frame inside BAND_HZ, in dB relative to full scale."""
    x = samples.astype(np.float64) / 32768 * _WINDOW
    power = np.abs(np.fft.rfft(x)) ** 2
    return max(MIN_LEVEL_DB, 10 * np.log10(power[_BAND].sum() / _NORM + 1e-30))


def periodicity(samples: np.ndarray) -> float:
    """0 (aperiodic) .. 1 (perfectly periodic) for the last 40 ms of audio: peak of the normalised, window-corrected
    autocorrelation over pitch lags, computed on the compressed 300-3400 Hz spectrum."""
    x = samples.astype(np.float64)
    x = (x - x.mean()) * _V_WINDOW
    mag = np.abs(np.fft.rfft(x, _V_FFT)) ** (2 * VOICING_EXPONENT) * _V_BAND
    ac = np.fft.irfft(mag, _V_FFT)
    if ac[0] <= 0:
        return 0.0
    return float(min(1.0, np.max(ac[_V_LAGS] / ac[0] / _V_WIN_AC)))


class NoiseFloor:
    """Causal noise-floor tracker: low percentile of the band levels of recent aperiodic frames."""

    def __init__(self):
        self.levels = deque(maxlen=FLOOR_WINDOW_MS // FRAME_MS)

    def reset(self):
        self.levels.clear()

    @property
    def ready(self) -> bool:
        return len(self.levels) >= FLOOR_MIN_FRAMES

    def value(self) -> float:
        return float(np.percentile(np.fromiter(self.levels, float), FLOOR_PERCENTILE))

    def add(self, level_db: float):
        self.levels.append(level_db)


class MySystem(System):
    def __init__(self):
        self.floor = NoiseFloor()
        self.speech = False             # caller speech-frame state (with hysteresis)
        self.voiced = False             # current caller frame is periodic
        self.prev_caller = np.zeros(FRAME_SAMPLES, dtype=np.int16)   # previous frame, for the 40 ms analysis
        self.agent_talking = False
        self.agent_text = ""            # what the agent is saying right now (not used by this experiment)
        self.stopped = False
        self.evidence = deque(maxlen=EVIDENCE_WINDOW_MS // FRAME_MS)   # (speech, voiced) per agent-talking frame
        self.quiet_ms = 0               # TAKE_TURN path (baseline)
        self.caller_spoke = False

    # --- what is happening in the call -------------------------------------------------------------------
    def on_scenario_start(self, context):
        # A new call: forget the previous call's noise and any agent state left over from it.
        self.floor.reset()
        self.speech = self.voiced = False
        self.prev_caller = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        self.agent_talking, self.stopped = False, False
        self.evidence.clear()
        self.caller_spoke, self.quiet_ms = False, 0

    def on_agent_start(self, text, duration_ms):
        self.agent_talking, self.agent_text, self.stopped = True, text, False
        self.evidence.clear()

    def on_agent_end(self):
        self.agent_talking, self.caller_spoke, self.quiet_ms = False, False, 0

    # --- caller speech evidence ---------------------------------------------------------------------------
    def is_speech(self, caller) -> bool:
        """Update the speech-frame state from one caller frame. The floor is computed from earlier frames only."""
        level = band_level_db(caller)
        self.voiced = periodicity(np.concatenate([self.prev_caller, caller])) >= VOICING_THRESHOLD
        self.prev_caller = caller
        loud_enough = loudness_db(caller) > LOUD_DB
        if self.floor.ready:
            threshold = SPEECH_OFF_DB if self.speech else SPEECH_ON_DB
            self.speech = loud_enough and level - self.floor.value() >= threshold
        else:                           # no trusted noise estimate yet: baseline rule, periodic frames only
            self.speech = loud_enough and self.voiced
        if not self.voiced:
            self.floor.add(level)
        return self.speech

    # --- every 20 ms: decide ---------------------------------------------------------------------------
    def on_frame(self, agent, caller):
        speech = self.is_speech(caller)

        if self.agent_talking:
            self.evidence.append((speech, speech and self.voiced))
            speech_ms = sum(s for s, _ in self.evidence) * FRAME_MS
            voiced_ms = sum(v for _, v in self.evidence) * FRAME_MS
            if speech_ms >= STOP_AFTER_MS and voiced_ms >= VOICED_EVIDENCE_MS and not self.stopped:
                self.stopped = True
                return "STOP"
            return None

        # End-of-turn track: the kit baseline, unchanged (fixed loudness and a fixed stretch of quiet).
        loud = loudness_db(caller) > LOUD_DB
        if loud:
            self.caller_spoke, self.quiet_ms = True, 0
        elif self.caller_spoke:
            self.quiet_ms += FRAME_MS
            if self.quiet_ms >= TAKE_TURN_AFTER_MS:
                self.caller_spoke = False
                return "TAKE_TURN"
        return None


if __name__ == "__main__":
    run(MySystem, TEAM, __file__)
