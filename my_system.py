"""Turn-taking system, Phase 3 candidate: neural speech frames + caller-relative loudness for STOP.

    python -m ttbench.play --calls all             # official real-time run on all 24 dev calls
    python tools/replay.py --system my_system.py   # fast offline replay (diagnostic only)

What changed from E01b, and why (experiments/P3-phase3-report.md has the measurements):
  E01b decides "speech" from level above a noise floor plus periodicity. Its remaining false stops were sounds
  that are loud and periodic but are not the caller taking the floor: non-speech (horn, cough, laughter, noise
  onsets), other people (TV, a voice across the room, the agent's own echo) and short soft acknowledgements.

STOP rule, per 20 ms caller frame (only audio already received is used; no labels, ids or transcripts):
  1. speech frame: a pretrained neural VAD (Silero VAD v6, MIT, 2.3 MB ONNX, CPU) says speech (probability >= 0.5).
     It replaces E01b's level + periodicity test: it rejects non-speech and reverberant far-field sound, and it
     bridges stop closures inside words.
  2. caller reference: the 90th percentile of the 300-3400 Hz band level of the speech frames heard while the
     agent was silent (the caller's own turns, last 15 s). It is trusted after 10 such frames (200 ms).
  3. loudness is judged against that reference, not against a fixed level, so microphone gain does not matter:
       - a speech frame must be within 20 dB of the reference (before the caller has spoken: the kit baseline's
         absolute -42 dBFS);
       - the audio given to the VAD is scaled so that the reference sits at -22 dB (limited to -12..+24 dB), so a
         quiet line looks like a normal one to the model.
  4. evidence, counted over agent-talking frames only:
       fast  STOP when the last 400 ms hold >= 200 ms of speech frames whose level (90th percentile) is no more
             than 6 dB below the reference: the caller is speaking as loudly as they normally do.
       soft  quieter speech (an acknowledgement, someone else in the room, an echo) must instead fill 1000 ms of
             the last 1400 ms. A short "إيه" never does; a soft but sustained interruption still stops the agent,
             about a second later.
     Until the reference is trusted only the fast rule applies (with the absolute gate). One STOP per agent
     utterance. No RESUME.
Echo (AEC = True): before step 1 the agent's own voice is subtracted from the caller channel by an adaptive echo
  canceller driven by the agent channel (echo_canceller.py). The STOP rule then sees the caller minus the echo.
  The end-of-turn path still reads the raw caller channel.
State: the reference, the echo canceller and the VAD's recurrent state are cleared at each scenario_start; the evidence window and the
stop flag at each agent_utterance_start.

VAD = None gives E01b's acoustic front end (noise floor + periodicity) under the same evidence rules; with
CALLER_REF = False and EVIDENCE_WINDOW_MS = 300 as well it is E01b decision for decision (tests/test_my_system.py).

The end-of-turn path (TAKE_TURN, a separate leaderboard) is the kit baseline, unchanged.
"""
from collections import deque

import numpy as np

from ttbench.server import System, run

TEAM = "my-team"            # placeholder until the official team name is known

SAMPLE_RATE = 16000
FRAME_MS = 20               # each on_frame call covers 20 ms of audio
FRAME_SAMPLES = 320
LOUD_DB = -42               # baseline absolute level: the TAKE_TURN rule, and the STOP gate before a reference exists
STOP_AFTER_MS = 200         # speech for this long while the agent talks -> STOP (as in the baseline) ...
EVIDENCE_WINDOW_MS = 400    # ... counted within this many most recent ms of agent speech
VOICED_EVIDENCE_MS = 40     # acoustic front end only: this much of the evidence must be periodic
TAKE_TURN_AFTER_MS = 700    # quiet for this long after the caller spoke -> TAKE_TURN (baseline)

VAD = "silero"              # "silero": neural speech frames (neural_vad.py); None: E01b's acoustic front end
VAD_THRESHOLD = 0.5         # speech probability at or above this = speech frame
VAD_NORMALIZE_DB = -22.0    # the VAD's input is scaled so the caller's reference sits at this band level; None = off
VAD_NORMALIZE_RANGE_DB = (-12.0, 24.0)   # limits of that scaling

AEC = True                  # subtract the agent's own echo from the caller channel first (echo_canceller.py)
CALLER_REF = True           # judge loudness against the caller's own earlier speech (gain-invariant)
REF_WINDOW_MS = 15000       # caller-turn speech frames remembered for the reference
REF_MIN_FRAMES = 10         # ... needed before the reference is trusted
REF_PERCENTILE = 90
REL_GATE_DB = 20.0          # once the reference is trusted, a speech frame must be within this many dB of it
ABS_GATE_DB = LOUD_DB       # until then (and when REL_GATE_DB is None) it must be louder than this absolute level
SOFT_DB = 6.0               # evidence more than this below the reference is "soft": not a fast STOP
SOFT_STOP_MS = 1000         # ... soft speech must instead last this long ...
SOFT_WINDOW_MS = 1400       # ... within this window

BAND_HZ = (300, 3400)       # telephone speech band
MIN_LEVEL_DB = -120.0       # clamp for digital silence (log of zero)

# Acoustic front end (VAD = None), as in E01b
FLOOR_WINDOW_MS = 3000      # noise-floor memory
FLOOR_PERCENTILE = 10       # low percentile of recent band levels = noise floor
FLOOR_MIN_FRAMES = 5        # need 100 ms of history before any frame can count as speech
SPEECH_ON_DB = 10.0         # band level this far above the floor starts a speech run
SPEECH_OFF_DB = 6.0         # ... and this far above keeps it going (hysteresis)
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


