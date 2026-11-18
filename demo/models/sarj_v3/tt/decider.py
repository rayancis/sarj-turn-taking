"""Decision logic: turns per-frame speech evidence into STOP / RESUME.

Pure Python, no I/O, so it can be unit-tested and replayed offline.
"""
from __future__ import annotations

import collections
import re

import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional

from .config import Config
from .frontend import Frontend, FrameFeatures
from .prefix import LinearModel, prefix_vector
from .words import classify

STOP, RESUME = "STOP", "RESUME"
_PUNCT_END = re.compile(r"[.!?؟،,؛;:…]$")
_QUESTION = re.compile(r"[?؟]$")


@dataclass
class Word:
    text: str
    start_ms: float
    end_ms: float


@dataclass
class AgentPlan:
    """What we know about the agent utterance from agent_utterance_start."""
    text: str = ""
    words: List[Word] = field(default_factory=list)
    phrase_ends_ms: List[float] = field(default_factory=list)
    question_ends_ms: List[float] = field(default_factory=list)


def estimate_word_times(text: str, duration_ms: float) -> List[Word]:
    """No word timings from the kit: spread the utterance duration over the
    words by character count, with extra weight for punctuation pauses."""
    toks = text.split()
    if not toks:
        return []
    weights = [len(w) + (4 if _PUNCT_END.search(w) else 1) for w in toks]
    scale = duration_ms / sum(weights)
    out, t = [], 0.0
    for w, wt in zip(toks, weights):
        speak = (len(w) + 1) * scale
        out.append(Word(w, t, t + speak))
        t += wt * scale
    return out


def parse_agent_info(*args, **kwargs) -> AgentPlan:
    """Accept whatever the kit passes at agent start (dict, object, text, words)."""
    info = {}
    for a in args:
        if isinstance(a, dict):
            info.update(a)
        elif isinstance(a, str):
            info.setdefault("text", a)
        elif isinstance(a, (list, tuple)):
            info.setdefault("words", a)
        elif isinstance(a, (int, float)) and not isinstance(a, bool):
            info.setdefault("duration_ms", float(a))
        elif a is not None and hasattr(a, "__dict__"):
            info.update(vars(a))
    info.update(kwargs)
    plan = AgentPlan(text=str(info.get("text") or info.get("transcript") or ""))
    words = info.get("words") or info.get("word_timings") or info.get("timings") or []
    for w in words:
        try:
            if isinstance(w, dict):
                t = w.get("word") or w.get("text") or ""
                s = float(w.get("start_ms", w.get("start", 0)))
                e = float(w.get("end_ms", w.get("end", s)))
            else:
                t, s, e = str(w[0]), float(w[1]), float(w[2])
        except Exception:
            continue
        plan.words.append(Word(t, s, e))
    if not plan.words and plan.text and float(info.get("duration_ms") or 0) > 0:
        plan.words = estimate_word_times(plan.text, float(info["duration_ms"]))
    # timings in seconds? convert if the whole utterance is < 100 "units"
    if plan.words and plan.words[-1].end_ms < 100:
        for w in plan.words:
            w.start_ms *= 1000.0
            w.end_ms *= 1000.0
    for i, w in enumerate(plan.words):
        nxt = plan.words[i + 1] if i + 1 < len(plan.words) else None
        gap = (nxt.start_ms - w.end_ms) if nxt else 0
        if _PUNCT_END.search(w.text.strip()) or gap > 250:
            plan.phrase_ends_ms.append(w.end_ms)
        if _QUESTION.search(w.text.strip()) and nxt is not None:
            plan.question_ends_ms.append(w.end_ms)
    return plan


