"""Per-frame audio frontend: echo suppression, noise floor, speech evidence.

Everything works on telephone-band powers (100-3400 Hz) so that wideband dev
audio and narrowband phone audio in the hidden set look the same to the
decision logic. Cost is one small rFFT per channel per frame (<0.2 ms).
"""
from __future__ import annotations

import collections
import math
import os

import numpy as np

from .config import Config

BAND_EDGES_HZ = [100, 300, 500, 700, 900, 1150, 1400, 1700, 2000, 2400, 2800, 3400]
EPS = 1e-12
FLOOR = 1e-10  # ~ -100 dBFS per band


def to_float(x) -> np.ndarray:
    a = np.asarray(x)
    if a.dtype.kind in "iu":
        return a.astype(np.float32) / 32768.0
    return a.astype(np.float32, copy=False)


class FrameFeatures:
    __slots__ = ("caller_db", "agent_db", "excess_db", "snr_db", "voicing",
                 "vad_prob", "echo_frac", "is_speech", "flux")

    def __repr__(self):
        return ("FF(snr=%.1f exc=%.1f ag=%.1f voi=%.2f vad=%s echo=%.2f sp=%s)" %
                (self.snr_db, self.excess_db, self.agent_db, self.voicing,
                 "-" if self.vad_prob is None else "%.2f" % self.vad_prob,
                 self.echo_frac, self.is_speech))


class _SileroVAD:
    """Optional Silero VAD (v5 ONNX). Disabled silently on any problem."""

    def __init__(self, path: str):
        import onnxruntime as ort  # noqa: local import, optional
        so = ort.SessionOptions()
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = 1
        self.sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        self.reset()
        for _ in range(3):  # warm-up: first inference is slow, keep it out of on_frame
            self.push(np.zeros(512, dtype=np.float32))
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.ctx = np.zeros(64, dtype=np.float32)
        self.buf = np.zeros(0, dtype=np.float32)
        self.prob = 0.0

    def push(self, x16k: np.ndarray) -> float:
        self.buf = np.concatenate([self.buf, x16k])
        while len(self.buf) >= 512:
            chunk, self.buf = self.buf[:512], self.buf[512:]
            inp = np.concatenate([self.ctx, chunk])[None, :].astype(np.float32)
            out, self.state = self.sess.run(
                None, {"input": inp, "state": self.state, "sr": np.array(16000, dtype=np.int64)})
            self.ctx = chunk[-64:]
            self.prob = float(out.reshape(-1)[0])
        return self.prob


