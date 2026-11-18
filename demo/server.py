"""SARJ interruption-detector demo: a web page that plays a call and shows the real detector's decisions.

Local (presenter laptop, competition dev calls, 127.0.0.1 only):
    python demo/server.py                      # then open http://127.0.0.1:8770 (opens automatically)
    python demo/server.py --port 8771 --no-browser
    python demo/server.py --model e01=git:8f08311:my_system.py     # add another detector version

Public (hosted site, see demo/DEPLOY.md):
    python demo/server.py --public             # 0.0.0.0:$PORT, publicly licensed collections only, limits on

Three detectors, pinned by file hash so a label can never run other code: Baseline (the kit's starter detector,
bd2a3f0), P3 Hybrid (Silero VAD + caller-relative loudness, Phase 3 commit b32cfc2) and SARJ v3 (acoustics + Arabic
words layer, demo/models/sarj_v3, every file pinned). The demo shows the 24 original
competition recordings (data/dev), publicly since the competition supervisor's written permission (2026-11-17,
recorded in demo/collections.json); public mode serves a collection only with such a record and never starts with
no playable scenario (demo/DEPLOY.md). The 50 Phase 3 research scenarios stay in the repository, not in the demo.

How a decision reaches the page (nothing is pre-scripted):
  1. The browser plays the call's two channels from one AudioContext and regularly posts its playhead (for both
     detectors: each has its own session, fed the same frames).
  2. For each 20 ms frame up to the playhead (+ a small look-ahead in public mode, to absorb network delay),
     DetectorSession does what the kit's server does with the harness's messages: control messages first
     (scenario_start, agent_utterance_start/end, built by the kit's own ttbench.harness.build_stream), then the
     1,280-byte frame, parsed back with np.frombuffer, into MySystem.on_frame(agent, caller). It is the class from
     my_system.py (P3) or the pinned baseline file, imported unchanged and checked against its SHA-256.
  3. Whatever on_frame returns goes through the kit's _as_messages and validate_client_message, is stamped with the
     frame's stream time (20 * n ms, as tools/replay.py does), and is returned. The page applies each decision when
     playback reaches that stream time (mutes the agent on STOP).
Labels are read only by Scorer, after the decisions exist, through the official ttbench.score.score_scenario.
The detector never receives them, and the page never changes its input (open loop, as in the official evaluation).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import types
import uuid
import wave
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

from ttbench.dataset import Scenario, lay_out_stream, load_split  # noqa: E402
from ttbench.harness import build_stream  # noqa: E402
from ttbench.protocol import FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE, validate_client_message  # noqa: E402
from ttbench.score import DEFAULT_THRESHOLDS, score_scenario  # noqa: E402
from ttbench.server import _as_messages  # noqa: E402

import live  # noqa: E402  (demo/live.py: Gemini Live agent with SARJ-controlled barge-in)

HOST = "127.0.0.1"
DEFAULT_PORT = 8770
STATIC = HERE / "static"
REGISTER = HERE / "collections.json"        # scenario collections and their redistribution rights
BENCHMARK = HERE / "benchmark.json"         # recorded dev-set results of the two detectors (from runs.json)
SOURCES = KIT / "data" / "extra" / "sources.json"   # per-clip licence and attribution of the Phase 3 scenarios
# Fixed types: on Windows, mimetypes reads the registry, which can map .js to text/plain (Chrome would then refuse it).
STATIC_TYPES = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "media-src 'self' blob:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
                               "frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",   # microphone: "test your own audio"
}

# The two detectors of the demo, each pinned to the SHA-256 of its exact file (as recorded in
# experiments/evidence/runs.json). If a file does not match its pin the detector is disabled, so the label "P3" can
# only ever run the audited P3 code, and "Baseline" only the original starter detector.
P3_SHA256 = "8066ae363967adebb61e4bb9841d816b1d7b130bda837c66c826c5be1ef9d4bf"        # b32cfc2 (= 6b4eece)
BASELINE_SHA256 = "85a8fa0ca7c6e0f92a01fb929877cf0eff36cb0d53749cceaeaf9b6de97fd830"  # bd2a3f0 (= kit 0915ace)
# SARJ v3 (leenalotaibi-source/sarj, branch claude/sarj-turn-taking-system-xwvrh0): my_system.py plus its tt/ package,
# every file pinned. Words layer: faster-whisper tiny, loaded from demo/models/sarj_v3/models/faster-whisper-tiny
# (downloaded at image build, see demo/Dockerfile); without it the detector runs on acoustics alone and says so.
V3_SHA256 = "09e5b65a117895dc15ce75a700cb48e313cd121c7ac1738559e320606cfcd1b8"
V3_FILES = {
    "demo/models/sarj_v3/tt/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "demo/models/sarj_v3/tt/config.py": "6dfbbe3fe26e2502287545dd6a53b51f96069da1fa3e4266cf4107252b599778",
    "demo/models/sarj_v3/tt/decider.py": "5e59028e2225717d48e115f077b470fd07057794241a25fb91f71cf6e6567a80",
    "demo/models/sarj_v3/tt/frontend.py": "85a3b2715c1c12d3c4b183e8a99515d1a30a10a596eb6e43a18ac5f86cae22b7",
    "demo/models/sarj_v3/tt/prefix.py": "1559dd15e907b006132f76e3365a51632a6f65acaf32ceff3c49076eabe3daa8",
    "demo/models/sarj_v3/tt/words.py": "c77aaf471db0676c9c6653ae5e06b646bfd46dd61a59330c22dadde417b70785",
}
DEFAULT_MODELS = {
    "p3": {"source": "file:my_system.py", "sha256": P3_SHA256, "name": "P3 Hybrid", "revision": "b32cfc2",
           "about": "Phase 3: Silero VAD speech frames + caller-relative loudness (experimental)"},
    "v3": {"source": "file:demo/models/sarj_v3/my_system.py", "sha256": V3_SHA256, "files": V3_FILES,
           "name": "SARJ v3", "revision": "18471a8",
           "about": "Echo-aware acoustics, caller-relative level, leaky speech evidence + Arabic stop/keep words"},
    "baseline": {"source": "file:demo/models/baseline_bd2a3f0.py", "sha256": BASELINE_SHA256, "name": "Baseline",
                 "revision": "bd2a3f0", "about": "The kit's starter detector: any caller sound for 200 ms stops the agent"},
}
KNOWN_SOURCES = {v["sha256"]: (v["name"], v["revision"]) for v in DEFAULT_MODELS.values()}
CATEGORY_NAMES = {"GI": "Genuine interruptions", "OV": "Overlap: talking at the same time",
                  "BC": "Acknowledgements (backchannels)", "NV": "Non-speech sounds: cough, laugh, knock, echo",
                  "BG": "Background speech and noise", "HT": "Hesitation", "AM": "Ambiguous words",
                  "DV": "Dialect variation"}
CATEGORY_ORDER = ["GI", "OV", "BC", "NV", "BG", "HT", "AM", "DV"]
SUBTYPE_TEXT = {      # Phase 3 templates (tools/build_extra.py): short name, what happens
    "interruption.multiword": ("Multi-word interruption", "The caller cuts in with several stop words."),
    "interruption.single_word": ("Single-word interruption", "The caller says one stop word."),
    "interruption.backchannel_opener": ("Yes, then no", "The caller starts like an acknowledgement, then objects."),
    "interruption.soft": ("Soft interruption", "The caller objects quietly."),
    "interruption.low_gain": ("Interruption, quiet microphone", "The whole caller line is 12 dB quieter."),
    "interruption.high_gain": ("Interruption, loud microphone", "The whole caller line is 6 dB louder."),
    "interruption.low_snr": ("Interruption in heavy rain", "The caller objects over loud rain."),
    "interruption.no_prior_caller_speech": ("Interruption before the caller has spoken",
                                            "The caller's first words are the interruption: no earlier speech to compare with."),
    "interruption.over_echo": ("Interruption over the agent's echo", "The agent's own voice leaks back into the caller line."),
    "overlap.simultaneous_start": ("Both start at once", "The caller starts objecting at the same moment the agent starts."),
    "overlap.talk_over": ("Caller talks over the agent", "A long, loud objection over the agent."),
    "backchannel.single_soft": ("Soft «نعم»", "One quiet acknowledgement while the agent talks."),
    "backchannel.repeated": ("Repeated «نعم»", "Three quiet acknowledgements on a telephone line."),
    "backchannel.normal_level": ("«نعم» at normal loudness", "One acknowledgement, as loud as the caller's greeting."),
    "backchannel.double": ("«نعم نعم» in the rain", "A double acknowledgement over rain."),
    "backchannel.low_gain": ("«موافق», quiet microphone", "An acknowledgement on a line 12 dB quieter."),
    "backchannel.high_gain": ("«نعم», loud microphone", "An acknowledgement on a line 6 dB louder."),
    "background.tv": ("Television in the background", "Broadcast-like speech in the room; the caller says nothing."),
    "background.horn": ("Car horn in traffic", "A horn over engine noise; the caller says nothing."),
    "background.side_speech": ("Someone else talking", "Another person speaks in the room, away from the phone."),
    "background.abrupt_noise": ("Sudden noise", "A loud noise starts suddenly; the caller says nothing."),
    "nonverbal.cough": ("Cough", "The caller coughs; nothing is said."),
    "nonverbal.laugh": ("Laugh", "The caller laughs; nothing is said."),
    "nonverbal.echo": ("Agent's own echo", "The agent's voice leaks back into the caller line; the caller is silent."),
    "nonverbal.transient": ("Knock on a door", "A knock near the caller; nothing is said."),
}


@dataclass
class Settings:
    """Local defaults: one presenter on localhost. for_public() tightens everything for an open URL."""
    public: bool = False
    host: str = HOST
    port: int = DEFAULT_PORT
    collections: tuple = ("dev",)       # keys in demo/collections.json: the demo shows the 24 competition recordings only
    data_dir: Path | None = None        # instead of collections: one folder (tests, ad-hoc local use)
    max_sessions: int = 24              # detector sessions held in memory (each playback runs three: Baseline, P3, v3)
    evict_idle_s: float = 0.0           # at capacity, an older session may be dropped once idle this long
    session_idle_s: float = 3600.0      # sessions idle longer than this are dropped
    lookahead_ms: int = 0               # feed the detector this far ahead of playback (hides network delay)
    poll_ms: int = 15                   # how often the page posts its playhead
    sessions_per_min: float = 0.0       # new sessions per minute, all visitors together (0 = unlimited)
    advance_per_s: float = 0.0          # playhead posts per second per session (0 = unlimited)
    pace: float = 0.0                   # max stream ms fed per wall-clock ms since the session began (0 = unlimited)
    max_body: int = 65536
    max_connections: int = 64
    custom_max_s: float = 60.0          # "test your own audio": longest caller recording accepted
    custom_max: int = 20                # own-audio scenarios kept in memory at once (never on disk)
    custom_ttl_s: float = 900.0         # ... and for at most this long
    custom_per_min: float = 0.0         # new own-audio scenarios per minute, all visitors together (0 = unlimited)

    @classmethod
    def for_public(cls, port: int | None = None, max_sessions: int | None = None) -> "Settings":
        env = os.environ
        return cls(public=True, host="0.0.0.0", port=port or int(env.get("PORT", "10000")),
                   collections=("dev",),   # served only once public hosting is authorised (demo/collections.json)
                   max_sessions=max_sessions or int(env.get("SARJ_DEMO_MAX_SESSIONS", "16")), evict_idle_s=60.0,
                   session_idle_s=600.0, lookahead_ms=800, poll_ms=100, sessions_per_min=80.0, advance_per_s=20.0,
                   pace=1.5, max_body=2048, max_connections=64, custom_per_min=20.0)


class Refused(Exception):
    """A request the public limits turn away (HTTP 429/503), with a message the page can show."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float):
        self.rate, self.burst, self.tokens, self.t = rate_per_s, burst, burst, time.monotonic()
        self.lock = threading.Lock()

    def take(self) -> bool:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
            self.t = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


