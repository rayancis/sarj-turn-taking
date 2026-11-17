#!/usr/bin/env python3
"""Deterministic acoustic augmentation of the public dev set (caller channel only).

Reads data/dev and writes full, loader-compatible splits (manifest.json + labels/ + audio/):

    data/aug/dev/<condition>/       seed family A
    data/aug/holdout/<condition>/   seed family B (frozen check set: different partner pairings,
                                    different random realisations, different parameter draws)

Same scenario ids, same length, same timings/events.  Channel 0 (agent) is copied bit-for-bit.  Only
channel 1 (caller microphone) is processed.  numpy + stdlib only: filters are windowed-sinc FIRs or
FFT spectral shaping, resampling is decimate / zero-stuff + FIR, G.711 mu-law is implemented here.

SNR definition: caller speech level = RMS of the ORIGINAL channel 1 inside labelled user_segments of
kind speech / backchannel / filler.  Added noise (whole-file RMS of the added component) is scaled to
speech_rms / 10^(snr/20).  The measured value is written to acoustic.snr_db.

Usage:
    python3 tools/augment_dev.py --src data/dev --out data/aug --tree both --conditions all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ttbench.dataset import read_wav_stereo, write_wav_stereo, sha256_file  # noqa: E402

GEN_VERSION = "1.0.0"
SR = 16000
FAMILY = {"dev": 1000, "holdout": 2000}          # seed families A and B
SPEECH_KINDS = {"speech", "backchannel", "filler"}

# label tokens (schema enum where one exists; the rest are extensions, see AUGMENT_REPORT.md)
TOKEN = {
    "phone_nb": ["phone-nb"], "ac_hum": ["ac-hum"], "car": ["car"], "street": ["street"],
    "cafe_babble": ["cafe"], "tv": ["tv"], "household": ["household"],
    "nonspeech_events": ["nonspeech-events"], "reverb": ["reverb"],
    "speakerphone": ["speakerphone", "reverb"], "level_low": ["level-low"],
    "level_high": ["level-high"], "packet_loss": ["packet-loss"],
    "combo_phone_street": ["street", "phone-nb"], "combo_speaker_ac": ["speakerphone", "reverb", "ac-hum"],
}
CONDITIONS = list(TOKEN)
NOISY_TOKENS = {"ac-hum", "car", "street", "cafe", "tv", "household", "speakerphone", "nonspeech-events"}

SNR_RANGE = {"ac_hum": (20, 25), "car": (10, 15), "street": (5, 12), "cafe_babble": (5, 10),
             "tv": (10, 14), "household": (10, 20)}

# Parameters that differ between the two seed families (same spec ranges for everything that is
# specified in SCRIPTS.md; these are the unspecified texture parameters).
PRM = {
    "hum_f0": {"dev": (49.7, 50.1), "holdout": (49.9, 50.3)},
    "fan_center": {"dev": (250, 600), "holdout": (450, 1000)},
    "car_fc": {"dev": (90, 220), "holdout": (160, 380)},
    "car_mod_hz": {"dev": (0.1, 0.3), "holdout": (0.25, 0.6)},
    "street_tilt": {"dev": (0.3, 0.6), "holdout": (0.5, 0.9)},
    "horn_f0": {"dev": (350, 460), "holdout": (430, 560)},
    "bike_f0": {"dev": (70, 120), "holdout": (100, 170)},
    "babble_lp": {"dev": (3800, 5200), "holdout": (3000, 4200)},
    "tv_lp": {"dev": (3800, 5500), "holdout": (3000, 4500)},
    "child_f0": {"dev": (330, 450), "holdout": (430, 560)},
    "clink_f": {"dev": (2500, 4200), "holdout": (3600, 6000)},
    "rt60_scale": {"dev": (0.0, 0.5), "holdout": (0.5, 1.0)},   # position inside the RT60 range
}


# ----------------------------------------------------------------------------- randomness

def derive_seed(*parts) -> int:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:4], "big")


class Ctx:
    """Per-file working context."""

    def __init__(self, tree, cond, sid, x, agent, label, seed, src):
        self.tree, self.cond, self.sid = tree, cond, sid
        self.x = x.astype(np.float64)              # original caller channel
        self.agent = agent.astype(np.float64)
        self.n = len(x)
        self.label = label
        self.seed = seed
        self.src = src
        self.mask = seg_mask(label, self.n)
        self.speech_rms = float(np.sqrt(np.mean(self.x[self.mask] ** 2))) if self.mask.any() \
            else float(np.sqrt(np.mean(self.x ** 2)))
        self.rng = np.random.default_rng(seed)

    def u(self, name):
        lo, hi = PRM[name][self.tree]
        return float(self.rng.uniform(lo, hi))


def seg_mask(label, n, kinds=SPEECH_KINDS):
    m = np.zeros(n, bool)
    for s in label["user_segments"]:
        if s["kind"] in kinds:
            m[max(0, s["start_ms"] * SR // 1000): min(n, s["end_ms"] * SR // 1000)] = True
    return m


def rms(a):
    return float(np.sqrt(np.mean(np.square(a)))) if len(a) else 0.0


# ----------------------------------------------------------------------------- DSP primitives

def fftconv(x, h):
    L = len(x) + len(h) - 1
    N = 1 << (L - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, N) * np.fft.rfft(h, N), N)[:L]


def fir_same(x, h):
    m = len(h) // 2
    return fftconv(x, h)[m:m + len(x)]


def firwin_bp(ntaps, lo, hi, sr=SR):
    """Blackman-windowed sinc band-pass (lo=0 -> low-pass).  ntaps odd."""
    k = np.arange(ntaps) - (ntaps - 1) / 2
    h = (2 * hi / sr) * np.sinc(2 * hi / sr * k)
    if lo > 0:
        h = h - (2 * lo / sr) * np.sinc(2 * lo / sr * k)
    return h * np.blackman(ntaps)


def lp_gain(fc, order=2):
    return lambda f: 1.0 / np.sqrt(1.0 + (f / fc) ** (2 * order))


def hp_gain(fc, order=2):
    return lambda f: 1.0 / np.sqrt(1.0 + (fc / np.maximum(f, 1e-3)) ** (2 * order))


def bp_gain(lo, hi, order=2):
    a, b = hp_gain(lo, order), lp_gain(hi, order)
    return lambda f: a(f) * b(f)


def colored(n, rng, gain):
    """Gaussian noise shaped in the frequency domain, unit std."""
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    y = np.fft.irfft(X * gain(f), n)
    return y / (y.std() + 1e-12)


def shape(x, gain):
    """Zero-phase spectral shaping of a finite signal (zero padded, no wrap-around)."""
    n = len(x)
    N = 1 << (2 * n - 1).bit_length()
    f = np.fft.rfftfreq(N, 1 / SR)
    return np.fft.irfft(np.fft.rfft(x, N) * gain(f), N)[:n]


def slow_mod(n, rng, fm, depth):
    m = colored(n, rng, lp_gain(fm, 2))
    return np.clip(1.0 + depth * m, 0.05, None)


def tvec(n):
    return np.arange(n) / SR


def add_at(dst, wave, start):
    s = max(0, int(start))
    e = min(len(dst), int(start) + len(wave))
    if e > s:
        dst[s:e] += wave[s - int(start): e - int(start)]


def norm_unit(x):
    return x / (x.std() + 1e-12)


# ---- G.711 mu-law (reference algorithm) ----

_LUT = np.array([0] + [int(np.floor(np.log2(i))) for i in range(1, 256)], dtype=np.int32)


def ulaw_encode(x):
    x = x.astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    mag = np.minimum(np.abs(x), 32635) + 0x84
    exp = _LUT[mag >> 7]
    mant = (mag >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mant)) & 0xFF


def ulaw_decode(b):
    b = (~b.astype(np.int32)) & 0xFF
    sign, exp, mant = b & 0x80, (b >> 4) & 7, b & 0x0F
    mag = (((mant << 3) + 0x84) << exp) - 0x84
    return np.where(sign != 0, -mag, mag)


def phone_nb(x, lo=300.0, hi=3400.0):
    """lo-hi Hz (nominally 300-3400), 8 kHz sample rate, mu-law round trip, back to 16 kHz.  Deterministic."""
    n = len(x)
    y = fir_same(x, firwin_bp(401, lo, hi))
    y8 = y[::2]
    q = np.clip(np.rint(y8), -32768, 32767).astype(np.int32)
    d = ulaw_decode(ulaw_encode(q)).astype(np.float64)
    up = np.zeros(2 * len(d))
    up[::2] = d * 2.0
    up = fir_same(up, firwin_bp(401, 0, 3700))
    out = np.zeros(n)
    m = min(n, len(up))
    out[:m] = up[:m]
    return out


def soft_limit(x, T=28000.0, top=32766.0):
    """Identity below T, smooth tanh knee above it; never reaches full scale."""
    a = np.abs(x)
    over = a > T
    if not over.any():
        return x
    y = x.copy()
    y[over] = np.sign(x[over]) * (T + (top - T) * np.tanh((a[over] - T) / (top - T)))
    return y


# ---- reverb ----

def make_rir(rng, rt60, wet):
    L = int(SR * min(rt60 * 1.1, 1.2))
    t = np.arange(L) / SR
    tail = rng.standard_normal(L) * np.exp(-6.9078 * t / rt60)
    tail[: int(0.004 * SR)] = 0
    tail = shape(tail, lp_gain(rng.uniform(4500, 7000), 2))
    tail *= np.sqrt(wet / (np.sum(tail ** 2) + 1e-12))
    h = tail
    h[0] += 1.0
    for _ in range(int(rng.integers(4, 9))):      # a few discrete early reflections
        d = int(rng.uniform(0.004, 0.04) * SR)
        h[d] += rng.choice([-1, 1]) * rng.uniform(0.15, 0.5)
    return h


def apply_rir(x, h):
    return fftconv(x, h)[: len(x)]


def seg_rms(x, mask):
    return rms(x[mask]) if mask.any() else rms(x)


# ----------------------------------------------------------------------------- event synthesisers
# All return float arrays with arbitrary scale (caller rescales); they use only the rng passed in.

def fade_env(n, a, r):
    e = np.ones(n)
    a, r = max(1, min(a, n // 2)), max(1, min(r, n // 2))
    e[:a] = np.linspace(0, 1, a)
    e[-r:] = np.linspace(1, 0, r)
    return e


def harmonic_tone(f_track, nharm, rolloff=1.0):
    ph = 2 * np.pi * np.cumsum(f_track) / SR
    y = np.zeros(len(f_track))
    for k in range(1, nharm + 1):
        valid = (f_track * k) < SR / 2 * 0.95
        y += np.where(valid, np.sin(k * ph) / k ** rolloff, 0)
    return y


def synth_horn(rng, ctx_u):
    dur = rng.uniform(0.3, 1.2)
    n = int(dur * SR)
    f0 = ctx_u("horn_f0")
    ratio = rng.choice([1.25, 1.335, 1.5])
    t = tvec(n)
    vib = 1 + 0.004 * np.sin(2 * np.pi * rng.uniform(4, 7) * t)
    y = harmonic_tone(f0 * vib, 5, 1.0) + 0.8 * harmonic_tone(f0 * ratio * vib, 5, 1.0)
    y += 0.05 * rng.standard_normal(n)
    return y * fade_env(n, int(0.02 * SR), int(0.08 * SR))


def synth_bike(rng, ctx_u):
    dur = rng.uniform(1.0, 3.0)
    n = int(dur * SR)
    t = tvec(n)
    f0 = ctx_u("bike_f0")
    sweep = rng.uniform(1.4, 2.4)
    f_track = f0 * (1 + (sweep - 1) * np.sin(np.pi * t / dur) ** 1.5)
    saw = harmonic_tone(f_track, 25, 0.7)
    saw = np.tanh(2.5 * norm_unit(saw))                       # rasp
    ph = 2 * np.pi * np.cumsum(f_track) / SR
    saw *= 1 + 0.4 * np.sin(0.5 * ph)                          # cylinder pulsing
    hiss = shape(rng.standard_normal(n), bp_gain(500, 3500)) * 0.4
    y = norm_unit(saw) + norm_unit(hiss) * 0.5
    return y * np.sin(np.pi * t / dur) ** 2


def synth_door_slam(rng, ctx_u):
    n = int(rng.uniform(0.4, 0.8) * SR)
    t = tvec(n)
    thump = np.sin(2 * np.pi * rng.uniform(55, 120) * t) * np.exp(-t / rng.uniform(0.06, 0.15))
    crack = shape(rng.standard_normal(n), lp_gain(2000, 2)) * np.exp(-t / 0.02)
    y = norm_unit(thump) * 1.0 + norm_unit(crack) * 0.6 * 1.0
    d = int(rng.uniform(0.08, 0.15) * SR)                     # small bounce
    if d < n:
        y[d:] += 0.3 * y[: n - d]
    return y


def synth_clink(rng, ctx_u):
    f = ctx_u("clink_f")
    hits = int(rng.integers(1, 4))
    n = int(0.5 * SR)
    y = np.zeros(n)
    t = tvec(n)
    pos = 0
    for _ in range(hits):
        ratios = np.array([1, 1.59, 2.14, 2.65, 3.2]) * rng.uniform(0.9, 1.1)
        tau = rng.uniform(0.015, 0.07)
        w = sum(rng.uniform(0.3, 1) * np.sin(2 * np.pi * f * r * t[: n - pos] + rng.uniform(0, 6.28))
                * np.exp(-t[: n - pos] / (tau / (0.6 + 0.4 * r))) for r in ratios)
        y[pos:] += w * rng.uniform(0.6, 1.0)
        pos += int(rng.uniform(0.04, 0.15) * SR)
        if pos >= n - 200:
            break
    return y


def synth_child(rng, ctx_u):
    dur = rng.uniform(0.2, 0.8)
    n = int(dur * SR)
    t = tvec(n)
    f0 = ctx_u("child_f0")
    f_track = f0 * (1 + rng.uniform(-0.2, 0.3) * (t / dur)) * (1 + 0.03 * np.sin(2 * np.pi * 5.5 * t))
    src = harmonic_tone(f_track, 20, 1.0) + 0.05 * rng.standard_normal(n)
    F1, F2 = rng.uniform(750, 1100), rng.uniform(2300, 3200)
    g = lambda f: (np.exp(-0.5 * ((f - F1) / 150) ** 2) + 0.7 * np.exp(-0.5 * ((f - F2) / 300) ** 2) + 0.05)
    y = shape(src, g)
    return y * fade_env(n, int(0.03 * SR), int(0.1 * SR))


def synth_cough(rng, ctx_u=None):
    pulses = int(rng.integers(1, 4))
    parts, gap = [], int(rng.uniform(0.08, 0.2) * SR)
    for i in range(pulses):
        n = int(rng.uniform(0.09, 0.2) * SR)
        t = tvec(n)
        noise = shape(rng.standard_normal(n), bp_gain(300, rng.uniform(2500, 4500)))
        buzz = harmonic_tone(np.full(n, rng.uniform(110, 200)), 14, 1.0)
        env = np.exp(-t / rng.uniform(0.03, 0.06)) * np.minimum(1, t / 0.004)
        p = (norm_unit(noise) * 0.9 + norm_unit(buzz) * 0.5 * np.exp(-t / 0.05)) * env
        parts.append(p * (0.85 ** i))
        parts.append(np.zeros(gap))
    return np.concatenate(parts[:-1])


def synth_laugh(rng, ctx_u=None):
    reps = int(rng.integers(3, 7))
    f0 = rng.uniform(180, 320)
    parts = []
    period = int(rng.uniform(0.15, 0.22) * SR)
    for i in range(reps):
        n = int(rng.uniform(0.08, 0.13) * SR)
        t = tvec(n)
        ft = f0 * (1 - 0.15 * i / reps) * (1 - 0.1 * t / t[-1])
        src = harmonic_tone(ft, 14, 1.0) + 0.15 * rng.standard_normal(n)
        F1 = rng.uniform(650, 900)
        y = shape(src, lambda f: np.exp(-0.5 * ((f - F1) / 200) ** 2) + 0.5 * np.exp(-0.5 * ((f - 2300) / 400) ** 2) + 0.03)
        y = norm_unit(y) * np.hanning(n) * (0.88 ** i)
        parts.append(np.concatenate([y, np.zeros(max(period - n, 0))]))
    return np.concatenate(parts)


def synth_sigh(rng, ctx_u=None):
    dur = rng.uniform(0.6, 1.2)
    n = int(dur * SR)
    t = tvec(n)
    breath = shape(rng.standard_normal(n), bp_gain(400, 4000))
    voiced = harmonic_tone(rng.uniform(150, 250) * (1 - 0.2 * t / dur), 10, 1.0)
    env = np.sin(np.pi * np.clip(t / dur, 0, 1) ** 0.7) ** 2
    return (norm_unit(breath) + 0.12 * norm_unit(voiced)) * env


# ----------------------------------------------------------------------------- noise makers
# Each returns (noise_float_unscaled, info_dict).  Scaling to SNR is done by the caller.

def noise_ac_hum(c):
    n, rng = c.n, c.rng
    t = tvec(n)
    f0 = c.u("hum_f0")
    amps = [1.0, rng.uniform(0.4, 0.8), rng.uniform(0.1, 0.4)]
    hum = sum(a * np.sin(2 * np.pi * f0 * (k + 1) * t + rng.uniform(0, 6.28)) * (1 + 0.05 * np.sin(2 * np.pi * rng.uniform(0.05, 0.3) * t))
              for k, a in enumerate(amps))
    fc = c.u("fan_center")
    fan = colored(n, rng, bp_gain(fc * 0.5, fc * 2.5, 2)) * slow_mod(n, rng, 0.3, 0.1)
    ratio_db = rng.uniform(-3, 6)                    # hum vs fan power
    hum_n, fan_n = norm_unit(hum), fan
    y = hum_n * 10 ** (ratio_db / 20) + fan_n
    return y, {"hum_f0": round(f0, 2), "fan_center_hz": round(fc), "hum_vs_fan_db": round(ratio_db, 1)}


def noise_car(c):
    n, rng = c.n, c.rng
    fc = c.u("car_fc")
    brown = colored(n, rng, lambda f: lp_gain(fc, 2)(f) / np.maximum(f, 15.0))
    pink = colored(n, rng, lambda f: lp_gain(1500, 2)(f) / np.sqrt(np.maximum(f, 30.0)))
    mf = c.u("car_mod_hz")
    depth = rng.uniform(0.2, 0.4)
    y = (norm_unit(brown) + 0.35 * norm_unit(pink)) * slow_mod(n, rng, mf, depth)
    return y, {"rumble_fc_hz": round(fc), "mod_hz": round(mf, 2), "mod_depth": round(depth, 2)}


def noise_street(c):
    n, rng = c.n, c.rng
    tilt = c.u("street_tilt")
    bed = colored(n, rng, lambda f: bp_gain(60, 3500, 2)(f) * np.maximum(f / 100.0, 0.1) ** -tilt)
    bed = bed * slow_mod(n, rng, 0.3, 0.3)
    tt = tvec(n)
    for _ in range(int(rng.integers(1, 4))):         # passing vehicles
        centre, width = rng.uniform(0, n / SR), rng.uniform(1.5, 4.0)
        bed += colored(n, rng, bp_gain(100, 1500, 2)) * rng.uniform(1.0, 2.5) * np.exp(-0.5 * ((tt - centre) / width) ** 2)
    ev = np.zeros(n)
    nh, nb = int(rng.integers(1, 4)), int(rng.integers(1, 3))
    placed = {"horns": [], "bikes": []}
    for _ in range(nh):
        w = synth_horn(rng, c.u)
        st = int(rng.uniform(0, max(1, n - len(w))))
        add_at(ev, norm_unit(w) * rng.uniform(2.0, 4.5), st)
        placed["horns"].append(round(st / SR, 2))
    for _ in range(nb):
        w = synth_bike(rng, c.u)
        st = int(rng.uniform(0, max(1, n - len(w))))
        add_at(ev, norm_unit(w) * rng.uniform(1.5, 3.5), st)
        placed["bikes"].append(round(st / SR, 2))
    y = norm_unit(bed) + ev
    return y, {"tilt": round(tilt, 2), "horns_at_s": placed["horns"], "bikes_at_s": placed["bikes"]}


def pick_partners(src, sid, cond, tree, k):
    """Other-scenario partners.  Dev uses the first k of a permutation; holdout draws from the
    scenarios that are NOT among dev's first 6 (so pairings never coincide)."""
    others = [i for i in src.ids if i != sid]
    pa = np.random.default_rng(derive_seed("pair", "dev", cond, sid)).permutation(len(others))
    dev_order = [others[i] for i in pa]
    if tree == "dev":
        return dev_order[:k]
    rest = dev_order[6:]
    pb = np.random.default_rng(derive_seed("pair", "holdout", cond, sid)).permutation(len(rest))
    return [rest[i] for i in pb][:k]


