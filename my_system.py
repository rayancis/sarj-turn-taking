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

# --- Phase 3 options. With these defaults the detector is E01b, decision for decision. ---------------------
ABS_GATE_DB = LOUD_DB       # a speech frame must be louder than this absolute level; None = no absolute gate
VAD = None                  # None, "silero" or "ten": pretrained neural VAD (neural_vad.py)
VAD_MODE = "replace"        # "replace": a frame is speech when the VAD says so (no level/periodicity test);
                            # "confirm": E01b speech frames, and a STOP also needs VAD_CONFIRM_MS of VAD speech
VAD_THRESHOLD = 0.5
VAD_CONFIRM_MS = 100
ECHO_GATE = False           # ignore caller frames that are explained by the agent's own delayed voice
ECHO_MAX_LAG_MS = 500       # longest echo delay searched
ECHO_HISTORY = 50           # agent-active frames kept per lag (1 s)
ECHO_MIN_FRAMES = 15        # ... of which this many are needed before a lag can be trusted
ECHO_SPREAD_DB = 3.0        # caller level follows the delayed agent level within this robust spread = echo
ECHO_MARGIN_DB = 6.0        # a frame this far above the predicted echo is not explained by it
AGENT_ACTIVE_DB = -50.0     # agent frames above this band level can produce an echo
CALLER_REF = False          # judge loudness against the caller's own earlier speech (gain-invariant)
REF_WINDOW_MS = 15000       # caller-turn speech frames remembered for the reference
REF_MIN_FRAMES = 10         # ... needed before the reference is trusted
REF_PERCENTILE = 90
SOFT_DB = 6.0               # evidence more than this below the caller's reference is "soft": not a fast STOP
SOFT_STOP_MS = 1000         # ... soft speech must instead last this long ...
SOFT_WINDOW_MS = 1400       # ... within this window
BACKGROUND_DB = None        # evidence more than this below the reference never stops (None = no such tier)
REL_GATE_DB = None          # once the reference is trusted, a speech frame must be within this many dB of it; this
                            # replaces the absolute gate, so the behaviour no longer depends on microphone gain

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


