"""Controlled, auditable experiments on my_system.py (a bounded pilot, not an open-ended search).

    python tools/autoresearch.py experiments/phase3/pilot.json                 # run every experiment not yet in the table
    python tools/autoresearch.py experiments/phase3/pilot.json --only A1,A2

An experiment is a hypothesis plus ONE group of option changes on top of a parent experiment:
    {"id": "A1", "group": "gain", "parent": "E01b", "hypothesis": "...", "set": {"ABS_GATE_DB": null}}
The options are the module-level constants of my_system.py; the file itself is never edited by this tool, so an
unsuccessful experiment leaves nothing to revert.

Every experiment is replayed offline (deterministic; decision time = frame time) and scored with the official scorer on
  dev     the kit's 24 calls                       xdev    data/extra/xdev (25 scenarios, human keywords)
  each also with the caller microphone at -12 dB and +6 dB (gain robustness), plus the 61-case generated matrix.
data/extra/holdout is never read here (tools/holdout_once.py scores it once, after selection).

Automatic rejection (the candidate is not kept, whatever its score):
  protocol errors; on_frame p99 above 5 ms (a quarter of a frame); a new missed interruption on dev; more than one
  new miss on xdev; or a loss of more than 1.0 total STOP credit over dev + xdev, all relative to the reference E01b.
Verdict KEEP needs the objective - the mean of dev Core and xdev Core - to rise by at least 1.0 over the parent,
without the gain-perturbed objective falling by more than 2.0. Anything else is NEUTRAL and is not carried forward.
Offline numbers are for selection only; reported comparisons still need the real-time harness.
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
sys.path[:0] = [str(KIT), str(KIT / "tools")]

import replay as replay_mod  # noqa: E402
import robustness  # noqa: E402
from ttbench.dataset import lay_out_stream, load_split  # noqa: E402
from ttbench.score import load_labels, score  # noqa: E402

SETS = {"dev": KIT / "data" / "dev", "xdev": KIT / "data" / "extra" / "xdev"}
GAINS = (-12, 6)
P99_LIMIT_US = 5000
COLUMNS = ["id", "group", "parent", "verdict", "objective", "d_objective", "dev_core", "dev_R", "dev_S", "dev_false_stops",
           "dev_misses", "xdev_core", "xdev_R", "xdev_S", "xdev_false_stops", "xdev_misses", "gain_objective",
           "dev_core_m12", "dev_misses_m12", "dev_core_p6", "dev_misses_p6", "xdev_core_m12", "xdev_misses_m12",
           "xdev_core_p6", "xdev_misses_p6", "matrix_ok", "stop_latency_median_ms", "on_frame_mean_us", "on_frame_p99_us",
           "protocol_errors", "reasons", "stop_regressions", "keep_gained", "keep_lost", "config"]


def load_system(overrides: dict):
    spec = importlib.util.spec_from_file_location("ar_system", KIT / "my_system.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k, v in overrides.items():
        if not hasattr(mod, k):
            raise KeyError(f"my_system.py has no option {k}")
        setattr(mod, k, v)
    return mod


def run_set(mod, data: Path, gain_db: float = 0.0) -> dict:
    scenarios = load_split(data)
    if gain_db:
        for s in scenarios:
            a = s.audio.astype(np.float64)
            a[:, 1] *= 10 ** (gain_db / 20)                       # caller microphone only
            s.audio = np.clip(np.round(a), -32768, 32767).astype(np.int16)
    times: list[int] = []

    def make():
        system = mod.MySystem()
        inner = system.on_frame

        def timed(agent, caller):
            t0 = time.perf_counter_ns()
            out = inner(agent, caller)
            times.append(time.perf_counter_ns() - t0)
            return out
        system.on_frame = timed
        return system

    log, meta = replay_mod.replay(make, lay_out_stream(scenarios))
    labels, manifest = load_labels(data)
    r = score(labels, meta, log, manifest.get("thresholds"))
    t = np.array(times) / 1000
    return {"core": r["core"]["core_score"], "R": r["core"]["R"], "S": r["core"]["S"], "false_stops": r["rates"]["false_stops"],
            "misses": r["rates"]["misses"], "errors": len(meta["errors"]), "latency_median": r["latency_ms"]["median"],
            "mean_us": float(t.mean()), "p99_us": float(np.percentile(t, 99)),
            "categories": {k: v["S"] for k, v in r["categories"].items()},
            "events": {e["event_id"]: (e["expected"], e["credit"]) for e in r["events"] if e["kind"] == "interruption"},
            "stray": sum(len(s["spurious_stops"]) for s in r["scenarios"])}


def evaluate(overrides: dict) -> dict:
    mod = load_system(overrides)
    out = {name: run_set(mod, path) for name, path in SETS.items()}
    for g in GAINS:
        for name, path in SETS.items():
            out[f"{name}@{g:+d}"] = run_set(mod, path, g)
    rows = robustness.part_a(mod)
    out["matrix_ok"] = sum(r[2] for r in rows)
    out["matrix_bad"] = [r[0] for r in rows if not r[2]]
    return out


def objective(res: dict) -> float:
    return (res["dev"]["core"] + res["xdev"]["core"]) / 2


def gain_objective(res: dict) -> float:
    return float(np.mean([res[f"{n}@{g:+d}"]["core"] for n in SETS for g in GAINS]))


def judge(res: dict, parent: dict | None, ref: dict | None) -> tuple[str, list[str], dict]:
    diff = {"stop_regressions": [], "keep_gained": [], "keep_lost": []}
    reasons = []
    if any(res[k]["errors"] for k in res if isinstance(res[k], dict) and "errors" in res[k]):
        reasons.append("protocol errors")
    p99 = max(res[n]["p99_us"] for n in SETS)
    if p99 > P99_LIMIT_US:
        reasons.append(f"on_frame p99 {p99:.0f} us > {P99_LIMIT_US}")
    if ref is None:
        return "REFERENCE", reasons, diff
    lost_credit = 0.0
    new_miss = {n: 0 for n in SETS}
    for n in SETS:
        for eid, (expected, credit) in res[n]["events"].items():
            before = ref[n]["events"][eid][1]
            if expected == "STOP":
                lost_credit += before - credit
                if credit < before - 1e-9:
                    diff["stop_regressions"].append(f"{eid} {before:.2f}->{credit:.2f}")
                if credit == 0 and before > 0:
                    new_miss[n] += 1
            elif expected == "KEEP":
                if credit > before:
                    diff["keep_gained"].append(eid)
                elif credit < before:
                    diff["keep_lost"].append(eid)
    if new_miss["dev"]:
        reasons.append(f"{new_miss['dev']} new missed interruption(s) on dev")
    if new_miss["xdev"] > 1:
        reasons.append(f"{new_miss['xdev']} new missed interruptions on xdev")
    if lost_credit > 1.0:
        reasons.append(f"STOP credit down by {lost_credit:.2f}")
    if reasons:
        return "REJECT", reasons, diff
    base = parent or ref
    if objective(res) - objective(base) >= 1.0 and gain_objective(res) - gain_objective(base) >= -2.0:
        return "KEEP", reasons, diff
    return "NEUTRAL", [f"objective {objective(res) - objective(base):+.2f}, gain objective "
                       f"{gain_objective(res) - gain_objective(base):+.2f} vs parent"], diff


def row_of(exp: dict, cfg: dict, res: dict, verdict: str, reasons: list[str], diff: dict, parent: dict | None) -> dict:
    d, x = res["dev"], res["xdev"]
    return {"id": exp["id"], "group": exp.get("group", ""), "parent": exp.get("parent") or "", "verdict": verdict,
            "objective": round(objective(res), 2),
            "d_objective": "" if parent is None else round(objective(res) - objective(parent), 2),
            "dev_core": round(d["core"], 2), "dev_R": round(d["R"], 3), "dev_S": round(d["S"], 3),
            "dev_false_stops": d["false_stops"], "dev_misses": d["misses"],
            "xdev_core": round(x["core"], 2), "xdev_R": round(x["R"], 3), "xdev_S": round(x["S"], 3),
            "xdev_false_stops": x["false_stops"], "xdev_misses": x["misses"], "gain_objective": round(gain_objective(res), 2),
            "dev_core_m12": round(res["dev@-12"]["core"], 1), "dev_misses_m12": res["dev@-12"]["misses"],
            "dev_core_p6": round(res["dev@+6"]["core"], 1), "dev_misses_p6": res["dev@+6"]["misses"],
            "xdev_core_m12": round(res["xdev@-12"]["core"], 1), "xdev_misses_m12": res["xdev@-12"]["misses"],
            "xdev_core_p6": round(res["xdev@+6"]["core"], 1), "xdev_misses_p6": res["xdev@+6"]["misses"],
            "matrix_ok": res["matrix_ok"], "stop_latency_median_ms": d["latency_median"],
            "on_frame_mean_us": round(max(d["mean_us"], x["mean_us"]), 1), "on_frame_p99_us": round(max(d["p99_us"], x["p99_us"]), 1),
            "protocol_errors": sum(res[k]["errors"] for k in res if isinstance(res[k], dict) and "errors" in res[k]),
            "reasons": "; ".join(reasons), "stop_regressions": ", ".join(diff["stop_regressions"]),
            "keep_gained": ", ".join(diff["keep_gained"]), "keep_lost": ", ".join(diff["keep_lost"]),
            "config": json.dumps(cfg, sort_keys=True)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("plan", type=Path)
    p.add_argument("--only", default=None, help="comma-separated experiment ids")
    a = p.parse_args(argv)
    plan = json.loads(a.plan.read_text(encoding="utf-8"))
    out_dir = a.plan.parent
    cache_path = out_dir / "results.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    exps = {e["id"]: e for e in plan["experiments"]}
    if len(exps) - 1 > plan.get("max_experiments", 20):
        raise SystemExit(f"plan holds {len(exps) - 1} experiments; the pilot is bounded at {plan.get('max_experiments', 20)}")

    def config(eid: str) -> dict:
        e = exps[eid]
        return {**(config(e["parent"]) if e.get("parent") else {}), **e.get("set", {})}

    todo = [e for e in plan["experiments"] if (a.only is None or e["id"] in a.only.split(","))]
    for e in todo:
        cfg = config(e["id"])
        hit = cache.get(e["id"])
        if hit is None or hit["config"] != cfg:
            t0 = time.time()
            res = evaluate(cfg)
            cache[e["id"]] = {"config": cfg, "result": res, "seconds": round(time.time() - t0, 1),
                              "hypothesis": e.get("hypothesis", "")}
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    ref_id = plan["reference"]
    rows = []
    for e in plan["experiments"]:
        if e["id"] not in cache:
            continue
        res = cache[e["id"]]["result"]
        ref = None if e["id"] == ref_id else cache[ref_id]["result"]
        parent = cache[e["parent"]]["result"] if e.get("parent") and e["parent"] in cache else None
        verdict, reasons, diff = judge(res, parent, ref)
        rows.append(row_of(e, cache[e["id"]]["config"], res, verdict, reasons, diff, parent))
    with open(out_dir / "results.tsv", "w", encoding="utf-8") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            f.write("\t".join(str(r[c]) for c in COLUMNS) + "\n")
    print(f"{'id':6s} {'verdict':9s} {'obj':>6s} {'dev':>6s} {'R':>5s} {'S':>5s} {'FS':>3s} {'mis':>3s} | {'xdev':>6s} {'R':>5s} {'S':>5s} "
          f"{'FS':>3s} {'mis':>3s} | {'gain':>5s} {'mtx':>3s} {'p99us':>6s}  reasons / STOP regressions")
    for r in rows:
        print(f"{r['id']:6s} {r['verdict']:9s} {r['objective']:6.2f} {r['dev_core']:6.2f} {r['dev_R']:5.3f} {r['dev_S']:5.3f} "
              f"{r['dev_false_stops']:3d} {r['dev_misses']:3d} | {r['xdev_core']:6.2f} {r['xdev_R']:5.3f} {r['xdev_S']:5.3f} "
              f"{r['xdev_false_stops']:3d} {r['xdev_misses']:3d} | {r['gain_objective']:5.1f} {r['matrix_ok']:3d} "
              f"{r['on_frame_p99_us']:6.0f}  {r['reasons']} {('| ' + r['stop_regressions']) if r['stop_regressions'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