def partner_speech(src, pid, n, rng, fade=int(0.02 * SR)):
    """Caller speech of another scenario (label-gated, unit RMS), time-shifted/tiled to n samples."""
    a = src.audio[pid][:, 1].astype(np.float64)
    m = seg_mask(src.labels[pid], len(a)).astype(np.float64)
    k = np.hanning(2 * fade + 1)
    m = np.clip(np.convolve(m, k / k.sum(), mode="same"), 0, 1)
    s = a * m
    r = seg_rms(a, m > 0.5)
    s = s / (r + 1e-9)
    s = np.roll(s, int(rng.integers(0, len(s))))
    return np.resize(s, n)


def noise_babble(c):
    n, rng = c.n, c.rng
    k = int(rng.integers(3, 7))
    partners = pick_partners(c.src, c.sid, "cafe_babble", c.tree, k)
    lp = c.u("babble_lp")
    rir = make_rir(rng, rng.uniform(0.2, 0.4), rng.uniform(0.4, 1.0))
    mix = np.zeros(n)
    for p in partners:
        mix += partner_speech(c.src, p, n, rng) * 10 ** (rng.uniform(-3, 3) / 20)
    mix = apply_rir(shape(mix, bp_gain(150, lp, 2)), rir)
    mix = norm_unit(mix)
    clk = np.zeros(n)
    nc = int(rng.integers(4, 13))
    for _ in range(nc):
        w = synth_clink(rng, c.u)
        add_at(clk, norm_unit(w) * rng.uniform(0.5, 2.0), int(rng.uniform(0, n)))
    return mix + clk, {"partners": partners, "n_voices": k, "n_clinks": nc, "lp_hz": round(lp)}