class EchoTracker:
    """Is the caller frame just the agent's own voice coming back (speakerphone, line echo)?

    An echo's level follows the agent's level a fixed delay earlier, minus a fixed loss. For every lag up to
    ECHO_MAX_LAG_MS it keeps the last ECHO_HISTORY differences (caller level - agent level at that lag), taken while
    the agent frame was active. If the differences at the best lag stay within a small robust spread (median
    absolute deviation), an echo path exists with that delay and loss; a caller frame is then "explained" when it
    is no louder than the predicted echo plus a margin. A caller talking over the echo is louder than the
    prediction, so it is not explained. Only levels are compared, so the room's response and phase do not matter.
    """

    def __init__(self):
        self.lags = ECHO_MAX_LAG_MS // FRAME_MS + 1
        self.reset()

    def reset(self):
        self.agent = deque([MIN_LEVEL_DB] * (self.lags + 1), maxlen=self.lags + 1)   # newest last
        self.diff = np.full((self.lags, ECHO_HISTORY), np.nan)
        self.pos = np.zeros(self.lags, dtype=int)
        self.count = np.zeros(self.lags, dtype=int)
        self.lag, self.loss = None, 0.0

    def explained(self, agent_level: float, caller_level: float) -> bool:
        self.agent.append(agent_level)
        past = np.fromiter(self.agent, float)[::-1][:self.lags]       # past[d] = agent level d frames ago
        active = past > AGENT_ACTIVE_DB
        idx = np.nonzero(active)[0]
        self.diff[idx, self.pos[idx]] = caller_level - past[idx]
        self.pos[idx] = (self.pos[idx] + 1) % ECHO_HISTORY
        self.count[idx] += 1
        ready = self.count >= ECHO_MIN_FRAMES
        self.lag = None
        if ready.any():
            med = np.nanmedian(self.diff[ready], axis=1)
            spread = 1.4826 * np.nanmedian(np.abs(self.diff[ready] - med[:, None]), axis=1)
            best = int(np.argmin(spread))
            if spread[best] <= ECHO_SPREAD_DB:
                self.lag, self.loss = int(np.nonzero(ready)[0][best]), float(med[best])
        if self.lag is None:
            return False
        near = past[max(0, self.lag - 1):self.lag + 2]                 # the room smears the echo over time
        return bool(near.max() > AGENT_ACTIVE_DB and caller_level <= near.max() + self.loss + ECHO_MARGIN_DB)


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
        self.floor = NoiseFloor()
        self.vad = None
        if VAD:
            import neural_vad
            self.vad = neural_vad.load(VAD)
        self.vad_speech = False         # current frame: the neural VAD says speech
        self.echo = EchoTracker() if ECHO_GATE else None
        self.ref = CallerReference()
        self.level = MIN_LEVEL_DB       # current caller frame band level
        self.speech = False             # caller speech-frame state (with hysteresis)
        self.voiced = False             # current caller frame is periodic
        self.prev_caller = np.zeros(FRAME_SAMPLES, dtype=np.int16)   # previous frame, for the 40 ms analysis
        self.agent_talking = False
        self.agent_text = ""            # what the agent is saying right now (not used by this experiment)
        self.stopped = False
        window_ms = max(EVIDENCE_WINDOW_MS, SOFT_WINDOW_MS) if CALLER_REF else EVIDENCE_WINDOW_MS
        self.evidence = deque(maxlen=window_ms // FRAME_MS)   # (speech, voiced, vad, level) per agent-talking frame
        self.quiet_ms = 0               # TAKE_TURN path (baseline)
        self.caller_spoke = False

    # --- what is happening in the call -------------------------------------------------------------------
    def on_scenario_start(self, context):
        # A new call: forget the previous call's noise and any agent state left over from it.
        self.floor.reset()
        self.ref.reset()
        if self.vad:
            self.vad.reset()
        if self.echo:
            self.echo.reset()
        self.speech = self.voiced = self.vad_speech = False
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
    def is_speech(self, caller, agent=None) -> bool:
        """Update the speech-frame state from one caller frame. The floor is computed from earlier frames only."""
        level = self.level = band_level_db(caller)
        if CALLER_REF and REL_GATE_DB is not None and self.ref.ready:
            loud_enough = level >= self.ref.value() - REL_GATE_DB
        else:
            loud_enough = ABS_GATE_DB is None or loudness_db(caller) > ABS_GATE_DB
        echo = bool(self.echo) and self.echo.explained(band_level_db(agent), level)
        if self.vad:
            self.vad_speech = self.vad(caller) >= VAD_THRESHOLD
            if VAD_MODE == "replace":                 # the VAD decides; "voiced" evidence is the VAD itself
                self.speech = self.voiced = self.vad_speech and loud_enough and not echo
                return self.speech
        self.voiced = periodicity(np.concatenate([self.prev_caller, caller])) >= VOICING_THRESHOLD
        self.prev_caller = caller
        if self.floor.ready:
            threshold = SPEECH_OFF_DB if self.speech else SPEECH_ON_DB
            self.speech = loud_enough and level - self.floor.value() >= threshold
        else:                           # no trusted noise estimate yet: baseline rule, periodic frames only
            self.speech = loud_enough and self.voiced
        if not self.voiced:
            self.floor.add(level)
        if echo:
            self.speech = False
        return self.speech

    def should_stop(self) -> bool:
        """STOP rule on the evidence window (frames heard while the agent talks)."""
        frames = list(self.evidence)
        recent = frames[-(EVIDENCE_WINDOW_MS // FRAME_MS):]

        def enough(fr, need_ms):
            ok = sum(f[0] for f in fr) * FRAME_MS >= need_ms and sum(f[1] for f in fr) * FRAME_MS >= VOICED_EVIDENCE_MS
            if ok and self.vad and VAD_MODE == "confirm":
                ok = sum(f[2] for f in fr) * FRAME_MS >= VAD_CONFIRM_MS
            return ok

        fast = enough(recent, STOP_AFTER_MS)
        if not (CALLER_REF and self.ref.ready):
            return fast
        ref = self.ref.value()

        def rel(fr):                    # how loud the speech in these frames is, relative to the caller's own level
            return float(np.percentile([f[3] for f in fr if f[0]], REF_PERCENTILE)) - ref

        if fast and rel(recent) >= -SOFT_DB:
            return True                 # as loud as the caller normally speaks: stop at once
        if enough(frames, SOFT_STOP_MS):
            return BACKGROUND_DB is None or rel(frames) >= -BACKGROUND_DB
        return False

    # --- every 20 ms: decide ---------------------------------------------------------------------------
    def on_frame(self, agent, caller):
        speech = self.is_speech(caller, agent)

        if self.agent_talking:
            self.evidence.append((speech, speech and self.voiced, self.vad_speech, self.level))
            if not self.stopped and self.should_stop():
                self.stopped = True
                return "STOP"
            return None
        if CALLER_REF and speech and self.voiced:
            self.ref.add(self.level)                # the caller's own turn: remember how loud they speak

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
    MySystem()                  # load any model before the server accepts a connection
    run(MySystem, TEAM, __file__)