# ------------------------------------------------------------------------------------------------ detector versions
def _git(*args: str, raw: bool = False) -> str | bytes | None:
    try:
        r = subprocess.run(["git", *args], cwd=KIT, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout if raw else r.stdout.decode("utf-8", "replace")


class Model:
    """One detector version: the unmodified source of a system file, imported as its own module.

    With a pinned sha256 the file must match it exactly, or the detector is unavailable: a label can never run
    different code than it names."""

    def __init__(self, model_id: str, source: str | dict, sha256: str | None = None, name: str | None = None):
        if isinstance(source, dict):
            spec = source
            source, sha256 = spec["source"], spec.get("sha256", sha256)
            name, self.about, pinned_rev = spec.get("name", name), spec.get("about", ""), spec.get("revision")
            extra_files = spec.get("files", {})
        else:
            self.about, pinned_rev, extra_files = "", None, {}
        self.id, self.source, self.pinned = model_id, source, sha256
        self.available, self.error = False, None
        self.revision, self.dirty, self.name, self.sha256 = None, False, name or model_id, None
        self._cls = None
        try:
            raw, origin = self._read(source)
            self.sha256 = hashlib.sha256(raw).hexdigest()       # of the exact file bytes, as in runs.json
            if sha256 and self.sha256 != sha256:
                raise RuntimeError(f"{source} is not the pinned {self.name} file (sha256 {self.sha256[:12]}…, "
                                   f"expected {sha256[:12]}…)")
            for rel, want in extra_files.items():     # a multi-file detector: every module it imports is pinned too
                got = hashlib.sha256((KIT / rel).read_bytes()).hexdigest()
                if got != want:
                    raise RuntimeError(f"{rel} is not the pinned {self.name} file (sha256 {got[:12]}…, expected {want[:12]}…)")
            code = raw.decode("utf-8")
            module = types.ModuleType(f"sarj_demo_model_{re.sub(r'[^0-9A-Za-z_]', '_', model_id)}")
            module.__file__ = origin
            exec(compile(code, origin, "exec"), module.__dict__)  # noqa: S102 - our own repository's system file
            self._cls = module.MySystem
            first = self._cls()             # load any model (P3: the Silero ONNX session) now, not mid-demo
            note = getattr(first, "demo_note", "")
            if note:                        # e.g. SARJ v3 reports whether its words layer really loaded
                self.about = f"{self.about} ({note})" if self.about else note
            self.available = True
        except Exception as e:  # noqa: BLE001 - shown in the page instead of crashing the demo
            self.error = f"{type(e).__name__}: {e}"
        known = KNOWN_SOURCES.get(self.sha256 or "")
        if known:
            self.name, self.revision = known
        elif pinned_rev:
            self.revision = pinned_rev
        elif source.startswith("file:"):
            self.revision = self.revision or (self.sha256 or "")[:7] or None

    def _read(self, source: str) -> tuple[bytes, str]:
        kind, _, rest = source.partition(":")
        if kind == "file":
            path = (KIT / rest).resolve()
            self.dirty = bool(_git("status", "--porcelain", "--", rest) or "")
            return path.read_bytes(), str(path)
        if kind == "git":
            rev, _, path = rest.partition(":")
            code = _git("show", f"{rev}:{path}", raw=True)
            if code is None:
                raise RuntimeError(f"`git show {rev}:{path}` failed (is git installed and the commit present?)")
            full = _git("rev-parse", "--short", rev)
            self.revision = full.strip() if full else rev
            return code, f"{rev}:{path}"
        raise ValueError(f"unknown model source {source!r}: use file:<path> or git:<rev>:<path>")

    def new_system(self):
        return self._cls()

    def describe(self, public: bool = False) -> dict:
        d = {"id": self.id, "name": self.name, "revision": self.revision, "about": self.about,
             "dirty": self.dirty, "available": self.available}
        if not public:                   # file paths, hashes and errors stay off the public site
            d.update(source=self.source, sha256=self.sha256, error=self.error)
        return d


# ------------------------------------------------------------------------------------------------ data
def _clip_credits() -> dict:
    try:
        return json.loads(SOURCES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def describe_scenario(label: dict, design: dict | None) -> tuple[str, str]:
    """A short name and a one-paragraph explanation, from the scenario's own label and design record."""
    sub = label.get("subtype", "")
    title, text = SUBTYPE_TEXT.get(sub, (sub.split(".")[-1].replace("_", " ").capitalize() or label["scenario_id"], ""))
    segs = {s["seg_id"]: s for s in label.get("user_segments", [])}
    parts = [text] if text else []
    for ev in label["events"]:
        words = [segs[i].get("text") for i in ev.get("segment_ids", []) if segs.get(i, {}).get("text")]
        if words:
            parts.append(f"Caller: «{' … '.join(words)}».")
            break
    if design:
        if design.get("rel_level_db"):
            r = design["rel_level_db"]
            parts.append(f"Spoken {abs(r)} dB {'quieter' if r < 0 else 'louder'} than the caller's own greeting.")
        if design.get("mic_gain_db") and "gain" not in sub:
            parts.append(f"Caller microphone {design['mic_gain_db']:+d} dB.")
        if design.get("telephone_band"):
            parts.append("Telephone band (300–3400 Hz).")
        if design.get("bed"):
            parts.append(f"{design['bed'][0].capitalize()} noise underneath (SNR {design['bed'][1]} dB).")
        if design.get("echo"):
            e = design["echo"]
            parts.append(f"Agent echo at {e['level_db']:.0f} dB, {e['delay_ms']} ms delay.")
    elif not text:
        parts += [ev.get("rationale", "") for ev in label["events"][:1]]
    expected = sorted({ev["expected"] for ev in label["events"]})
    meaning = {"STOP": "STOP (a genuine interruption: the agent should yield)",
               "KEEP": "KEEP (the agent should keep talking)", "EITHER": "EITHER (ambiguous: both acceptable)",
               "TAKE_TURN": "TAKE_TURN (end-of-turn track)"}
    parts.append("Expected: " + "; ".join(meaning.get(x, x) for x in expected) + ".")
    return title, " ".join(p for p in parts if p)


class DevData:
    """One scenario collection (manifest + labels + stereo WAVs), read once. Labels stay server-side except through
    timeline(), which keeps ground truth apart from what the detector is told."""

    def __init__(self, data_dir: Path, meta: dict | None = None):
        self.dir = Path(data_dir)
        self.meta = meta or {"key": self.dir.name, "title": self.dir.name, "public": False}
        self.key = self.meta["key"]
        self.manifest = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))
        self.thresholds = {**DEFAULT_THRESHOLDS, **self.manifest.get("thresholds", {})}
        self.scenarios = {s.scenario_id: s for s in load_split(self.dir)}
        design = self.dir / "design.json"
        self.design = ({d["scenario_id"]: d for d in json.loads(design.read_text(encoding="utf-8"))}
                       if design.is_file() else {})

    def info(self, public: bool = False) -> dict:
        m = self.meta
        return {"key": self.key, "title": m.get("title"), "kind": m.get("kind", ""), "description": m.get("description", ""),
                "rights": m.get("rights", ""), "public": bool(m.get("public")), "count": len(self.scenarios),
                "label_status": self.manifest.get("label_status", "script_derived")}

    def summary(self) -> list[dict]:
        out = []
        for sid, s in self.scenarios.items():
            lab = s.label
            title, explanation = describe_scenario(lab, self.design.get(sid))
            out.append({"id": sid, "collection": self.key, "title": title, "explanation": explanation,
                        "category": lab["category"], "category_name": CATEGORY_NAMES.get(lab["category"], ""),
                        "subtype": lab.get("subtype", ""), "difficulty": lab.get("difficulty"),
                        "dialect": lab.get("dialect", ""), "acoustic": lab.get("acoustic", {}).get("conditions", []),
                        "use_case": lab["context"]["use_case"], "duration_ms": s.duration_ms,
                        "expected": [e["expected"] for e in lab["events"]]})
        return out

    def audio_path(self, sid: str) -> Path:
        return self.dir / "audio" / self.scenarios[sid].label["audio"]["file"]

    def credits(self, sid: str) -> list[dict]:
        """Per-clip attribution of the sounds mixed into this scenario (Phase 3 collections)."""
        lab = self.scenarios[sid].label
        if not self.design:
            return []
        clips, out = _clip_credits(), []
        for src in lab.get("acoustic", {}).get("noise_source_ids", []):
            if src.startswith("ESC50-"):
                c = clips.get("esc:" + src[len("ESC50-"):], {})
                out.append({"what": "sound clip", "attribution": c.get("attribution", src), "license": c.get("licence", "?")})
        out.append({"what": "voices", "attribution": "Arabic Speech Commands Dataset (Ghandoura, Hjabo, Al Dakkak, 2021): "
                    "real speakers reading isolated keywords; trimmed, concatenated and mixed", "license": "CC BY 4.0"})
        return out

    def timeline(self, sid: str) -> dict:
        """Protocol inputs (what the detector is told) kept apart from ground truth (what it is never told)."""
        s = self.scenarios[sid]
        lab = s.label
        return {
            "id": sid, "duration_ms": s.duration_ms, "collection": self.key, "credits": self.credits(sid),
            "protocol": {"context": {"use_case": lab["context"]["use_case"], "language": "ar"},
                         "agent_utterances": [{"utt_id": u["utt_id"], "start_ms": u["start_ms"],
                                               "end_ms": u["end_ms"], "text": u["text"]}
                                              for u in lab["agent_utterances"]]},
            "ground_truth": {"category": lab["category"], "subtype": lab.get("subtype", ""),
                             "label_status": lab.get("provenance", {}).get("label_status", ""),
                             "events": lab["events"], "user_segments": lab.get("user_segments", []),
                             "thresholds": self.thresholds},
        }