def noise_tv(c):
    n, rng = c.n, c.rng
    (p,) = pick_partners(c.src, c.sid, "tv", c.tree, 1)
    lp = c.u("tv_lp")
    s = partner_speech(c.src, p, n, rng)
    s = shape(s, bp_gain(180, lp, 3))
    rt = 0.25 + (0.5 - 0.25) * rng.uniform()
    s = apply_rir(s, make_rir(rng, rt, rng.uniform(0.8, 2.0)))
    return norm_unit(s), {"partner": p, "rt60_s": round(rt, 2), "lp_hz": round(lp)}


def noise_household(c):
    n, rng = c.n, c.rng
    ev = np.zeros(n)
    kinds = []
    for _ in range(int(rng.integers(3, 9))):
        kind = rng.choice(["door", "dish", "child", "child"])
        w = {"door": synth_door_slam, "dish": synth_clink, "child": synth_child}[kind](rng, c.u)
        w = norm_unit(w) * rng.uniform(0.7, 1.5)
        st = int(rng.uniform(0, max(1, n - len(w))))
        add_at(ev, w, st)
        kinds.append((str(kind), round(st / SR, 2)))
    tone = colored(n, rng, lambda f: lp_gain(2500, 2)(f) / np.sqrt(np.maximum(f, 40.0)))
    tone_db = rng.uniform(8, 16)
    y = ev + norm_unit(tone) * (rms(ev) * 10 ** (-tone_db / 20))
    return y, {"events": kinds, "room_tone_below_events_db": round(tone_db, 1)}


