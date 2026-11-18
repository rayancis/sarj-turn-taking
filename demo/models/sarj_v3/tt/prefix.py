"""Prefix features: describe the first N ms of a caller speech run.

Used identically at training time (tools/train_classifier.py) and at runtime
(Decider), so the classifier sees exactly what the live system sees. Only
cheap scalar features here; embedding-based models go behind the same API.
"""
from __future__ import annotations

import json
import math
from typing import List, Optional

import numpy as np

FEATURE_NAMES = [
    "speech_ratio",       # share of frames flagged speech since onset
    "snr_mean", "snr_max",
    "level_rel_caller",   # excess level minus caller's own level (BG/BC are softer)
    "voicing_mean",
    "level_slope",        # dB per 100 ms (BC fade quickly, interruptions build up)
    "echo_frac_mean",
    "agent_level_mean",   # agent speaking loudly over it, or pausing?
    "agent_pause_at_onset",
    "near_phrase_end",    # onset within 450 ms after an agent phrase end
    "after_question",     # onset within 1.2 s after an agent question
    "t_in_utt_s",         # how far into the agent utterance
]


def prefix_vector(frames, caller_ref_db: Optional[float], agent_pause: bool,
                  near_phrase_end: bool, after_question: bool, t_in_utt_ms: float) -> np.ndarray:
    """frames: list of FrameFeatures from run onset onwards."""
    if not frames:
        return np.zeros(len(FEATURE_NAMES), dtype=np.float32)
    snr = np.array([f.snr_db for f in frames])
    exc = np.array([f.excess_db for f in frames])
    sp = np.array([f.is_speech for f in frames], dtype=float)
    voi = np.array([f.voicing for f in frames])
    ech = np.array([f.echo_frac for f in frames])
    ag = np.array([f.agent_db for f in frames])
    ref = caller_ref_db if caller_ref_db is not None else float(np.median(exc[sp > 0])) if sp.any() else -40.0
    lvl = float(np.mean(exc[sp > 0])) if sp.any() else float(exc.mean())
    if len(exc) >= 3:
        slope = float(np.polyfit(np.arange(len(exc)) * 0.2, exc, 1)[0])  # dB per 100 ms
    else:
        slope = 0.0
    v = [sp.mean(), snr.mean(), snr.max(), lvl - ref, voi.mean(), slope, ech.mean(),
         max(ag.mean(), -90.0), float(agent_pause), float(near_phrase_end),
         float(after_question), t_in_utt_ms / 1000.0]
    return np.nan_to_num(np.array(v, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)


class LinearModel:
    """Standardised logistic regression stored as JSON (numpy-only at runtime)."""

    def __init__(self, path: str):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        assert d["features"] == FEATURE_NAMES, "feature set changed: retrain"
        self.prefix_ms = float(d["prefix_ms"])
        self.mu = np.array(d["mean"])
        self.sd = np.array(d["std"])
        self.w = np.array(d["coef"])
        self.b = float(d["intercept"])

    def p_interrupt(self, x: np.ndarray) -> float:
        z = float(np.dot((x - self.mu) / self.sd, self.w) + self.b)
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
