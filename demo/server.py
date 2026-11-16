"""SARJ interruption-detector demo: a local web page that plays a dev call and shows the real detector's decisions.

    python demo/server.py                      # then open http://127.0.0.1:8770 (opens automatically)
    python demo/server.py --port 8771 --no-browser
    python demo/server.py --model e01=git:8f08311:my_system.py     # add another detector version

How a decision reaches the page (nothing is pre-scripted):
  1. The browser plays the call's two channels from one AudioContext and, every ~15 ms, posts its playhead.
  2. For each 20 ms frame the playhead has reached, DetectorSession does what the kit's server does with the
     harness's messages: control messages first (scenario_start, agent_utterance_start/end, built by the kit's own
     ttbench.harness.build_stream), then the 1,280-byte frame, parsed back with np.frombuffer, into
     MySystem.on_frame(agent, caller). It is the class from my_system.py (or from a pinned git revision), imported
     unchanged.
  3. Whatever on_frame returns goes through the kit's _as_messages and validate_client_message, is stamped with the
     frame's stream time (20 * n ms, as tools/replay.py does), and is returned to the page, which mutes the agent.
Labels are read only by Scorer, after the decisions exist, through the official ttbench.score.score_scenario.
The detector never receives them, and its input is never changed by the page (the evaluation is open loop: muting
the agent for the listener does not alter the agent channel the detector receives).

The server listens on 127.0.0.1 only: the dev audio must not be exposed on a network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
import types
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

KIT = Path(__file__).resolve().parents[1]
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

from ttbench.dataset import lay_out_stream, load_split  # noqa: E402
from ttbench.harness import build_stream  # noqa: E402
from ttbench.protocol import FRAME_MS, FRAME_SAMPLES, validate_client_message  # noqa: E402
from ttbench.score import DEFAULT_THRESHOLDS, score_scenario  # noqa: E402
from ttbench.server import _as_messages  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8770
STATIC = Path(__file__).resolve().parent / "static"
MAX_SESSIONS = 8
# Fixed types: on Windows, mimetypes reads the registry, which can map .js to text/plain (Chrome would then refuse it).
STATIC_TYPES = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8"}

# Detector versions offered by default. "file:" follows the working tree, so the demo shows every later algorithm
# change without edits here; "git:<rev>:<path>" pins a committed revision (read with `git show`, never checked out).
DEFAULT_MODELS = {
    "current": "file:my_system.py",
    "baseline": "git:bd2a3f0:my_system.py",
}
# Display names for known revisions only (they never influence a decision).
REVISION_NAMES = {"0915ace": "Kit baseline", "bd2a3f0": "Baseline", "8f08311": "E01", "e566234": "E01b"}
CATEGORY_NAMES = {"GI": "Genuine interruption", "BC": "Backchannel", "HT": "Hesitation", "BG": "Background",
                  "NV": "Coughs, laughs, echo", "AM": "Ambiguous words", "OV": "Overlap", "DV": "Dialect variation"}


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
    """One detector version: the unmodified source of a system file, imported as its own module."""

    def __init__(self, model_id: str, source: str):
        self.id, self.source = model_id, source
        self.available, self.error = False, None
        self.revision, self.dirty, self.name, self.sha256 = None, False, model_id, None
        self._cls = None
        try:
            raw, origin = self._read(source)
            self.sha256 = hashlib.sha256(raw).hexdigest()       # of the exact file bytes, as in runs.json
            code = raw.decode("utf-8")
            module = types.ModuleType(f"sarj_demo_model_{re.sub(r'[^0-9A-Za-z_]', '_', model_id)}")
            module.__file__ = origin
            exec(compile(code, origin, "exec"), module.__dict__)  # noqa: S102 - our own repository's system file
            self._cls = module.MySystem
            self._cls()                                            # fail at start-up, not mid-demo
            self.available = True
        except Exception as e:  # noqa: BLE001 - shown in the page instead of crashing the demo
            self.error = f"{type(e).__name__}: {e}"
        default = "Current my_system.py" if source.startswith("file:") else model_id
        self.name = REVISION_NAMES.get(self.revision or "", default)

    def _read(self, source: str) -> tuple[bytes, str]:
        kind, _, rest = source.partition(":")
        if kind == "file":
            path = (KIT / rest).resolve()
            log = _git("log", "-1", "--format=%h", "--", rest)
            self.revision = (log or "").strip() or None
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

    def describe(self) -> dict:
        return {"id": self.id, "name": self.name, "source": self.source, "revision": self.revision,
                "dirty": self.dirty, "sha256": self.sha256, "available": self.available, "error": self.error}


# ------------------------------------------------------------------------------------------------ data
class DevData:
    """The dev split, read once. Scenario audio is served as the original files; labels stay server-side."""

    def __init__(self, data_dir: Path):
        self.dir = data_dir
        manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
        self.thresholds = {**DEFAULT_THRESHOLDS, **manifest.get("thresholds", {})}
        self.scenarios = {s.scenario_id: s for s in load_split(data_dir)}

    def summary(self) -> list[dict]:
        out = []
        for sid, s in self.scenarios.items():
            lab = s.label
            out.append({"id": sid, "category": lab["category"], "category_name": CATEGORY_NAMES.get(lab["category"], ""),
                        "subtype": lab.get("subtype", ""), "difficulty": lab.get("difficulty"),
                        "dialect": lab.get("dialect", ""), "acoustic": lab.get("acoustic", {}).get("conditions", []),
                        "use_case": lab["context"]["use_case"], "duration_ms": s.duration_ms,
                        "expected": [e["expected"] for e in lab["events"]]})
        return out

    def audio_path(self, sid: str) -> Path:
        return self.dir / "audio" / self.scenarios[sid].label["audio"]["file"]

    def timeline(self, sid: str) -> dict:
        """Protocol inputs (what the detector is told) kept apart from ground truth (what it is never told)."""
        s = self.scenarios[sid]
        lab = s.label
        return {
            "id": sid, "duration_ms": s.duration_ms,
            "protocol": {"context": {"use_case": lab["context"]["use_case"], "language": "ar"},
                         "agent_utterances": [{"utt_id": u["utt_id"], "start_ms": u["start_ms"],
                                               "end_ms": u["end_ms"], "text": u["text"]}
                                              for u in lab["agent_utterances"]]},
            "ground_truth": {"category": lab["category"], "subtype": lab.get("subtype", ""),
                             "label_status": lab.get("provenance", {}).get("label_status", ""),
                             "events": lab["events"], "user_segments": lab.get("user_segments", []),
                             "thresholds": self.thresholds},
        }


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
    """Official per-scenario scoring of the decisions made so far. Reveals an event only once nothing later in the
    stream can change its outcome, so the page never shows a verdict ahead of the audio."""

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


# ------------------------------------------------------------------------------------------------ benchmark evidence
def benchmark_results() -> dict:
    """Previously measured dev-set results, read from the committed evidence index (never computed here)."""
    path = KIT / "experiments" / "evidence" / "runs.json"
    try:
        idx = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "rows": []}
    rows = []
    for run_id, r in idx.get("runs", {}).items():
        commit = r.get("system_commit")
        rows.append({"run": run_id, "commit": commit, "name": REVISION_NAMES.get(commit, commit),
                     "core": r.get("core"), "R": r.get("R"), "S": r.get("S"), "misses": r.get("misses"),
                     "false_stops": r.get("false_stops"), "protocol_errors": r.get("protocol_errors")})
    return {"available": True, "source": "experiments/evidence/runs.json", "scope": idx.get("scope"),
            "speed": idx.get("speed"), "calls": idx.get("calls"), "data": idx.get("data"), "rows": rows}


# ------------------------------------------------------------------------------------------------ HTTP
class DemoApp:
    def __init__(self, data_dir: Path, model_specs: dict[str, str]):
        self.data = DevData(data_dir)
        self.models = {mid: Model(mid, src) for mid, src in model_specs.items()}   # loaded before serving
        self.sessions: dict[str, tuple[DetectorSession, Scorer, dict]] = {}
        self.lock = threading.Lock()

    def config(self) -> dict:
        return {"models": [m.describe() for m in self.models.values()], "scenarios": self.data.summary(),
                "benchmark": benchmark_results(), "frame_ms": FRAME_MS}

    def new_session(self, scenario_id: str, model_id: str) -> dict:
        if scenario_id not in self.data.scenarios:
            raise KeyError(f"unknown scenario {scenario_id!r}")
        model = self.models.get(model_id)
        if model is None or not model.available:
            raise KeyError(f"model {model_id!r} is not available")
        scen = self.data.scenarios[scenario_id]
        sess = DetectorSession(model, scen)
        sid = uuid.uuid4().hex
        with self.lock:
            self.sessions[sid] = (sess, Scorer(scen.label, self.data.thresholds), model.describe())
            while len(self.sessions) > MAX_SESSIONS:
                self.sessions.pop(next(iter(self.sessions)))
        return {"session_id": sid, "model": model.describe(), "scenario_id": scenario_id,
                "stream_end_ms": sess.n_frames * FRAME_MS}

    def advance(self, session_id: str, to_ms: float) -> dict:
        with self.lock:
            entry = self.sessions.get(session_id)
        if entry is None:
            raise KeyError("unknown or expired session")
        sess, scorer, _ = entry
        t0 = time.perf_counter_ns()
        new, protocol = sess.advance(float(to_ms))
        server_ms = (time.perf_counter_ns() - t0) / 1e6
        return {"session_id": session_id, "fed_ms": sess.fed_ms, "done": sess.done, "decisions": new,
                "protocol": protocol, "errors": sess.errors, "server_ms": round(server_ms, 3),
                "mean_on_frame_us": sess.mean_on_frame_us(),
                "score": scorer.evaluate(sess.decisions, sess.fed_ms, sess.done)}


def make_handler(app: DemoApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "sarj-demo/1"
        protocol_version = "HTTP/1.1"     # keep-alive: the page polls ~50x/s; one connection instead of thousands
        disable_nagle_algorithm = True    # headers and body are separate writes: avoid ~40 ms Nagle/delayed-ACK stalls

        def log_message(self, fmt, *args):            # keep the console readable during a presentation
            pass

        def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _error(self, status: int, message: str):
            self._json({"error": message}, status)

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/config":
                return self._json(app.config())
            m = re.fullmatch(r"/api/scenario/([A-Za-z0-9_-]{1,64})", path)
            if m:
                if m.group(1) not in app.data.scenarios:
                    return self._error(404, "unknown scenario")
                return self._json(app.data.timeline(m.group(1)))
            m = re.fullmatch(r"/api/audio/([A-Za-z0-9_-]{1,64})\.wav", path)
            if m:
                if m.group(1) not in app.data.scenarios:
                    return self._error(404, "unknown scenario")
                return self._send(200, app.data.audio_path(m.group(1)).read_bytes(), "audio/wav", "private, max-age=600")
            name = "index.html" if path == "/" else path.lstrip("/")
            m = re.fullmatch(r"[a-z_]+\.(html|js|css)", name)
            if m and (STATIC / name).is_file():
                return self._send(200, (STATIC / name).read_bytes(), STATIC_TYPES[m.group(1)])
            return self._error(404, "not found")

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length <= 65536 else None
                if not isinstance(body, dict):
                    return self._error(400, "expected a small JSON object")
                if path == "/api/session":
                    return self._json(app.new_session(str(body.get("scenario")), str(body.get("model"))))
                m = re.fullmatch(r"/api/session/([0-9a-f]{32})/advance", path)
                if m:
                    to_ms = float(body.get("to_ms"))
                    if not np.isfinite(to_ms):
                        return self._error(400, "to_ms must be finite")
                    return self._json(app.advance(m.group(1), to_ms))
                return self._error(404, "not found")
            except KeyError as e:
                return self._error(404, str(e.args[0]) if e.args else "not found")
            except (TypeError, ValueError) as e:
                return self._error(400, str(e))

    return Handler


def make_server(app: DemoApp, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((HOST, port), make_handler(app))
    srv.daemon_threads = True
    return srv


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
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--data", type=Path, default=KIT / "data" / "dev")
    p.add_argument("--model", action="append", default=[], metavar="ID=SOURCE",
                   help="extra detector version: file:<path> or git:<rev>:<path> (repeatable)")
    p.add_argument("--no-browser", action="store_true", help="do not open the page automatically")
    a = p.parse_args(argv)

    app = DemoApp(a.data, parse_models(a.model))
    for m in app.models.values():
        d = m.describe()
        state = f"{d['name']} @ {d['revision']}{' (uncommitted changes)' if d['dirty'] else ''}" if m.available \
            else f"UNAVAILABLE: {m.error}"
        print(f"  model {m.id:<10} {m.source:<28} {state}")
    srv = make_server(app, a.port)
    url = f"http://{HOST}:{srv.server_address[1]}/"
    print(f"SARJ demo: {len(app.data.scenarios)} dev calls. Open {url} in Chrome. Ctrl+C to stop.", flush=True)
    if not a.no_browser:
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