NOISE_FN = {"ac_hum": noise_ac_hum, "car": noise_car, "street": noise_street,
            "cafe_babble": noise_babble, "tv": noise_tv, "household": noise_household}


def add_noise(c, base, cname, ref_rms=None):
    """base + noise scaled to a drawn SNR (relative to the original caller speech level)."""
    lo, hi = SNR_RANGE[cname]
    snr = float(c.rng.uniform(lo, hi))
    noise, info = NOISE_FN[cname](c)
    ref = c.speech_rms if ref_rms is None else ref_rms
    noise = noise * (ref / 10 ** (snr / 20) / (rms(noise) + 1e-12))
    info["snr_target_db"] = round(snr, 2)
    return base + noise, noise, info


# ----------------------------------------------------------------------------- conditions
# Each returns dict(out=float ch1, noise=float|None (added component), ref_rms=float, extra=dict, info=dict)

def forbidden_zones(label, n):
    """Sample intervals where nothing may be inserted (labelled segments, event windows)."""
    z = []
    for s in label["user_segments"]:
        z.append((s["start_ms"] - 400, s["end_ms"] + 400))
    for e in label["events"]:
        ts = [e[k] for k in ("onset_ms", "offset_ms", "window_end_ms", "deadline_ms", "evident_ms", "turn_end_ms")
              if e.get(k) is not None]
        ts += [p["end_ms"] for p in e.get("pauses", [])] + [p["start_ms"] for p in e.get("pauses", [])]
        z.append((min(ts) - 500, max(ts) + 300))
    dur = n * 1000 / SR
    for b in label.get("background_events", []):
        if b["end_ms"] - b["start_ms"] < 0.8 * dur:       # localized background events (horn, door ...)
            z.append((b["start_ms"] - 300, b["end_ms"] + 300))
    z.sort()
    merged = []
    for a, b in z:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def cond_nonspeech(c):
    n, rng = c.n, c.rng
    free = []
    cur = 200
    for a, b in forbidden_zones(c.label, n):
        if a > cur:
            free.append([cur, a])
        cur = max(cur, b)
    end = n * 1000 // SR - 200
    if end > cur:
        free.append([cur, end])
    added = np.zeros(n)
    placed = []
    want = int(rng.integers(1, 4))
    for _ in range(want):
        kind = str(rng.choice(["cough", "laugh", "sigh"]))
        w = {"cough": synth_cough, "laugh": synth_laugh, "sigh": synth_sigh}[kind](rng)
        d_ms = len(w) * 1000 / SR
        cand = [(i, f) for i, f in enumerate(free) if f[1] - f[0] >= d_ms + 100]
        if not cand:
            continue
        lens = np.array([f[1] - f[0] - d_ms for _, f in cand])
        i, f = cand[int(rng.choice(len(cand), p=lens / lens.sum()))]
        st = f[0] + 50 + rng.uniform(0, f[1] - f[0] - d_ms - 100)
        gdb = rng.uniform(-10, -3)
        w = norm_unit(w) * c.speech_rms * 10 ** (gdb / 20)
        add_at(added, w, int(st * SR / 1000))
        placed.append({"kind": kind, "start_ms": int(st), "end_ms": int(st + d_ms), "level_db_re_speech": round(gdb, 1)})
        free.remove(f)
        for g in ([f[0], st - 150], [st + d_ms + 150, f[1]]):
            if g[1] - g[0] > 50:
                free.append(g)
    return {"out": c.x + added, "noise": None, "ref_rms": c.speech_rms, "info": {"bursts": placed}}