class Decider:
    def __init__(self, cfg: Optional[Config] = None, words=None):
        self.cfg = cfg or Config()
        self.words = words          # WordsWorker (tt/words.py) or None
        self.audio = collections.deque(maxlen=int(self.cfg.asr_window_ms // self.cfg.frame_ms) + 20)
        self.utt_seq = 0
        self.run_seq = 0
        self.last_submit = -1e9
        self.fe = Frontend(self.cfg)
        self.caller_ref_db = None   # caller's own speaking level (near-field)
        self._ref_levels = collections.deque(maxlen=500)
        self.log = []               # (t_ms, event, details) for failure analysis
        self.agent_on = False
        self.plan = AgentPlan()
        self.prefix_sink = None     # training: list that receives (abs_ms, run_start_abs_ms, vec)
        self.prefix_ms = 300.0
        self.abs_t = 0.0
        self.clf = None
        if self.cfg.use_clf:
            try:
                self.clf = LinearModel(self.cfg.clf_path)
            except Exception:
                self.clf = None
        self._reset_utt()

    # --------------------------------------------------------------- events
    def _reset_utt(self):
        self.t = 0.0
        self.stopped = False
        self.stop_t = None
        self.resumed = False
        self.n_resumes = 0
        self.run_speech = 0.0
        self.run_gap = 1e9
        self.run_start = None
        self.run_levels = []
        self.run_flux = []
        self.last_agent_voice_t = 0.0
        self.run_in_pause = False
        self.run_frames = []
        self.run_ctx = (False, False, False, 0.0)
        self.clf_adjust = 0.0
        self.word_verdict = None
        self.word_text = ""
        self.final_sent = False

    def agent_start(self, *args, **kwargs):
        self.agent_on = True
        try:
            self.plan = parse_agent_info(*args, **kwargs)
        except Exception:
            self.plan = AgentPlan()
        self.utt_seq += 1
        self._reset_utt()

    def agent_end(self, *args, **kwargs):
        self.agent_on = False
        self.stopped = False

    # ------------------------------------------------------------ main loop
    def frame(self, agent, caller) -> Optional[str]:
        cfg = self.cfg
        f = self.fe.process(agent, caller)
        self.t += cfg.frame_ms
        t = self.t
        self.abs_t += cfg.frame_ms
        if self.words is not None:
            self.audio.append((self.abs_t, np.asarray(caller, dtype=np.int16).copy()))
            self._poll_words()

        if not self.agent_on:
            # the caller is the main speaker now: learn their near-field level
            # robust: a high percentile of the caller's speech frames, so weak
            # onsets/offsets and background talk do not pull it down
            if f.is_speech and f.snr_db > cfg.snr_strong_db:
                self._ref_levels.append(f.excess_db)
                if len(self._ref_levels) >= 10:
                    self.caller_ref_db = float(np.percentile(self._ref_levels, cfg.caller_ref_pct))
            return None

        if f.agent_db > -50:
            self.last_agent_voice_t = t

        # ---- run tracking (speech frames with short gaps tolerated) ----
        speech = f.is_speech and t > cfg.min_ms_after_agent_start
        if speech and self._is_background(f):
            speech = False
        if speech:
            # a new run only once the previous one has fully leaked away
            if self.run_gap > cfg.hangover_ms and self.run_speech <= 0:
                self.run_start = t
                self.run_speech = 0.0
                self.run_levels = []
                self.run_flux = []
                self.run_in_pause = (t - self.last_agent_voice_t) > 150
                self.run_frames = []
                self.run_ctx = self.context(t)
                self.clf_adjust = 0.0
                self.run_start_abs = self.abs_t
                self.run_seq += 1
                self.word_verdict = None
                self.word_text = ""
            self.run_speech += cfg.frame_ms
            self.run_levels.append(f.excess_db)
            self.run_flux.append(f.flux)
            self.run_gap = 0.0
        else:
            self.run_gap += cfg.frame_ms
            if self.run_gap > cfg.hangover_ms and self.run_speech > 0:
                # leaky accumulation: short words separated by pauses
                # ("لا … لا … لحظة") add up; isolated sounds fade out
                self.run_speech = max(0.0, self.run_speech - cfg.leak_rate * cfg.frame_ms)
        if self.run_start is not None and self.run_gap <= cfg.hangover_ms:
            self.run_frames.append(f)
            n_ms = len(self.run_frames) * cfg.frame_ms
            if self.clf is not None and n_ms == self.clf.prefix_ms:
                p = self.clf.p_interrupt(prefix_vector(self.run_frames, self.caller_ref_db, *self.run_ctx))
                self.clf_adjust = -cfg.clf_gain_ms * (2 * p - 1)
            if self.prefix_sink is not None and n_ms == self.prefix_ms:
                self.prefix_sink.append((self.abs_t, self.run_start_abs,
                                         prefix_vector(self.run_frames, self.caller_ref_db, *self.run_ctx)))
        run_ended = self.run_gap > cfg.hangover_ms

        # ---- after a (soft) stop: maybe take it back ----
        if self.stopped:
            if (cfg.resume and self.n_resumes < cfg.max_resumes
                    and self.run_gap >= cfg.resume_gap_ms
                    and self.run_speech < cfg.resume_if_run_under_ms
                    and t - self.stop_t <= cfg.resume_window_ms):
                # the caller went quiet again quickly: it was a backchannel /
                # noise, not a turn claim -> take the stop back
                self.stopped = False
                self.resumed = True
                self.n_resumes += 1
                self._log(t, RESUME, f)
                return RESUME
            return None

        if self.run_start is None:
            return None

        # ---- words layer ----
        if self.words is not None:
            if (self.word_verdict == "stop" and self.run_speech >= cfg.word_stop_min_ms
                    and self.run_gap <= cfg.word_stop_max_gap_ms
                    and self._run_rel_db() >= -cfg.word_stop_soft_db):
                # a floor-claiming word ("لا", "لحظة", "توقف"): stop now, even
                # if the caller already fell silent while ASR was working
                self.stopped = True
                self.stop_t = t
                self._log(t, STOP, f, words=self.word_text)
                return STOP
            if not run_ended:
                self._submit_words()
                self.final_sent = False
            elif not self.final_sent and self.word_verdict is None and self.run_gap <= cfg.asr_final_within_ms:
                # the caller just fell silent: transcribe the complete short
                # word once ("لحظة." alone), the case acoustics cannot decide
                self.final_sent = self._submit_words(final=True)

        if run_ended:
            return None

        need = self._needed_ms(f)
        if self.run_speech >= need:
            self.stopped = True
            self.stop_t = t
            self._log(t, STOP, f, need=need)
            return STOP
        return None

    # -------------------------------------------------------------- helpers
    def _run_rel_db(self) -> float:
        """Run level (80th pct) relative to the caller's own level; 0 if unknown."""
        if not self.run_levels or self.caller_ref_db is None:
            return 0.0
        return float(np.percentile(self.run_levels, 80)) - self.caller_ref_db

    def _submit_words(self, final: bool = False) -> bool:
        cfg = self.cfg
        if self.words.busy or (not final and (self.run_speech < cfg.asr_min_speech_ms
                                              or self.abs_t - self.last_submit < cfg.asr_every_ms)):
            return False
        t0 = self.run_start_abs - cfg.asr_pre_ms
        frames = [a for (ts, a) in self.audio if ts > t0]
        if not frames:
            return False
        req = (id(self), self.utt_seq, self.run_seq)
        if self.words.submit(req, np.concatenate(frames), self.abs_t,
                             t0_ms=max(t0, self.abs_t - cfg.asr_window_ms), t1_ms=self.abs_t):
            self.last_submit = self.abs_t
            return True
        return False

    def _poll_words(self):
        res = self.words.poll(self.abs_t)
        if res is None:
            return
        req, text = res
        if req != (id(self), self.utt_seq, self.run_seq):
            return  # stale: an earlier run, utterance or call
        v = classify(text)
        if v is not None and not (self.word_verdict == "stop" and v != "stop"):
            self.word_verdict, self.word_text = v, text

    def context(self, t: float):
        """(agent_pause, near_phrase_end, after_question, t_in_utt_ms) at time t."""
        return ((t - self.last_agent_voice_t) > 150,
                self._in_zone(t, self.plan.phrase_ends_ms, self.cfg.bc_zone_ms),
                self._in_zone(t, self.plan.question_ends_ms, self.cfg.question_zone_ms),
                t)

    def _is_background(self, f: FrameFeatures) -> bool:
        if self.caller_ref_db is None:
            return False
        return f.excess_db < self.caller_ref_db - self.cfg.bg_below_caller_db

    def _needed_ms(self, f: FrameFeatures) -> float:
        cfg = self.cfg
        if cfg.soft_stop and not self.resumed:
            need = cfg.soft_stop_after_ms
        else:
            need = cfg.stop_after_ms
            if self.resumed:
                need += cfg.after_resume_extra_ms  # we were wrong once this utterance
        start = self.run_start
        if self.run_levels and self.caller_ref_db is not None:
            # someone claiming the floor speaks at full voice; backchannels,
            # fillers, side talk and background are softer than the caller's
            # own turn level
            rel = float(np.percentile(self.run_levels, 80)) - self.caller_ref_db
            if rel > cfg.loud_bonus_db:
                need -= cfg.loud_shorten_ms
            elif rel < -cfg.soft_rel_db:
                # graded, not a cliff: every dB softer asks for more evidence
                need += min(cfg.soft_extra_ms, cfg.soft_ms_per_db * (-rel - cfg.soft_rel_db))
        if self.run_flux and len(self.run_flux) >= 5:
            # little spectral change = one sustained sound (أها، اممم، هيه), a horn
            if sum(self.run_flux) / len(self.run_flux) < cfg.flux_low:
                need += cfg.flux_extra_ms
        if self._in_zone(start, self.plan.phrase_ends_ms, cfg.bc_zone_ms):
            need += cfg.bc_zone_extend_ms
        elif not self.plan.phrase_ends_ms and self.run_in_pause:
            # no word timings: an agent pause is the fallback "phrase end"
            need += cfg.bc_zone_extend_ms
        if self._in_zone(start, self.plan.question_ends_ms, cfg.question_zone_ms):
            need -= cfg.question_shorten_ms
        need += self.clf_adjust
        if self.word_verdict == "keep":
            # only listener tokens so far (نعم، أها، تمام). Arabic answers often
            # START with one ("إيه الأحد، احجزه"), so a token cannot delay the
            # stop forever: it only asks the run to outlast a backchannel.
            need = max(need, cfg.keep_word_min_run_ms)
        elif self.word_verdict == "content":
            need -= cfg.content_word_shorten_ms
        return max(need, 160.0)

    @staticmethod
    def _in_zone(t: float, ends: List[float], width: float) -> bool:
        return any(0 <= t - e <= width for e in ends)

    def _log(self, t, ev, f, **kw):
        self.log.append((round(t), ev, repr(f), kw))