def load_register() -> dict:
    return json.loads(REGISTER.read_text(encoding="utf-8"))


def check_public_collection(col: DevData, register: dict) -> None:
    """The public site serves a collection only if the register marks it public, it is not the kit's competition
    data, and every WAV in it is byte-identical to a reviewed file (SHA-256 listed in the register)."""
    kit = (KIT / "data" / "dev").resolve() in (col.dir.resolve(), *col.dir.resolve().parents) \
        or col.manifest.get("dataset") == "sarj-turn-taking-ar"
    if kit and not (col.meta.get("public") and col.meta.get("authorization")):
        raise SystemExit("refusing to serve the competition recordings publicly: public web hosting is not authorised. "
                         "Record the organisers' written permission in demo/collections.json (dev: public, authorization); "
                         "see demo/DEPLOY.md")
    if not col.meta.get("public"):
        raise SystemExit(f"refusing to serve {col.dir} publicly: not marked public in demo/collections.json")
    allowed = dict(register.get("public_wav_sha256", {}))
    if kit:                             # authorised: exactly the kit's own files, as listed in its manifest
        allowed.update({e["scenario_id"]: e["wav_sha256"] for e in col.manifest["scenarios"]})
    for sid in col.scenarios:
        digest = hashlib.sha256(col.audio_path(sid).read_bytes()).hexdigest()
        if allowed.get(sid) != digest:
            raise SystemExit(f"refusing to serve {sid} publicly: its audio is not a reviewed file")