def draw_rir(c, lo=0.3, hi=0.8):
    """RT60 uniform in [lo, hi]; the two seed families draw from different halves of the range."""
    a, b = PRM["rt60_scale"][c.tree]
    rt60 = float(lo + (hi - lo) * c.rng.uniform(a, b))
    return rt60, make_rir(c.rng, rt60, float(c.rng.uniform(0.3, 0.9)))


def cond_reverb(c):
    rt60, h = draw_rir(c)
    y = apply_rir(c.x, h)
    y *= c.speech_rms / (seg_rms(y, c.mask) + 1e-9)
    return {"out": y, "noise": None, "ref_rms": c.speech_rms, "info": {"rt60_s": round(rt60, 2)}}


def cond_speakerphone(c):
    rt60, h = draw_rir(c)
    y = apply_rir(c.x, h)
    y *= c.speech_rms / (seg_rms(y, c.mask) + 1e-9)
    lvl = float(c.rng.uniform(-25, -15))
    delay = int(c.rng.integers(150, 251))
    a = np.zeros(c.n)
    d = delay * SR // 1000
    a[d:] = c.agent[: c.n - d]
    leak = apply_rir(a, h) * 10 ** (lvl / 20)
    return {"out": y + leak, "noise": None, "ref_rms": c.speech_rms,
            "echo": {"level_db": round(lvl, 1), "delay_ms": delay}, "info": {"rt60_s": round(rt60, 2)}}