class Frontend:
    def __init__(self, cfg: Config, model_dir: str | None = None):
        self.cfg = cfg
        self.sr = None
        self.n = None
        self.vad = None
        self._vad_tried = False
        self.model_dir = model_dir or os.path.join(os.path.dirname(__file__), "..", "models")
        self._load_vad()
        self.reset_call()

    def _load_vad(self):
        if self._vad_tried or not self.cfg.use_silero:
            return
        self._vad_tried = True
        p = os.path.join(self.model_dir, "silero_vad.onnx")
        if os.path.exists(p):
            try:
                self.vad = _SileroVAD(p)
            except Exception:
                self.vad = None

    # ------------------------------------------------------------------ setup
    def _setup(self, n: int):
        self.n = n
        self.sr = int(round(n / (self.cfg.frame_ms / 1000.0)))
        self.win = np.hanning(n).astype(np.float32)
        freqs = np.fft.rfftfreq(n, 1.0 / self.sr)
        edges = [e for e in BAND_EDGES_HZ if e < self.sr / 2] or [self.sr / 2]
        self.band_idx = np.digitize(freqs, edges) - 1
        self.nb = len(edges) - 1
        self.valid = (freqs >= edges[0]) & (freqs < edges[-1])
        lag_min = max(1, int(self.sr / 400))
        lag_max = int(self.sr / 70)
        self.lag_rng = (lag_min, lag_max)
        L = self.cfg.max_echo_lag_frames + 2
        self.agent_hist = collections.deque([np.full(self.nb, FLOOR)] * L, maxlen=L)
        self._reset_echo()
        self.noise = np.full(self.nb, FLOOR * 10)
        self.prev_caller = np.zeros(n, dtype=np.float32)
        self.prev_shape = None
        self._rise_noise = 10 ** (self.cfg.noise_rise_db_per_s * self.cfg.frame_ms / 1000 / 10)

    def _reset_echo(self):
        self.H = np.full(self.nb, 10 ** (self.cfg.echo_init_db / 10))
        self.ratio_hist = collections.deque(maxlen=self.cfg.echo_window_frames)
        self.pc_s = collections.deque(maxlen=3)
        self.ref_s = collections.deque(maxlen=3)

    def reset_call(self):
        self.lag = None
        self.env_a = collections.deque(maxlen=150)
        self.env_c = collections.deque(maxlen=150)
        self.frame_no = 0
        if self.n is not None:
            self._setup(self.n)

    # ---------------------------------------------------------------- helpers
    def _bands(self, x: np.ndarray) -> np.ndarray:
        spec = np.fft.rfft(x * self.win)
        p = (spec.real ** 2 + spec.imag ** 2) / (self.n * self.n)
        out = np.bincount(self.band_idx[self.valid], weights=p[self.valid], minlength=self.nb)
        return np.maximum(out[: self.nb], FLOOR)

    def _voicing(self, x: np.ndarray) -> float:
        seg = np.concatenate([self.prev_caller, x])
        seg = seg - seg.mean()
        e = float(np.dot(seg, seg))
        if e < 1e-9:
            return 0.0
        m = len(seg)
        f = np.fft.rfft(seg, 2 * m)
        ac = np.fft.irfft(f.real ** 2 + f.imag ** 2)[:m]
        lo, hi = self.lag_rng
        hi = min(hi, m - 1)
        # normalise by overlap length so long lags are not penalised
        norm = ac[lo:hi] / (ac[0] + EPS) * (m / (m - np.arange(lo, hi)))
        return float(np.clip(norm.max(), 0.0, 1.0))

    def _update_lag(self):
        if len(self.env_a) < 100:
            return
        a = np.array(self.env_a)
        c = np.array(self.env_c)
        if a.std() < 3.0:  # agent not modulated enough to estimate a delay
            return
        best, best_k = -1.0, None
        K = self.cfg.max_echo_lag_frames
        for k in range(K + 1):
            aa = a[: len(a) - k]
            cc = c[k:]
            if aa.std() < 1e-6 or cc.std() < 1e-6:
                continue
            r = float(np.corrcoef(aa, cc)[0, 1])
            if r > best:
                best, best_k = r, k
        if best > 0.5 and best_k != self.lag:
            # the echo-gain estimate was calibrated against another reference
            self.lag = best_k
            self._reset_echo()

    # ------------------------------------------------------------------- main
    def process(self, agent, caller) -> FrameFeatures:
        a = to_float(agent).reshape(-1)
        c = to_float(caller).reshape(-1)
        if self.n != len(c):
            self._setup(len(c))
        self.frame_no += 1
        cfg = self.cfg

        Pa = self._bands(a) if len(a) == self.n else np.full(self.nb, FLOOR)
        Pc = self._bands(c)
        self.agent_hist.appendleft(Pa)
        agent_db = 10 * math.log10(Pa.sum())
        caller_db = 10 * math.log10(Pc.sum())
        self.env_a.append(agent_db)
        self.env_c.append(caller_db)
        if self.frame_no % 25 == 0:
            self._update_lag()

        # noise floor of the raw caller channel (minimum statistics: fast down,
        # slow up). Stationary noise must not be mistaken for echo below.
        down = Pc < self.noise
        self.noise = np.where(down, 0.8 * self.noise + 0.2 * Pc, self.noise * self._rise_noise)
        self.noise = np.maximum(self.noise, FLOOR)

        # echo reference: agent power at the estimated delay (+-1 frame smear),
        # or the max over all plausible delays while the delay is unknown.
        hist = list(self.agent_hist)
        if self.lag is None:
            ref = np.max(hist[: cfg.max_echo_lag_frames + 1], axis=0)
        else:
            lo = max(0, self.lag - 1)
            ref = np.max(hist[lo: self.lag + 2], axis=0)

        # echo-path gain per band = median log-ratio caller/agent-ref over the
        # last few seconds of agent speech, on 3-frame smoothed powers. The
        # median ignores the minority of frames where the caller talks too.
        self.pc_s.append(Pc)
        self.ref_s.append(ref)
        pc3 = np.mean(self.pc_s, axis=0)
        ref3 = np.mean(self.ref_s, axis=0)
        agent_active = ref3.sum() > 1e-7
        if agent_active:
            self.ratio_hist.append(np.log10(np.maximum(pc3 - self.noise, FLOOR) / ref3))
            if len(self.ratio_hist) >= 25 and self.frame_no % 5 == 0:
                med = np.percentile(np.array(self.ratio_hist), cfg.echo_percentile, axis=0)
                self.H = np.clip(10 ** med, 1e-7, 4.0)

        over = 10 ** (cfg.echo_overestimate_db / 10)
        echo_est = self.H * np.maximum(ref, ref3)
        excess = np.maximum(Pc - over * echo_est, 0.0)
        echo_frac = float(min(1.0, echo_est.sum() / Pc.sum()))

        ex_sum = float(excess.sum())
        f = FrameFeatures()
        f.caller_db = caller_db
        f.agent_db = agent_db
        f.excess_db = 10 * math.log10(ex_sum + FLOOR)
        f.snr_db = 10 * math.log10((ex_sum + EPS) / self.noise.sum())
        f.voicing = self._voicing(c)
        f.echo_frac = echo_frac
        # spectral change vs previous frame (articulation: consonants, vowel
        # changes). Level-normalised log spectrum, only bands above noise.
        shape = 10 * np.log10(np.maximum(excess, self.noise))
        shape = shape - shape.mean()
        f.flux = 0.0 if self.prev_shape is None else float(np.mean(np.abs(shape - self.prev_shape)))
        self.prev_shape = shape
        self.prev_caller = c

        f.vad_prob = None
        if self.vad is not None:
            try:
                x16 = c if self.sr == 16000 else np.interp(
                    np.arange(0, len(c), self.sr / 16000), np.arange(len(c)), c).astype(np.float32)
                f.vad_prob = self.vad.push(x16)
            except Exception:
                self.vad = None

        speech = f.snr_db > cfg.snr_on_db and (f.voicing > cfg.voicing_on or f.snr_db > cfg.snr_strong_db)
        if f.vad_prob is not None:
            # VAD confirms "is it speech"; our SNR term confirms "is it NOT echo"
            speech = f.snr_db > cfg.snr_on_db and f.vad_prob > cfg.silero_on
        f.is_speech = bool(speech)
        return f
