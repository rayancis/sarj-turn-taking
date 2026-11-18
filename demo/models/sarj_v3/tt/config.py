"""All tunable parameters in one place so the scorer sweep can override them.

Times are in milliseconds, levels in dB. Defaults are first guesses: tune them
on the dev scorer, and prefer values that also hold on the D3 checkpoint set.
"""
from dataclasses import dataclass, field, asdict


@dataclass
class Config:
    frame_ms: float = 20.0

    # ---- echo suppression (frontend) ----
    max_echo_lag_frames: int = 15        # search echo delay 0..300 ms
    echo_overestimate_db: float = 6.0    # subtract this much more than the estimated echo
    echo_window_frames: int = 200        # agent-active frames used for the echo-gain estimate
    echo_percentile: float = 60.0        # robust estimate (caller speech is the minority)
    echo_init_db: float = -6.0           # pessimistic start: assume echo ~6 dB below agent

    # ---- noise floor (frontend) ----
    noise_rise_db_per_s: float = 3.0

    # ---- frame-level speech evidence ----
    snr_on_db: float = 9.0               # excess-over-noise needed for a speech frame
    snr_strong_db: float = 18.0          # above this, voicing is not required
    voicing_on: float = 0.40             # normalised autocorr peak (pitch 70-400 Hz)
    use_silero: bool = False             # needs models/silero_vad.onnx; A/B on dev first
    silero_on: float = 0.5

    # ---- background-speech rejection ----
    bg_below_caller_db: float = 99.0     # per-frame gate (off: the run-level soft_rel_db check does this); frames this far below the caller's own level are ignored
    caller_ref_pct: float = 80.0         # caller level = this percentile of their speech frames

    # ---- run / decision logic ----
    hangover_ms: float = 200.0           # gap that still counts as the same run
    leak_rate: float = 0.5               # after the hangover, accumulated speech decays at this rate (1e9 = hard reset)
    stop_after_ms: float = 520.0         # speech needed in a run before STOP
    loud_bonus_db: float = 4.0           # louder than caller's usual level ...
    loud_shorten_ms: float = 120.0       # ... -> stop sooner
    soft_rel_db: float = 0.0             # run (p80 level) below caller's level by more than this ...
    soft_ms_per_db: float = 300.0        # ... -> need this much more speech per extra dB
    soft_extra_ms: float = 4000.0        # cap
    flux_low: float = 0.0                # mean spectral change below this = sustained sound (0 = off)
    flux_extra_ms: float = 300.0
    bc_zone_ms: float = 450.0            # after an agent phrase end, backchannels are expected
    bc_zone_extend_ms: float = 160.0     # ... -> need more speech
    question_zone_ms: float = 1200.0     # after an agent question mid-utterance
    question_shorten_ms: float = 80.0
    min_ms_after_agent_start: float = 200.0  # ignore the first frames of an agent turn

    # ---- learned backchannel-vs-interruption classifier (tools/train_classifier.py) ----
    use_clf: bool = False
    clf_path: str = "models/bc_clf.json"
    clf_gain_ms: float = 200.0           # p=1 -> stop 200 ms sooner, p=0 -> 200 ms later

    # ---- words layer (tt/words.py) ----
    use_words: bool = True               # falls back to acoustics only if the model is missing
    asr_model_dir: str = "models/faster-whisper-tiny"
    asr_min_speech_ms: float = 160.0     # transcribe once a run has this much speech
    asr_every_ms: float = 200.0          # re-transcribe the growing run this often
    asr_pre_ms: float = 200.0            # audio kept before the run start
    asr_window_ms: float = 3000.0        # at most this much audio per request
    asr_final_within_ms: float = 600.0   # after the run ends, one last request (complete word) within this gap
    word_stop_min_ms: float = 100.0
    word_stop_max_gap_ms: float = 1000.0 # a stop word that arrives later than this after the caller fell silent is ignored (slow CPU)
    word_stop_soft_db: float = 6.0       # a stop word from a voice this much softer than the caller (TV, room) is ignored      # a stop word needs at least this much speech
    keep_word_min_run_ms: float = 800.0  # only listener tokens heard -> the run must last this long
    content_word_shorten_ms: float = 0.0

    # ---- soft stop + RESUME ----
    soft_stop: bool = False              # stop EARLY (soft_stop_after_ms), rely on RESUME
    soft_stop_after_ms: float = 300.0
    resume: bool = False                 # take back a stop if the caller goes quiet fast
    resume_if_run_under_ms: float = 900.0   # only if the whole run was this short
    resume_gap_ms: float = 140.0         # caller silence before we resume
    resume_window_ms: float = 3000.0     # <500 ms after STOP = half credit; later still un-silences the agent
    max_resumes: int = 3                 # per agent utterance
    after_resume_extra_ms: float = 150.0

    extra: dict = field(default_factory=dict)

    def override(self, **kw):
        for k, v in kw.items():
            if not hasattr(self, k):
                raise KeyError(f"unknown config key {k}")
            cur = getattr(self, k)
            if isinstance(cur, bool):
                v = str(v).strip().lower() in ("1", "true", "yes", "on")
            else:
                v = type(cur)(float(v)) if isinstance(cur, (int, float)) else v
            setattr(self, k, v)
        return self

    def as_dict(self):
        return asdict(self)