def draw_phone_edges(c):
    """Band edges jittered a little around 300 / 3400 Hz so the two seed families differ."""
    return float(c.rng.uniform(280, 320)), float(c.rng.uniform(3300, 3500))


def cond_phone(c):
    lo, hi = draw_phone_edges(c)
    y = phone_nb(c.x, lo, hi)
    return {"out": y, "noise": None, "ref_rms": seg_rms(y, c.mask),
            "info": {"band_hz": [round(lo), round(hi)]}}


def cond_level_low(c):
    g = float(c.rng.uniform(-14, -10))
    return {"out": c.x * 10 ** (g / 20), "noise": None, "ref_rms": c.speech_rms * 10 ** (g / 20), "info": {"gain_db": round(g, 1)}}


def cond_level_high(c):
    g = float(c.rng.uniform(8, 12))
    y = 32767.0 * np.tanh(c.x * 10 ** (g / 20) / 32767.0)
    return {"out": y, "noise": None, "ref_rms": c.speech_rms * 10 ** (g / 20), "info": {"gain_db": round(g, 1), "no_limiter": True}}


def cond_packet_loss(c):
    rng, n = c.rng, c.n
    F = SR * 20 // 1000
    nf = -(-n // F)
    target = float(rng.uniform(0.02, 0.05))
    mode = str(rng.choice(["zero", "repeat"]))
    L = float(rng.uniform(1.5, 3.0))                     # mean burst length (frames)
    b2g = 1.0 / L
    g2b = target * b2g / (1 - target)
    for _ in range(50):                                  # redraw until realised rate is inside 2-5 %
        lost = np.zeros(nf, bool)
        bad = False
        for i in range(1, nf):
            bad = (rng.random() < g2b) if not bad else (rng.random() >= b2g)
            lost[i] = bad
        if 0.02 <= lost.mean() <= 0.05:
            break
    y = c.x.copy()
    for i in np.flatnonzero(lost):
        s, e = i * F, min(n, (i + 1) * F)
        y[s:e] = 0.0 if mode == "zero" else y[max(0, s - F): max(0, s - F) + (e - s)]
    return {"out": y, "noise": None, "ref_rms": c.speech_rms,
            "info": {"loss_rate": round(float(lost.mean()), 4), "mode": mode, "mean_burst_frames": round(L, 2),
                     "lost_ms": [[int(i * 20), int(i * 20 + 20)] for i in np.flatnonzero(lost)]}}


def make_additive(cname):
    def f(c):
        out, noise, info = add_noise(c, c.x, cname)
        return {"out": out, "noise": noise, "ref_rms": c.speech_rms, "info": info}
    return f


def cond_combo_phone_street(c):
    """street, then phone_nb.  The noise level is calibrated AFTER the phone chain so that the
    SNR measured on the final signal (phone(x+n) - phone(x) against phone(x) speech) hits the draw."""
    lo, hi = draw_phone_edges(c)
    snr = float(c.rng.uniform(*SNR_RANGE["street"]))
    noise, info = NOISE_FN["street"](c)
    ref = phone_nb(c.x, lo, hi)
    ref_rms = seg_rms(ref, c.mask)
    want = ref_rms / 10 ** (snr / 20)
    noise = noise * (want / (rms(phone_nb(noise * c.speech_rms / (rms(noise) + 1e-12), lo, hi)) + 1e-12)) * c.speech_rms / (rms(noise) + 1e-12)
    for _ in range(2):                               # mu-law is not exactly linear: refine twice
        out = phone_nb(c.x + noise, lo, hi)
        noise = noise * want / (rms(out - ref) + 1e-12)
    out = phone_nb(c.x + noise, lo, hi)
    info.update({"snr_target_db": round(snr, 2), "band_hz": [round(lo), round(hi)]})
    return {"out": out, "noise": out - ref, "ref_rms": ref_rms, "info": info}


def cond_combo_speaker_ac(c):
    s = cond_speakerphone(c)
    out, noise, info = add_noise(c, s["out"], "ac_hum")
    info["rt60_s"] = s["info"]["rt60_s"]
    return {"out": out, "noise": noise, "ref_rms": c.speech_rms, "echo": s["echo"], "info": info}


COND_FN = {
    "phone_nb": cond_phone, "ac_hum": make_additive("ac_hum"), "car": make_additive("car"),
    "street": make_additive("street"), "cafe_babble": make_additive("cafe_babble"), "tv": make_additive("tv"),
    "household": make_additive("household"), "nonspeech_events": cond_nonspeech, "reverb": cond_reverb,
    "speakerphone": cond_speakerphone, "level_low": cond_level_low, "level_high": cond_level_high,
    "packet_loss": cond_packet_loss, "combo_phone_street": cond_combo_phone_street,
    "combo_speaker_ac": cond_combo_speaker_ac,
}


# ----------------------------------------------------------------------------- source + splits

class Source:
    def __init__(self, src_dir):
        self.dir = Path(src_dir)
        self.manifest = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))
        self.ids = [e["scenario_id"] for e in self.manifest["scenarios"]]
        self.labels, self.audio = {}, {}
        for sid in self.ids:
            lab = json.loads((self.dir / "labels" / f"{sid}.json").read_text(encoding="utf-8"))
            self.labels[sid] = lab
            self.audio[sid] = read_wav_stereo(self.dir / "audio" / lab["audio"]["file"])


