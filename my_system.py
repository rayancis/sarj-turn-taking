"""Turn-taking system, experiment 1: adaptive noise floor + relative speech evidence for STOP.

    python -m ttbench.play --calls all         # official real-time run on all 24 dev calls
    python tools/replay.py --system my_system.py   # fast offline replay (diagnostic only)

Hypothesis. The kit's loudness baseline treats any caller-channel energy above a fixed -42 dBFS as speech, so steady
background noise above that level (car, street) makes the agent stop about 200 ms into every utterance. Judging
caller sound relative to an estimate of the noise floor, instead of only against a fixed level, should remove
those stops without making the system slower to react to a real caller.

STOP rule, per 20 ms caller frame (only audio already received is used; no labels, ids or transcripts):
  1. band level: energy in 300-3400 Hz (the telephone speech band; also ignores low rumble and hum), in dB.
  2. noise floor: the 10th percentile of the band levels of the previous 3 s of this call. A low percentile of a
     few seconds follows steady noise but is not pulled up by speech, which has gaps and quiet frames.
  3. speech frame, with hysteresis: becomes active when the band level is >= 10 dB above the floor, stays active
     while it is >= 6 dB above it. Every speech frame must also be louder than the baseline's absolute -42 dBFS,
     so in quiet rooms the system is never more sensitive than the baseline was.
  4. speech run: speech frames are counted while the agent talks; a dip of up to 100 ms (a gap between syllables
     or a consonant, which in noise falls close to the floor) does not end the run, a longer one does. Only speech
     frames count, so dips never add evidence.
  5. STOP once a run holds 200 ms of speech frames (the baseline's amount of evidence); one STOP per agent
     utterance. No RESUME.
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
STOP_AFTER_MS = 200         # speech for this long while the agent talks -> STOP (as in the baseline)
TAKE_TURN_AFTER_MS = 700    # quiet for this long after the caller spoke -> TAKE_TURN (baseline)

BAND_HZ = (300, 3400)       # telephone speech band
FLOOR_WINDOW_MS = 3000      # noise-floor memory
FLOOR_PERCENTILE = 10       # low percentile of recent band levels = noise floor
FLOOR_MIN_FRAMES = 5        # need 100 ms of history before any frame can count as speech
SPEECH_ON_DB = 10.0         # band level this far above the floor starts a speech run
SPEECH_OFF_DB = 6.0         # ... and this far above keeps it going (hysteresis)
MAX_GAP_MS = 100            # a non-speech dip this short does not end a speech run (syllable gaps)
MIN_LEVEL_DB = -120.0       # clamp for digital silence (log of zero)

_WINDOW = np.hanning(FRAME_SAMPLES)
_FREQS = np.fft.rfftfreq(FRAME_SAMPLES, 1 / SAMPLE_RATE)
_BAND = (_FREQS >= BAND_HZ[0]) & (_FREQS <= BAND_HZ[1])
_NORM = FRAME_SAMPLES * np.sum(_WINDOW ** 2) / 2      # a full-scale sine in the band reads about -3 dB


def loudness_db(samples: np.ndarray) -> float:
    """Loudness of 20 ms of 16-bit audio, in dB below full scale (silence is about -90, speech -40 to -15)."""
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2)) / 32768
    return 20 * np.log10(rms + 1e-12)


def band_level_db(samples: np.ndarray) -> float:
    """Energy of one frame inside BAND_HZ, in dB relative to full scale."""
    x = samples.astype(np.float64) / 32768 * _WINDOW
    power = np.abs(np.fft.rfft(x)) ** 2
    return max(MIN_LEVEL_DB, 10 * np.log10(power[_BAND].sum() / _NORM + 1e-30))


class NoiseFloor:
    """Causal noise-floor tracker: low percentile of the band levels of the last FLOOR_WINDOW_MS."""

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
        self.agent_talking = False
        self.agent_text = ""            # what the agent is saying right now (not used by this experiment)
        self.stopped = False
        self.speech_ms = 0              # caller speech in the current run while the agent talks
        self.gap_ms = 0                 # current non-speech dip inside that run
        self.quiet_ms = 0               # TAKE_TURN path (baseline)
        self.caller_spoke = False

    # --- what is happening in the call -------------------------------------------------------------------
    def on_scenario_start(self, context):
        # A new call: forget the previous call's noise and any agent state left over from it.
        self.floor.reset()
        self.speech = False
        self.agent_talking, self.stopped, self.speech_ms, self.gap_ms = False, False, 0, 0
        self.caller_spoke, self.quiet_ms = False, 0

    def on_agent_start(self, text, duration_ms):
        self.agent_talking, self.agent_text, self.stopped = True, text, False
        self.speech_ms, self.gap_ms = 0, 0

    def on_agent_end(self):
        self.agent_talking, self.caller_spoke, self.quiet_ms = False, False, 0

    # --- caller speech evidence ---------------------------------------------------------------------------
    def is_speech(self, caller) -> bool:
        """Update the speech-frame state from one caller frame. The floor is computed from earlier frames only."""
        level = band_level_db(caller)
        if self.floor.ready:
            above = level - self.floor.value()
            loud_enough = loudness_db(caller) > LOUD_DB
            threshold = SPEECH_OFF_DB if self.speech else SPEECH_ON_DB
            self.speech = loud_enough and above >= threshold
        else:
            self.speech = False
        self.floor.add(level)
        return self.speech

    # --- every 20 ms: decide ---------------------------------------------------------------------------
    def on_frame(self, agent, caller):
        speech = self.is_speech(caller)

        if self.agent_talking:
            if speech:
                self.speech_ms, self.gap_ms = self.speech_ms + FRAME_MS, 0
            else:
                self.gap_ms += FRAME_MS
                if self.gap_ms > MAX_GAP_MS:
                    self.speech_ms = 0
            if self.speech_ms >= STOP_AFTER_MS and not self.stopped:
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
