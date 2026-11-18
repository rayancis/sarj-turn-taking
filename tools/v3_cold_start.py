"""Cold-start vs warm-start diagnostic for SARJ v3 (acoustics only, offline).

A live conversation can begin while the agent is already speaking, so the detector
has heard neither the line noise nor the caller's own voice. The 24 dev calls always
give it a first agent turn and the caller's answer before the long agent turn. This
tool replays every dev call twice through a fresh `Decider`:

  warm  from the start of the call (the benchmark situation)
  cold  from the start of the call's longest agent utterance, with nothing before it

Labels are read only here, to classify the decisions afterwards; the detector sees
audio frames and agent text/duration exactly as in the kit. The words layer (Whisper)
is not included: it runs asynchronously in real time and needs the official
real-time harness. This is a diagnostic, not a benchmark score.

    python tools/v3_cold_start.py [--set key=value ...] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo" / "models" / "sarj_v3"))

from tt.config import Config  # noqa: E402
from tt.decider import Decider, STOP  # noqa: E402
from ttbench.dataset import load_split  # noqa: E402

FRAME = 320  # 20 ms at 16 kHz


def replay(scen, start_ms: float, cfg: Config):
    """Decisions (ms, action) from a fresh decider fed from start_ms onwards."""
    lab = scen.label
    events = []
    for u in lab["agent_utterances"]:
        if u["start_ms"] >= start_ms:
            events += [(u["start_ms"], 0, u), (u["end_ms"], 1, u)]
    events.sort(key=lambda e: (e[0], e[1]))
    dec = Decider(cfg)
    agent, caller = scen.audio[:, 0], scen.audio[:, 1]
    out = []
    for i in range(int(start_ms * 16), len(agent) - FRAME + 1, FRAME):
        t = i / 16
        while events and events[0][0] <= t:
            _, kind, u = events.pop(0)
            if kind == 0:
                dec.agent_start(u["text"], u["end_ms"] - u["start_ms"])
            else:
                dec.agent_end()
        r = dec.frame(agent[i:i + FRAME], caller[i:i + FRAME])
        if r:
            out.append((t, r))
    return out


def classify(label, decisions, from_ms):
    """Per decision: 'hit' (STOP event, after onset-300 ms), 'false' (KEEP window
    or no event at all) or 'other' (EITHER window). Only STOPs inside agent turns count."""
    utts = [u for u in label["agent_utterances"] if u["end_ms"] >= from_ms]
    res = {"hit": 0, "false": 0, "either": 0, "stops": []}
    for t, act in decisions:
        if act != STOP or not any(u["start_ms"] <= t <= u["end_ms"] + 200 for u in utts):
            continue
        kind = "false"
        for e in label["events"]:
            if e["kind"] != "interruption":
                continue
            end = e.get("window_end_ms") or e["onset_ms"] + 1500
            if e["onset_ms"] - 300 <= t <= end:
                kind = {"STOP": "hit", "KEEP": "false", "EITHER": "either"}[e["expected"]]
                break
        res[kind] += 1
        res["stops"].append((round(t), kind))
    stop_events = [e for e in label["events"] if e["expected"] == "STOP" and e["onset_ms"] >= from_ms]
    res["missed"] = sum(1 for e in stop_events
                        if not any(k == "hit" and e["onset_ms"] - 300 <= t <= e["onset_ms"] + 1500
                                   for t, k in res["stops"]))
    return res


def run(cfg: Config):
    rows = {}
    for scen in load_split(ROOT / "data" / "dev", None):
        lab = scen.label
        longest = max(lab["agent_utterances"], key=lambda u: u["end_ms"] - u["start_ms"])
        warm = classify(lab, replay(scen, 0.0, cfg), longest["start_ms"])
        cold = classify(lab, replay(scen, longest["start_ms"], cfg), longest["start_ms"])
        rows[lab["scenario_id"]] = {"warm": warm, "cold": cold}
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", action="append", default=[], help="Config override key=value")
    ap.add_argument("--json")
    args = ap.parse_args()
    cfg = Config()
    for item in args.set:
        k, v = item.split("=", 1)
        cfg.override(**{k: v})
    rows = run(cfg)
    tot = {m: {"hit": 0, "false": 0, "either": 0, "missed": 0} for m in ("warm", "cold")}
    for sid, r in rows.items():
        for m in tot:
            for k in tot[m]:
                tot[m][k] += r[m][k]
        print(f"{sid}  warm {r['warm']['stops']} miss={r['warm']['missed']}  "
              f"cold {r['cold']['stops']} miss={r['cold']['missed']}")
    print("TOTAL (longest agent turn onwards)", json.dumps(tot))
    if args.json:
        Path(args.json).write_text(json.dumps({"overrides": args.set, "rows": rows, "total": tot}, indent=1))


if __name__ == "__main__":
    main()