def augment_scenario(src, sid, cond, tree):
    """Return (stereo int16 array, new label dict, per-file info dict).  Pure function of its inputs."""
    seed = derive_seed("aug", FAMILY[tree], cond, sid)
    st = src.audio[sid]
    c = Ctx(tree, cond, sid, st[:, 1], st[:, 0], src.labels[sid], seed, src)
    r = COND_FN[cond](c)
    out = r["out"]
    if cond != "level_high":
        out = soft_limit(out)
    q = np.clip(np.rint(out), -32767, 32767).astype("<i2")
    audio = np.stack([st[:, 0], q], axis=1)

    lab = json.loads(json.dumps(src.labels[sid]))
    ac = lab["acoustic"]
    orig = list(ac["conditions"])
    added = TOKEN[cond]
    base = [t for t in orig if not (t == "quiet" and set(added) & NOISY_TOKENS)]
    ac["conditions"] = base + [t for t in added if t not in base]
    if r["noise"] is not None:
        snr = 20 * np.log10(r["ref_rms"] / (rms(r["noise"]) + 1e-12))
        ac["snr_db"] = round(float(snr), 1)
    else:
        snr = None
    if "echo" in r:
        ac["echo"] = r["echo"]
    ac["noise_source_ids"] = list(ac.get("noise_source_ids", [])) + [f"AUG-{tree}-{cond}-{seed}"]
    ac["notes"] = f"augmented: {cond}, seed {seed}; base conditions: {'+'.join(orig)}"
    info = {"scenario_id": sid, "condition": cond, "tree": tree, "seed": seed,
            "base_conditions": orig, "snr_db": ac.get("snr_db"), "params": r["info"]}
    if "echo" in r:
        info["echo"] = r["echo"]
    return audio, lab, info


