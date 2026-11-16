"""Offline, deterministic replay of a system class against a benchmark split (no network, no real time).

    python tools/replay.py --system my_system.py --name cand-01            # all dev calls
    python tools/replay.py --system my_system.py --calls GI-D01,BC-D02

Use it for fast, exactly repeatable iteration and for diffing two systems decision by decision.
It is NOT an official measurement: reported comparisons still need the real-time harness at speed 1.0.

Fidelity choices (they mirror the official kit, which is imported read-only and never modified):
- Control messages and their per-frame order come from ttbench.harness.build_stream, the harness's own layout.
- Calls into the system follow ttbench.server: on_scenario_start(context), on_agent_start(text, duration_ms),
  on_agent_end(), then on_frame(agent, caller) for every 20 ms frame.
- A decision produced on frame n is stamped at stream time 20 * n ms, i.e. the moment the harness sends that
  frame. The real harness stamps on arrival, which adds processing + network time (about 1-3 ms locally).
- Every decision is validated with ttbench.protocol.validate_client_message; failures are recorded as errors.
- The run folder (log.jsonl, run.json) has the harness format, so the official scorer reads it unchanged.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np

KIT = Path(__file__).resolve().parents[1]
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

from ttbench.dataset import lay_out_stream, load_split, sha256_file  # noqa: E402
from ttbench.harness import build_stream  # noqa: E402
from ttbench.protocol import FRAME_MS, FRAME_SAMPLES, validate_client_message  # noqa: E402
from ttbench.server import _as_messages  # noqa: E402


def load_system(path: Path):
    """Import a system file and return (class factory, team name)."""
    spec = importlib.util.spec_from_file_location(f"replayed_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MySystem, getattr(mod, "TEAM", "replay")


def replay(make_system, scenarios, team: str = "replay") -> tuple[list[dict], dict]:
    """Feed the laid-out scenarios to a fresh system. Returns (log lines, run meta) in the harness format."""
    stream, controls = build_stream(scenarios)
    n_frames = len(stream) // FRAME_SAMPLES
    system = make_system()
    log: list[dict] = []
    errors: list[dict] = []
    seq = 0
    frame_ns_total = 0

    def add(direction, msg, stream_ms):
        nonlocal seq
        seq += 1
        log.append({"seq": seq, "dir": direction, "t_mono_ns": None, "stream_ms": stream_ms, "msg": msg})

    hello = {"type": "hello", "team": team, "system_version": "offline-replay", "tracks": ["core", "eot"]}
    add("rx", hello, None)
    for n in range(n_frames):
        t = n * FRAME_MS
        for msg in controls.get(n, []):
            add("tx", msg, t)
            if msg["type"] == "scenario_start":
                system.on_scenario_start(msg.get("context", {}))
            elif msg["type"] == "agent_utterance_start":
                system.on_agent_start(msg.get("text", ""), msg.get("duration_ms", 0))
            elif msg["type"] == "agent_utterance_end":
                system.on_agent_end()
        frame = stream[n * FRAME_SAMPLES:(n + 1) * FRAME_SAMPLES]
        t0 = time.perf_counter_ns()
        out = system.on_frame(frame[:, 0], frame[:, 1])
        frame_ns_total += time.perf_counter_ns() - t0
        try:
            msgs = _as_messages(out)
        except ValueError as e:
            errors.append({"stream_ms": t, "error": str(e)})
            continue
        for m in msgs:
            err = validate_client_message(m)
            if err:
                errors.append({"stream_ms": t, "error": err, "msg": m})
                add("rx_invalid", m, t)
            else:
                add("rx", m, t)
    meta = {"harness_version": "offline-replay", "speed": None, "session_id": "offline", "official": False,
            "offline_replay": True, "frames_planned": n_frames, "frames_sent": n_frames, "completed": True,
            "errors": errors, "rtt_ms": [], "hello": hello, "max_send_lag_ms": None,
            "decisions_received": sum(1 for l in log if l["dir"] == "rx" and l["msg"].get("type") == "decision"),
            "on_frame_mean_us": round(frame_ns_total / max(1, n_frames) / 1000, 2),
            "scenarios": [{"scenario_id": s.scenario_id, "opaque_id": s.opaque_id, "start_ms": s.start_ms,
                           "duration_ms": s.duration_ms} for s in scenarios]}
    return log, meta


def write_run(out: Path, log: list[dict], meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "log.jsonl", "w", encoding="utf-8") as f:
        for line in log:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    (out / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--system", default="my_system.py")
    p.add_argument("--data", type=Path, default=KIT / "data" / "dev")
    p.add_argument("--calls", default="all", help="all, or comma-separated scenario ids")
    p.add_argument("--name", default=None, help="run folder name under runs/ (default: replay-<time>)")
    p.add_argument("--out", type=Path, default=None, help="explicit run folder (overrides --name)")
    p.add_argument("--no-score", action="store_true")
    a = p.parse_args(argv)

    only = None if a.calls == "all" else a.calls.split(",")
    scenarios = lay_out_stream(load_split(a.data, only))
    make_system, team = load_system(Path(a.system).resolve())
    log, meta = replay(make_system, scenarios, team)
    meta["dataset"] = {"path": str(a.data), "manifest_sha256": sha256_file(a.data / "manifest.json")}
    meta["system_file_sha256"] = sha256_file(Path(a.system))
    out = a.out or KIT / "runs" / (a.name or time.strftime("replay-%m%d-%H%M%S"))
    write_run(out, log, meta)
    print(f"Replayed {len(scenarios)} calls offline: {meta['decisions_received']} decisions, "
          f"{len(meta['errors'])} protocol errors, on_frame mean {meta['on_frame_mean_us']} us. Log: {out}")
    if not a.no_score:
        from ttbench.score import score_run_dir
        r = score_run_dir(a.data, out)
        c = r["core"]
        print(f"Core {c['core_score']:.2f}  R {c['R']:.4f}  S {c['S']:.4f}  misses {r['rates']['misses']}/"
              f"{r['rates']['n_stop_events']}  false stops {r['rates']['false_stops']} "
              f"({r['rates']['false_stops_per_min']}/min)  [offline, not official]")
    return 1 if meta["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