class Catalog:
    """All collections the server offers, with scenario ids unique across them."""

    def __init__(self, settings: Settings):
        self.collections: list[DevData] = []
        if settings.data_dir is not None:
            if settings.public:
                raise SystemExit("public mode serves only the collections in demo/collections.json")
            self.collections.append(DevData(settings.data_dir))
        else:
            register = load_register()
            metas = {c["key"]: c for c in register["collections"]}
            for key in settings.collections:
                meta = metas[key]
                folder = KIT / meta["dir"]
                if not settings.public and not (folder / "audio").is_dir():
                    continue                    # e.g. Phase 3 audio not built on this machine
                col = DevData(folder, meta)
                if settings.public:
                    check_public_collection(col, register)
                self.collections.append(col)
        self.by_id: dict[str, DevData] = {}
        for col in self.collections:
            for sid in col.scenarios:
                if sid in self.by_id:
                    raise SystemExit(f"scenario id {sid} appears in two collections")
                self.by_id[sid] = col
        self.scenarios = {sid: col.scenarios[sid] for sid, col in self.by_id.items()}
        if settings.public and not self.scenarios:
            raise SystemExit("refusing to start a public demo with no playable scenario")

    def thresholds(self, sid: str) -> dict:
        return self.by_id[sid].thresholds

    def audio_path(self, sid: str) -> Path:
        return self.by_id[sid].audio_path(sid)

    def timeline(self, sid: str) -> dict:
        return self.by_id[sid].timeline(sid)

    def summary(self) -> list[dict]:
        rows = [r for col in self.collections for r in col.summary()]
        order = {c.key: i for i, c in enumerate(self.collections)}
        cat = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        return sorted(rows, key=lambda r: (order[r["collection"]], cat.get(r["category"], 99), r["id"]))