def jdump(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False)


def build_split(src_dir, out_root, tree, cond, only=None, src=None):
    src = src or Source(src_dir)
    out = Path(out_root) / tree / cond
    (out / "audio").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    entries, infos = [], []
    for sid in src.ids:
        if only and sid not in only:
            continue
        audio, lab, info = augment_scenario(src, sid, cond, tree)
        wav = out / "audio" / lab["audio"]["file"]
        write_wav_stereo(wav, audio)
        lab["audio"]["sha256"] = sha256_file(wav)
        lp = out / "labels" / f"{sid}.json"
        lp.write_text(jdump(lab), encoding="utf-8")
        entries.append({"scenario_id": sid, "wav_sha256": lab["audio"]["sha256"], "label_sha256": sha256_file(lp),
                        "aug": {"seed": info["seed"], "snr_db": info["snr_db"], "params": info["params"]}})
        infos.append(info)
    m = json.loads(json.dumps(src.manifest))
    m["scenarios"] = entries
    m["source_split"] = "dev"
    m["augmentation"] = {"condition": cond, "tree": tree, "seed_family": FAMILY[tree],
                         "generator": "tools/augment_dev.py", "generator_version": GEN_VERSION}
    (out / "manifest.json").write_text(jdump(m), encoding="utf-8")
    return infos


def _job(args):
    src_dir, out_root, tree, cond, only = args
    t = time.time()
    infos = build_split(src_dir, out_root, tree, cond, only)
    return tree, cond, infos, time.time() - t


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default="data/dev")
    ap.add_argument("--out", default="data/aug")
    ap.add_argument("--tree", default="both", choices=["dev", "holdout", "both"])
    ap.add_argument("--conditions", default="all")
    ap.add_argument("--only", default="", help="comma separated scenario ids (default: all)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--stats-json", default="", help="write per-file info to this json file")
    a = ap.parse_args(argv)
    conds = CONDITIONS if a.conditions == "all" else a.conditions.split(",")
    bad = [x for x in conds if x not in COND_FN]
    if bad:
        ap.error(f"unknown conditions {bad}; choose from {CONDITIONS}")
    trees = ["dev", "holdout"] if a.tree == "both" else [a.tree]
    only = [s for s in a.only.split(",") if s] or None
    jobs = [(a.src, a.out, t, cnd, only) for t in trees for cnd in conds]
    t0 = time.time()
    res = []
    if a.jobs > 1:
        with Pool(a.jobs) as p:
            for r in p.imap_unordered(_job, jobs):
                print(f"{r[0]:8s} {r[1]:20s} {len(r[2])} files {r[3]:.1f}s", flush=True)
                res.append(r)
    else:
        for j in jobs:
            r = _job(j)
            print(f"{r[0]:8s} {r[1]:20s} {len(r[2])} files {r[3]:.1f}s", flush=True)
            res.append(r)
    print(f"done in {time.time() - t0:.1f}s")
    if a.stats_json:
        Path(a.stats_json).write_text(json.dumps([i for r in res for i in r[2]], indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