class CallerReference:
    """The caller's own speaking level: a high percentile of their speech-frame levels while the agent is silent."""

    def __init__(self):
        self.levels = deque(maxlen=REF_WINDOW_MS // FRAME_MS)
        self._value = None

    def reset(self):
        self.levels.clear()
        self._value = None

    def add(self, level_db: float):
        self.levels.append(level_db)
        self._value = None

    @property
    def ready(self) -> bool:
        return len(self.levels) >= REF_MIN_FRAMES

    def value(self) -> float:
        if self._value is None:             # only changes while the caller has the floor
            self._value = float(np.percentile(np.fromiter(self.levels, float), REF_PERCENTILE))
        return self._value


class MySystem(System):
    def __init__(self):
        self.vad = None
        if VAD:
            import neural_vad
            self.vad = neural_vad.load(VAD)     # the model file is read once per process (see __main__)
        self.aec = None
        if AEC:
            import echo_canceller
            self.aec = echo_canceller.EchoCanceller()
        self.floor = NoiseFloor()               # acoustic front end only
        self.ref = CallerReference()
        self.level = MIN_LEVEL_DB       # band level of the current caller frame
        self.candidate = False          # the current frame is speech, before any loudness gate
        self.speech = False             # ... and loud enough to count as evidence
        self.voiced = False             # acoustic front end: the current frame is periodic
        self.prev_caller = np.zeros(FRAME_SAMPLES, dtype=np.int16)   # previous frame, for the 40 ms analysis
        self.agent_talking = False
        self.agent_text = ""            # what the agent is saying right now (not used)
        self.stopped = False
        window_ms = max(EVIDENCE_WINDOW_MS, SOFT_WINDOW_MS) if CALLER_REF else EVIDENCE_WINDOW_MS
        self.evidence = deque(maxlen=window_ms // FRAME_MS)   # (speech, voiced, level) per agent-talking frame
        self.quiet_ms = 0               # TAKE_TURN path (baseline)
        self.caller_spoke = False

    # --- what is happening in the call -------------------------------------------------------------------
    def on_scenario_start(self, context):
        # A new call: a new caller, microphone and room. Forget the previous call entirely.
        self.floor.reset()
        self.ref.reset()
        if self.vad:
            self.vad.reset()
        if self.aec:
            self.aec.reset()
        self.candidate = self.speech = self.voiced = False
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
        """Update the speech-frame state from one caller frame, using only this and earlier frames."""
        level = self.level = band_level_db(caller)
        trusted = CALLER_REF and self.ref.ready
        if trusted and REL_GATE_DB is not None:
            loud_enough = level >= self.ref.value() - REL_GATE_DB
        else:
            loud_enough = ABS_GATE_DB is None or loudness_db(caller) > ABS_GATE_DB

        if self.vad:
            x = caller
            if trusted and VAD_NORMALIZE_DB is not None:        # a quiet line looks like a normal one to the model
                gain_db = float(np.clip(VAD_NORMALIZE_DB - self.ref.value(), *VAD_NORMALIZE_RANGE_DB))
                x = np.clip(caller.astype(np.float64) * 10 ** (gain_db / 20), -32768, 32767)
            self.candidate = self.vad(x) >= VAD_THRESHOLD
            self.speech = self.voiced = self.candidate and loud_enough
            return self.speech

        # Acoustic front end (E01b): level above a noise floor learnt from aperiodic frames, plus periodicity.
        self.voiced = periodicity(np.concatenate([self.prev_caller, caller])) >= VOICING_THRESHOLD
        self.prev_caller = caller
        if self.floor.ready:
            above = level - self.floor.value()
            self.candidate = self.voiced and above >= SPEECH_ON_DB
            self.speech = loud_enough and above >= (SPEECH_OFF_DB if self.speech else SPEECH_ON_DB)
        else:                           # no trusted noise estimate yet: baseline rule, periodic frames only
            self.candidate = False
            self.speech = loud_enough and self.voiced
        if not self.voiced:
            self.floor.add(level)
        return self.speech

    def should_stop(self) -> bool:
        """The STOP rule on the evidence window (frames heard while the agent talks)."""
        frames = list(self.evidence)
        recent = frames[-(EVIDENCE_WINDOW_MS // FRAME_MS):]

        def enough(fr, need_ms):
            return (sum(f[0] for f in fr) * FRAME_MS >= need_ms
                    and sum(f[1] for f in fr) * FRAME_MS >= VOICED_EVIDENCE_MS)

        fast = enough(recent, STOP_AFTER_MS)
        if not (CALLER_REF and self.ref.ready):
            return fast
        if fast:                        # how loud is this speech, relative to the caller's own level?
            loud = float(np.percentile([f[2] for f in recent if f[0]], REF_PERCENTILE))
            if loud >= self.ref.value() - SOFT_DB:
                return True             # as loud as the caller normally speaks: stop at once
        return enough(frames, SOFT_STOP_MS)     # softer: only if it goes on

    # --- every 20 ms: decide ---------------------------------------------------------------------------
    def on_frame(self, agent, caller):
        heard = self.aec.process(agent, caller) if self.aec else caller   # caller minus the agent's echo
        speech = self.is_speech(heard)

        if self.agent_talking:
            self.evidence.append((speech, speech and self.voiced, self.level))
            if not self.stopped and self.should_stop():
                self.stopped = True
                return "STOP"
            return None
        if CALLER_REF and self.candidate:
            self.ref.add(self.level)    # the caller's own turn: remember how loud they speak

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
    MySystem()                  # load the model before the server accepts a connection
    run(MySystem, TEAM, __file__)
