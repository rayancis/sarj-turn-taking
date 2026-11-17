"""Echo handling: remove the agent's own voice from the caller channel, but only when there is an echo.

Why: on a speakerphone (or any line with echo) the caller channel carries a delayed, room-filtered copy of the
agent. A neural VAD correctly calls that "speech", so the detector stops the agent for its own voice. The harness
gives us the agent's audio, the very signal that produced the echo, so the echo can be predicted and removed.

Two parts:
1. Echo-path detector (`LinearEcho`): a partitioned-block frequency-domain NLMS filter (the multi-delay filter of
   Speex/WebRTC), driven by the agent channel. It is purely linear, so it can only "explain" caller sound that is a
   filtered copy of the agent; it cannot learn to remove a caller who talks over the agent. When its residual stays
   clearly below the raw caller signal while the agent talks (ECHO_ON_DB), an echo path exists.
2. Canceller (`EchoCanceller`): WebRTC AEC3 (via the `livekit` package, 1.3.0, Apache-2.0; WebRTC BSD-3-Clause),
   run on every frame so it stays converged, but its output is used ONLY while the detector reports an echo path.

Why the gate (measured, see experiments/E02-echo.md): AEC3 removes about 20 dB of echo from the first second, but its
residual-echo suppressor also attenuates a genuine interruption by 4-16 dB on calls that have no echo at all, which
cost 6 missed interruptions on the human-voice xdev set. The linear filter alone converges too slowly (about 3 dB
of reduction during the first agent utterance) to be the canceller, but it is a safe detector.

Causal: every output uses the current and earlier frames only. reset() starts a new call.
Limits: until the detector has seen enough agent speech (about 1 s) an echo is not removed; nonlinear loudspeaker
echo is only partly modelled by the linear detector.
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
FRAME = 320                 # 20 ms
HALF = 160                  # AEC3 works on 10 ms frames

PARTITIONS = 32             # linear detector covers 32 x 20 ms = 640 ms of echo path (delay + room tail)
STEP = 0.6                  # NLMS step (normalised by the agent power summed over partitions)
FAR_ACTIVE_DB = -55.0       # agent frames below this level (dBFS) are not used
ERLE_SMOOTH = 0.95          # smoothing of the echo-reduction estimate (about 20 agent-active frames)
ECHO_ON_DB = 6.0            # the linear filter removes at least this much of the caller signal -> echo path
DIVERGED = 2.0              # residual above twice the raw caller energy = the filter diverged; it is cleared
ECHO_OFF_DB = 3.0           # ... and the echo path is dropped again only after the reduction stays below this ...
ECHO_HOLD_FRAMES = 150      # ... for this many agent-active frames (3 s): a caller talking over the echo lowers the
                            # measured reduction for a moment, which must not switch the canceller off mid-sentence


def _energy(x: np.ndarray) -> float:
    return float(np.dot(x, x))


def _db(x: np.ndarray) -> float:
    return 10 * np.log10(_energy(x) / len(x) / 32768 ** 2 + 1e-12)


class LinearEcho:
    """Linear echo-path model; reports whether the caller channel is (partly) a filtered copy of the agent."""

    def __init__(self, partitions: int = PARTITIONS):
        self.k = partitions
        self.reset()

    def reset(self):
        n = FRAME + 1
        self.X = np.zeros((self.k, n), dtype=np.complex128)        # agent spectra, newest first
        self.W = np.zeros((self.k, n), dtype=np.complex128)
        self.prev_far = np.zeros(FRAME)
        self.power = np.zeros(n)
        self.near_e = self.res_e = 1e-6
        self.frames = 0
        self.echo = False
        self.low = 0                # consecutive agent-active frames below ECHO_OFF_DB while the echo is on

    def process(self, far: np.ndarray, near: np.ndarray) -> bool:
        far = np.asarray(far, dtype=np.float64)
        near = np.asarray(near, dtype=np.float64)
        self.X = np.roll(self.X, 1, axis=0)
        self.X[0] = np.fft.rfft(np.concatenate([self.prev_far, far]))
        self.prev_far = far
        if _db(far) <= FAR_ACTIVE_DB:
            return self.echo
        res = near - np.fft.irfft(np.sum(self.W * self.X, axis=0))[FRAME:]
        self.power = 0.8 * self.power + 0.2 * np.sum(np.abs(self.X) ** 2, axis=0)
        reg = 1e-3 * float(np.mean(self.power)) + 1e3
        E = np.fft.rfft(np.concatenate([np.zeros(FRAME), res]))
        self.W += STEP * np.conj(self.X) * (E / (self.power + reg))
        j = self.frames % self.k                                    # gradient constraint, one partition per frame
        w = np.fft.irfft(self.W[j])
        w[FRAME:] = 0.0
        self.W[j] = np.fft.rfft(w)
        self.frames += 1
        a = ERLE_SMOOTH
        self.near_e = a * self.near_e + (1 - a) * _energy(near)
        self.res_e = a * self.res_e + (1 - a) * _energy(res)
        if not np.all(np.isfinite(self.W)) or self.res_e > DIVERGED * self.near_e:
            self.W[:] = 0.0                                         # diverged (worse than no filter): start again
            self.res_e = self.near_e
        erle = 10 * np.log10(self.near_e / max(self.res_e, 1e-6))
        if not self.echo:
            self.echo = erle >= ECHO_ON_DB
            self.low = 0
        else:
            self.low = self.low + 1 if erle < ECHO_OFF_DB else 0
            self.echo = self.low < ECHO_HOLD_FRAMES
        return self.echo


class EchoCanceller:
    name = "WebRTC AEC3 via livekit 1.3.0, gated by a linear echo-path detector"
    licence = "Apache-2.0 (livekit), BSD-3-Clause (WebRTC)"

    def __init__(self):
        from livekit import rtc     # imported here so a missing package fails at start-up, not mid-call
        self._rtc = rtc
        self.detector = LinearEcho()
        self.reset()

    def reset(self):
        self.apm = self._rtc.AudioProcessingModule(echo_cancellation=True)
        self.detector.reset()
        self.active = False         # was the cancelled signal used for the last frame?

    def _frame(self, samples: np.ndarray):
        f = self._rtc.AudioFrame.create(SAMPLE_RATE, 1, HALF)
        np.frombuffer(f.data, dtype=np.int16)[:] = np.clip(np.asarray(samples), -32768, 32767).astype(np.int16)
        return f

    def process(self, far: np.ndarray, near: np.ndarray) -> np.ndarray:
        """One 20 ms frame: agent samples, caller samples (int16) -> caller with the echo removed when there is one."""
        out = np.empty(len(near), dtype=np.int16)
        for i in range(0, len(near), HALF):
            self.apm.process_reverse_stream(self._frame(far[i:i + HALF]))
            cap = self._frame(near[i:i + HALF])
            self.apm.set_stream_delay_ms(0)     # the harness delivers both channels in step
            self.apm.process_stream(cap)        # in place
            out[i:i + HALF] = np.frombuffer(cap.data, dtype=np.int16)
        self.active = self.detector.process(far, near)
        return out if self.active else np.asarray(near)