def benchmark() -> dict | None:
    """Recorded dev-set results of the two detectors (demo/benchmark.json, copied from runs.json; tested)."""
    try:
        return json.loads(BENCHMARK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ one playback
class DetectorSession:
    """Feeds one scenario to a fresh detector, frame by frame, exactly as the kit's harness + server would.

    Only build_stream's output reaches the detector: the same control messages, in the same order, before the same
    frames. advance(to_ms) never goes backwards, so pausing (no calls) or polling in uneven steps cannot change what
    the detector sees; a reset is a new session.
    """

    def __init__(self, model: Model, scenario):
        laid = lay_out_stream([scenario])            # one call, starting at stream time 0
        self.stream, self.controls = build_stream(laid)
        self.n_frames = len(self.stream) // FRAME_SAMPLES
        self.system = model.new_system()
        self.next_frame = 0
        self.current_utt: str | None = None
        self.decisions: list[dict] = []
        self.errors: list[dict] = []
        self.frame_ns = 0
        self.failed = False
        self.lock = threading.Lock()

    @property
    def fed_ms(self) -> int:
        return self.next_frame * FRAME_MS

    @property
    def done(self) -> bool:
        return self.next_frame >= self.n_frames or self.failed

    def advance(self, to_ms: float) -> tuple[list[dict], list[dict]]:
        """Send every frame whose start time is <= to_ms (the harness sends frame n at 20 n ms)."""
        new_decisions, protocol = [], []
        with self.lock:
            last = min(self.n_frames - 1, int(to_ms // FRAME_MS))
            while self.next_frame <= last and not self.failed:
                n = self.next_frame
                for msg in self.controls.get(n, []):
                    protocol.append({k: v for k, v in msg.items() if k != "text"})
                    self._control(msg)
                raw = self.stream[n * FRAME_SAMPLES:(n + 1) * FRAME_SAMPLES].astype("<i2").tobytes()
                frame = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)      # as ttbench.server parses it
                t0 = time.perf_counter_ns()
                try:
                    out = self.system.on_frame(frame[:, 0], frame[:, 1])
                except Exception as e:  # noqa: BLE001 - a crashing detector ends the session, visibly
                    self.failed = True
                    self.errors.append({"stream_ms": n * FRAME_MS, "error": f"on_frame raised {type(e).__name__}: {e}"})
                    break
                dt = time.perf_counter_ns() - t0
                self.frame_ns += dt
                self.next_frame += 1
                try:
                    msgs = _as_messages(out)
                except ValueError as e:
                    self.errors.append({"stream_ms": n * FRAME_MS, "error": str(e)})
                    continue
                for m in msgs:
                    err = validate_client_message(m)
                    if err:
                        self.errors.append({"stream_ms": n * FRAME_MS, "error": err})
                        continue
                    d = {"stream_ms": n * FRAME_MS, "frame": n, "decision": m["decision"],
                         "utt_id": self.current_utt, "on_frame_us": round(dt / 1000, 1),
                         **{k: m[k] for k in ("label", "confidence") if k in m}}
                    self.decisions.append(d)
                    new_decisions.append(d)
        return new_decisions, protocol

    def _control(self, msg: dict) -> None:
        t = msg["type"]                                  # the calls ttbench.server makes, with the same arguments
        if t == "scenario_start":
            self.system.on_scenario_start(msg.get("context", {}))
        elif t == "agent_utterance_start":
            self.current_utt = msg["utt_id"]
            self.system.on_agent_start(msg.get("text", ""), msg.get("duration_ms", 0))
        elif t == "agent_utterance_end":
            self.current_utt = None
            self.system.on_agent_end()

    def mean_on_frame_us(self) -> float:
        return round(self.frame_ns / max(1, self.next_frame) / 1000, 1)


class Scorer:
    """Official per-scenario scoring of the decisions made so far. An event is marked closed only once nothing later
    in the stream can change its outcome; the page additionally waits for playback to reach close_ms."""

    def __init__(self, label: dict, thresholds: dict):
        self.label, self.th = label, thresholds

    def close_ms(self, ev: dict) -> float:
        """Stream time after which no later decision can change this event's outcome under score_scenario:
        a yield must start by the deadline and must not resume before the caller's offset; a KEEP window ends at
        offset + keep_tail, and a stop inside it is judged by a RESUME up to hiccup_ms later."""
        th = self.th
        if ev["kind"] != "interruption":
            return ev.get("turn_end_ms", ev["onset_ms"]) + th["E_miss_ms"]
        offset = ev.get("offset_ms", ev["onset_ms"])
        ends = [offset]
        if ev["expected"] in ("STOP", "EITHER"):
            ends.append(ev.get("evident_ms", ev["onset_ms"]) + th["T_dead_ms"])
        if ev["expected"] in ("KEEP", "EITHER"):
            ends.append(offset + th["keep_tail_ms"] + th["hiccup_ms"])
        return max(ends)

    def evaluate(self, decisions: list[dict], fed_ms: float, done: bool) -> dict:
        result = score_scenario(self.label, [(d["stream_ms"], d["decision"]) for d in decisions], self.th)
        events = []
        for ev, r in zip(self.label["events"], result["events"]):
            closed = done or fed_ms >= self.close_ms(ev)
            events.append({"event_id": ev["event_id"], "closed": closed, "close_ms": self.close_ms(ev),
                           **({k: r.get(k) for k in ("outcome", "behaviour", "latency_ms", "credit", "detail")}
                              if closed else {})})
        out = {"events": events}
        if done:
            out["protected"] = result["protected"]
            out["ignored_decisions"] = result["ignored_decisions"]
        return out


# ------------------------------------------------------------------------------------------------ application
@dataclass
class Entry:
    sess: DetectorSession
    scorer: Scorer
    created: float
    last_seen: float
    bucket: TokenBucket | None = field(default=None)


class DemoApp:
    def __init__(self, data_dir: Path | None, model_specs: dict, settings: Settings | None = None):
        self.settings = settings or Settings()
        if data_dir is not None:
            self.settings.data_dir = Path(data_dir)
        self.data = Catalog(self.settings)
        self.models = {mid: Model(mid, src) for mid, src in model_specs.items()}   # loaded before serving
        self.sessions: dict[str, Entry] = {}
        self.lock = threading.Lock()
        s = self.settings
        self.create_bucket = TokenBucket(s.sessions_per_min / 60, max(5.0, s.sessions_per_min / 3)) \
            if s.sessions_per_min else None
        # "Test your own audio": scenarios built from a visitor's recording, kept in memory only (never written to
        # disk or logged), under an unguessable id, and dropped after custom_ttl_s.
        self.custom: dict[str, tuple] = {}
        # Live AI conversation (demo/live.py): configured from the environment; disabled without GEMINI_API_KEY.
        self.live_settings = live.LiveSettings.from_env()
        self.live_gate = live.LiveGate(self.live_settings)
        self.custom_bucket = TokenBucket(s.custom_per_min / 60, max(3.0, s.custom_per_min / 4)) \
            if s.custom_per_min else None

    # --- scenarios: the catalog plus the visitor's own-audio scenarios -----------------------------------------
    def _purge_custom(self):
        now = time.monotonic()
        for cid in [k for k, v in self.custom.items() if now - v[2] > self.settings.custom_ttl_s]:
            del self.custom[cid]

    def scenario(self, sid: str):
        with self.lock:
            self._purge_custom()
            if sid in self.custom:
                return self.custom[sid][0]
        if sid in self.data.scenarios:
            return self.data.scenarios[sid]
        raise KeyError(f"unknown scenario {sid!r}")

    def thresholds(self, sid: str) -> dict:
        return self.data.thresholds(sid) if sid in self.data.scenarios else dict(DEFAULT_THRESHOLDS)

    def timeline(self, sid: str) -> dict:
        scen = self.scenario(sid)
        if sid in self.data.scenarios:
            return self.data.timeline(sid)
        lab = scen.label
        return {"id": sid, "duration_ms": scen.duration_ms, "collection": "custom", "credits": [],
                "custom": lab["custom"],
                "protocol": {"context": {"use_case": lab["context"]["use_case"], "language": "ar"},
                             "agent_utterances": [{k: u[k] for k in ("utt_id", "start_ms", "end_ms", "text")}
                                                  for u in lab["agent_utterances"]]},
                "ground_truth": {"category": "", "subtype": "your own audio", "label_status": "none", "events": [],
                                 "user_segments": [], "thresholds": self.thresholds(sid)}}

    def audio_bytes(self, sid: str) -> bytes:
        self.scenario(sid)
        with self.lock:
            if sid in self.custom:
                return self.custom[sid][1]
        return self.data.audio_path(sid).read_bytes()

    def create_custom(self, agent_sid: str, offset_ms: float, pcm: bytes) -> dict:
        """A scenario whose agent channel and agent timing come from a catalog scenario and whose caller channel is
        the visitor's audio (16 kHz mono int16 little-endian), starting offset_ms into the call."""
        s = self.settings
        if agent_sid not in self.data.scenarios:
            raise KeyError(f"unknown scenario {agent_sid!r}")
        if len(pcm) % 2 or len(pcm) < 2 * FRAME_SAMPLES * 10:
            raise ValueError("expected at least 0.2 s of 16-bit mono audio")
        if len(pcm) > s.custom_max_s * SAMPLE_RATE * 2:
            raise ValueError(f"audio longer than {s.custom_max_s:.0f} s")
        if not np.isfinite(offset_ms):
            raise ValueError("offset_ms must be finite")
        if self.custom_bucket and not self.custom_bucket.take():
            raise Refused(429, "Many people are testing their own audio right now. Please try again in a minute.")
        base = self.data.scenarios[agent_sid]
        caller = np.frombuffer(pcm, dtype="<i2")
        off = int(min(max(0.0, offset_ms), base.duration_ms)) * SAMPLE_RATE // 1000
        n = max(len(base.audio), off + len(caller))
        n += (-n) % FRAME_SAMPLES
        audio = np.zeros((n, 2), dtype=np.int16)
        audio[:len(base.audio), 0] = base.audio[:, 0]          # the agent: original channel, original timing
        audio[off:off + len(caller), 1] = caller              # the caller: the visitor's audio
        cid = "U" + uuid.uuid4().hex
        info = {"agent_from": agent_sid, "offset_ms": off * 1000 // SAMPLE_RATE,
                "caller_ms": len(caller) * 1000 // SAMPLE_RATE}
        label = {**base.label, "scenario_id": cid, "subtype": "custom", "events": [], "user_segments": [],
                 "background_events": [], "custom": info,
                 "audio": {**base.label["audio"], "file": None, "duration_ms": n * 1000 // SAMPLE_RATE}}
        scen = Scenario(cid, label, audio)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:                        # in memory only
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(audio.astype("<i2").tobytes())
        with self.lock:
            self._purge_custom()
            while len(self.custom) >= s.custom_max:
                del self.custom[min(self.custom, key=lambda k: self.custom[k][2])]
            self.custom[cid] = (scen, buf.getvalue(), time.monotonic())
        return {"scenario_id": cid, "duration_ms": scen.duration_ms, **info}

    def config(self) -> dict:
        s = self.settings
        register = load_register() if REGISTER.is_file() else {}
        shown = {c.key for c in self.data.collections}
        credits = [a for a in register.get("attribution", []) if not a.get("applies_to") or set(a["applies_to"]) & shown]
        return {"mode": "public" if s.public else "local", "frame_ms": FRAME_MS,
                "lookahead_ms": s.lookahead_ms, "poll_ms": s.poll_ms,
                "collections": [c.info(public=s.public) for c in self.data.collections],
                "attribution": credits,
                "models": [m.describe(public=s.public) for m in self.models.values()],
                "scenarios": self.data.summary(), "benchmark": self.public_benchmark() if s.public else benchmark(),
                "custom": {"max_s": s.custom_max_s, "ttl_s": s.custom_ttl_s},
                "live": self.live_gate.status()}

    @staticmethod
    def public_benchmark() -> dict | None:
        b = benchmark()
        if b:
            b["rows"] = [{k: v for k, v in r.items() if k != "my_system_sha256"} for r in b["rows"]]
        return b

    def new_session(self, scenario_id: str, model_id: str) -> dict:
        s = self.settings
        scen = self.scenario(scenario_id)
        model = self.models.get(model_id)
        if model is None or not model.available:
            raise KeyError(f"model {model_id!r} is not available")
        if self.create_bucket and not self.create_bucket.take():
            raise Refused(429, "Many people are starting the demo right now. Please try again in a few seconds.")
        now = time.monotonic()
        with self.lock:
            for sid in [k for k, e in self.sessions.items() if now - e.last_seen > s.session_idle_s]:
                del self.sessions[sid]
            if len(self.sessions) >= s.max_sessions:
                lru_id, lru = min(self.sessions.items(), key=lambda kv: kv[1].last_seen)
                if now - lru.last_seen < s.evict_idle_s:
                    raise Refused(503, f"The demo is at capacity ({s.max_sessions} simultaneous plays). "
                                       "Please try again in a minute.")
                del self.sessions[lru_id]
            sess = DetectorSession(model, scen)
            sid = uuid.uuid4().hex
            bucket = TokenBucket(s.advance_per_s, 2 * s.advance_per_s) if s.advance_per_s else None
            self.sessions[sid] = Entry(sess, Scorer(scen.label, self.thresholds(scenario_id)), now, now, bucket)
        return {"session_id": sid, "model": model.describe(public=s.public), "scenario_id": scenario_id,
                "stream_end_ms": sess.n_frames * FRAME_MS}

    def close_session(self, session_id: str) -> dict:
        with self.lock:
            self.sessions.pop(session_id, None)
        return {"closed": True}

    def advance(self, session_id: str, to_ms: float) -> dict:
        s = self.settings
        with self.lock:
            entry = self.sessions.get(session_id)
        if entry is None:
            raise KeyError("unknown or expired session")
        if entry.bucket and not entry.bucket.take():
            raise Refused(429, "too many requests for this session")
        now = time.monotonic()
        entry.last_seen = now
        if s.pace:                      # a session cannot run the detector much faster than real time
            to_ms = min(to_ms, (now - entry.created) * 1000 * s.pace + s.lookahead_ms + 1000)
        sess = entry.sess
        t0 = time.perf_counter_ns()
        new, protocol = sess.advance(float(to_ms))
        server_ms = (time.perf_counter_ns() - t0) / 1e6
        return {"session_id": session_id, "fed_ms": sess.fed_ms, "done": sess.done, "decisions": new,
                "protocol": protocol, "errors": sess.errors, "server_ms": round(server_ms, 3),
                "mean_on_frame_us": sess.mean_on_frame_us(),
                "score": entry.scorer.evaluate(sess.decisions, sess.fed_ms, sess.done)}


# ------------------------------------------------------------------------------------------------ HTTP
def make_handler(app: DemoApp):
    public = app.settings.public
    audio_cache = "public, max-age=86400" if public else "private, max-age=600"

    class Handler(BaseHTTPRequestHandler):
        server_version = "sarj-demo/1"
        sys_version = ""
        protocol_version = "HTTP/1.1"     # keep-alive: the page polls often; one connection instead of thousands
        disable_nagle_algorithm = True    # headers and body are separate writes: avoid ~40 ms Nagle/delayed-ACK stalls
        timeout = 30                      # idle keep-alive connections are closed after 30 s
        head_only = False

        def log_message(self, fmt, *args):            # keep the console readable (and free of visitor details)
            pass

        def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for k, v in SECURITY_HEADERS.items():
                if k == "Content-Security-Policy":       # the live agent's WebSocket: this host only
                    host = re.sub(r"[^A-Za-z0-9.:\-\[\]]", "", self.headers.get("Host", ""))
                    v = v.replace("connect-src 'self'", f"connect-src 'self' wss://{host} ws://{host}" if host else
                                  "connect-src 'self'")
                self.send_header(k, v)
            if public:
                self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.end_headers()
            if not self.head_only:
                self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _error(self, status: int, message: str):
            self._json({"error": message}, status)

        def do_HEAD(self):  # noqa: N802
            self.head_only = True
            try:
                self.do_GET()
            finally:
                self.head_only = False

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/live":
                return self._live()
            if path == "/api/live/check":                # model availability, at most once per 30 s, no secrets
                g = app.live_gate
                if not app.live_settings.api_key:          # names only, to diagnose a misnamed variable
                    return self._json({"ok": False, "error": g.status()["reason"],
                                       "similar_variable_names": live.similar_key_names(os.environ)})
                if app.live_settings.mode == "off":
                    return self._json({"ok": False, "error": "switched off"})
                if time.monotonic() - g.last_check < 30:
                    return self._error(429, "checked less than 30 s ago")
                g.last_check = time.monotonic()
                return self._json({**g.check(), "key_variable": app.live_settings.key_variable})
            if path == "/healthz":
                return self._json({"ok": True})
            if path == "/robots.txt":
                return self._send(200, b"User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
            if path == "/api/config":
                return self._json(app.config())
            m = re.fullmatch(r"/api/scenario/([A-Za-z0-9_-]{1,64})", path)
            if m:
                try:
                    return self._json(app.timeline(m.group(1)))
                except KeyError:
                    return self._error(404, "unknown scenario")
            m = re.fullmatch(r"/api/audio/([A-Za-z0-9_-]{1,64})\.wav", path)
            if m:
                try:
                    body = app.audio_bytes(m.group(1))
                except KeyError:
                    return self._error(404, "unknown scenario")
                own = m.group(1) not in app.data.scenarios
                return self._send(200, body, "audio/wav", "no-store" if own else audio_cache)
            name = "index.html" if path == "/" else path.lstrip("/")
            m = re.fullmatch(r"[a-z_]+\.(html|js|css)", name)
            if m and (STATIC / name).is_file():
                return self._send(200, (STATIC / name).read_bytes(), STATIC_TYPES[m.group(1)], "no-cache")
            return self._error(404, "not found")

        def _live(self):
            """WebSocket for the live agent (same-origin only)."""
            h = self.headers
            origin_host = urlparse(h.get("Origin", "")).netloc
            if h.get("Upgrade", "").lower() != "websocket" or not h.get("Sec-WebSocket-Key") \
                    or h.get("Sec-WebSocket-Version") != "13":
                return self._error(400, "WebSocket upgrade expected")
            if not origin_host or origin_host != h.get("Host"):
                return self._error(403, "cross-origin WebSocket refused")
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", live.ServerWebSocket.accept_key(h["Sec-WebSocket-Key"]))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            ws = live.ServerWebSocket(self.rfile, self.wfile)
            live.serve_live(ws, app.models, app.live_gate, app.live_settings)

        def do_POST(self):  # noqa: N802
            url = urlparse(self.path)
            path = url.path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if path == "/api/custom":                       # raw 16 kHz mono int16 audio, kept in memory only
                    limit = int(app.settings.custom_max_s * SAMPLE_RATE * 2)
                    if length < 0 or length > limit:
                        self.close_connection = True
                        return self._error(413, f"audio longer than {app.settings.custom_max_s:.0f} s")
                    q = parse_qs(url.query)
                    pcm = self.rfile.read(length)
                    return self._json(app.create_custom(q.get("agent", [""])[0],
                                                        float(q.get("offset_ms", ["0"])[0]), pcm))
                if length < 0 or length > app.settings.max_body:
                    self.close_connection = True               # the unread body must not become the next request
                    return self._error(413, "request too large")
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    return self._error(400, "expected a JSON object")
                if path == "/api/session":
                    return self._json(app.new_session(str(body.get("scenario")), str(body.get("model"))))
                m = re.fullmatch(r"/api/session/([0-9a-f]{32})/(advance|close)", path)
                if m and m.group(2) == "close":
                    return self._json(app.close_session(m.group(1)))
                if m:
                    to_ms = float(body.get("to_ms"))
                    if not np.isfinite(to_ms):
                        return self._error(400, "to_ms must be finite")
                    return self._json(app.advance(m.group(1), to_ms))
                return self._error(404, "not found")
            except Refused as e:
                return self._error(e.status, str(e))
            except KeyError as e:
                return self._error(404, str(e.args[0]) if e.args else "not found")
            except (TypeError, ValueError) as e:
                return self._error(400, str(e))

    return Handler


class DemoServer(ThreadingHTTPServer):
    """Threaded HTTP server with a cap on simultaneous connections (excess connections are closed at once)."""
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler, max_connections: int):
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(app: DemoApp, port: int | None = None) -> DemoServer:
    s = app.settings
    return DemoServer((s.host, s.port if port is None else port), make_handler(app), s.max_connections)


def parse_models(extra: list[str]) -> dict[str, str]:
    models = dict(DEFAULT_MODELS)
    for item in extra:
        mid, sep, src = item.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", mid):
            raise SystemExit(f"--model expects ID=SOURCE, e.g. e01=git:8f08311:my_system.py (got {item!r})")
        models[mid] = src
    return models


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--public", action="store_true", default=os.environ.get("SARJ_DEMO_PUBLIC") == "1",
                   help="hosted mode: 0.0.0.0:$PORT, public demo scenarios only, limits on (also SARJ_DEMO_PUBLIC=1)")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--data", type=Path, default=None, help="scenario folder (public mode: must be publicly licensed)")
    p.add_argument("--model", action="append", default=[], metavar="ID=SOURCE",
                   help="extra detector version: file:<path> or git:<rev>:<path> (repeatable)")
    p.add_argument("--no-browser", action="store_true", help="do not open the page automatically")
    a = p.parse_args(argv)

    settings = Settings.for_public(a.port) if a.public else Settings(port=a.port or DEFAULT_PORT)
    if a.public and a.data is not None:
        raise SystemExit("--data is local-only; the public site serves the collections in demo/collections.json")
    app = DemoApp(a.data, parse_models(a.model), settings)
    for m in app.models.values():
        d = m.describe()
        state = f"{d['name']} @ {d['revision']}{' (uncommitted changes)' if d['dirty'] else ''}" if m.available \
            else f"UNAVAILABLE: {m.error}"
        print(f"  model {m.id:<10} {m.source:<40} {state}")
    if not all(m.available for m in app.models.values()) and settings.public:
        raise SystemExit("a pinned detector failed to load; refusing to serve a misleading selector")
    srv = make_server(app)
    shown = HOST if settings.host == "0.0.0.0" else settings.host
    url = f"http://{shown}:{srv.server_address[1]}/"
    cols = ", ".join(f"{c.key} ({len(c.scenarios)})" for c in app.data.collections)
    print(f"SARJ demo ({'PUBLIC' if settings.public else 'local'}): {len(app.data.scenarios)} calls: {cols}. "
          f"Listening on {settings.host}:{srv.server_address[1]}. Ctrl+C to stop.", flush=True)
    if not a.no_browser and not settings.public:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
