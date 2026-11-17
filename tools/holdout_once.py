"""Score the frozen holdout (data/extra/holdout) ONCE, after the candidate has been selected.

    python tools/holdout_once.py experiments/phase3/pilot.json E01b A2 B1 N1

Evaluates the named experiments of the plan (the selected candidate and the comparison rows of the report) in a
single pass, writes experiments/phase3/holdout.json, and refuses to run again: the holdout is a one-time check of
generalisation, never something to iterate on. Nothing in tools/autoresearch.py reads the holdout.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(KIT), str(KIT / "tools")]
import autoresearch as ar  # noqa: E402


def main(argv) -> int:
    plan_path, ids = Path(argv[0]), argv[1:]
    out = plan_path.parent / "holdout.json"
    if out.exists():
        print(f"{out} exists: the holdout was already scored on {json.loads(out.read_text())['scored_utc']}. Not running again.")
        return 2
    plan = {e["id"]: e for e in json.loads(plan_path.read_text(encoding="utf-8"))["experiments"]}

    def config(eid):
        e = plan[eid]
        return {**(config(e["parent"]) if e.get("parent") else {}), **e.get("set", {})}

    data = KIT / "data" / "extra" / "holdout"
    result = {"scored_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=KIT, capture_output=True, text=True).stdout.strip(),
              "selected_before_scoring": ids[-1], "rows": {}}
    for eid in ids:
        mod = ar.load_system(config(eid))
        result["rows"][eid] = {"config": config(eid), **{f"gain{g:+d}" if g else "nominal": ar.run_set(mod, data, g) for g in (0, -12, 6)}}
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for eid, r in result["rows"].items():
        n = r["nominal"]
        print(f"{eid:5s} Core {n['core']:6.2f}  R {n['R']:.3f}  S {n['S']:.3f}  false stops {n['false_stops']:2d}  misses {n['misses']}  "
              f"| -12 dB: {r['gain-12']['core']:.1f} ({r['gain-12']['misses']} misses)  +6 dB: {r['gain+6']['core']:.1f} ({r['gain+6']['misses']} misses)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
