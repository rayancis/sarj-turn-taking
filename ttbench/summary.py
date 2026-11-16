"""Plain-language summary of a scored run, and your run history (are you improving?).

    python -m ttbench.summary runs/<name>              # what went right and wrong
    python -m ttbench.summary runs/<name> --failures   # a failure table to paste into WRITEUP.md
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

WHAT = {"GI": "a real interruption", "BC": "a backchannel (\"mm-hm\", \"ايه\")", "HT": "a hesitation",
        "BG": "background sound", "NV": "a cough, laugh or echo", "AM": "an ambiguous word",
        "OV": "both starting at once", "DV": "a dialect case"}


def _s(ms) -> str:
    return f"{ms / 1000:.1f} s"


def lines(r: dict) -> list[tuple[int, str]]:
    """(severity, sentence): 0 = good, 1 = could be better, 2 = wrong. Sorted worst first by the caller."""
    out = []
    stray = {sc["scenario_id"]: sc.get("spurious_stops", []) for sc in r.get("scenarios", [])}
    for e in r["events"]:
        sid, what = e["scenario_id"], WHAT.get(e["category"], e["category"])
        if e["kind"] == "interruption" and e["expected"] == "EITHER":
            did = "stopped" if e.get("behaviour") == "STOP" else "kept talking"
            out.append((0 if e["credit"] >= 0.6 else 1, f"{sid}: {what}; either choice is acceptable here, you {did}."))
        elif e["kind"] == "interruption":
            if e["expected"] == "STOP" or (e["expected"] == "EITHER" and e.get("behaviour") == "STOP"):
                if e["outcome"] == "yield":
                    lat = e.get("latency_ms") or 0
                    sev = 0 if lat <= 300 else 1
                    out.append((sev, f"{sid}: {what}. You stopped {_s(lat)} after it was clear"
                                     + ("." if sev == 0 else " (full credit needs 0.3 s).")))
                else:
                    early = [t for t in stray.get(sid, []) if t < e.get("onset_ms", 1e12)]
                    if early:
                        out.append((2, f"{sid}: {what}, but your agent had already stopped at {_s(early[-1])} for no reason, "
                                       "so it was not listening for this one (an early stop does not count)."))
                    else:
                        out.append((2, f"{sid}: {what}, and your agent did not stop in time (or resumed over the caller)."))
            else:
                if e["outcome"] == "kept":
                    out.append((0, f"{sid}: {what}. You kept talking, correctly."))
                elif e["outcome"] == "hiccup":
                    out.append((1, f"{sid}: {what}. You stopped, then resumed quickly (half credit)."))
                else:
                    out.append((2, f"{sid}: {what}. You stopped, but the caller did not want the floor."))
        else:
            bad = [i for i in e.get("patience_items", []) if not i["passed"]]
            for i in bad:
                where = "while the caller was still talking" if i["item"].startswith("while") else "while the caller was only pausing"
                out.append((2, f"{sid}: you took the turn at {_s(i['take_turn_at'])} {where}."))
            lat = e.get("latency_ms")
            if lat is None:
                out.append((2, f"{sid}: the caller finished and you never took the turn."))
            elif not bad:
                sev = 0 if lat <= 600 else 1
                out.append((sev, f"{sid}: the caller finished; you took the turn after {_s(lat)}"
                                 + ("." if sev == 0 else " (full credit needs 0.6 s).")))
    for s in r.get("scenarios", []):
        for t in s.get("spurious_stops", []):
            out.append((2, f"{s['scenario_id']}: you stopped at {_s(t)} when nothing was happening."))
    return out


def headline(r: dict) -> str:
    c, e = r["core"], r["eot"]
    eot = f"{e['eot_score']:.0f} / 100" if e["attempted"] else "not attempted"
    return (f"Score {c['core_score']:.1f} / 100   (stops when it should: {c['R']:.2f}, keeps talking when it should: "
            f"{c['S']:.2f})   ·   end-of-turn track: {eot}")


def record(run_dir: Path, r: dict) -> str:
    """Append to runs/history.csv and compare with the previous run on the same calls at the same speed."""
    hist = run_dir.parent / "history.csv"
    calls = "+".join(sorted({e["scenario_id"] for e in r["events"]}))
    row = {"when": time.strftime("%Y-%m-%d %H:%M"), "run": run_dir.name, "calls": calls, "speed": r.get("speed") or 1.0,
           "version": r.get("system_version") or "", "score": round(r["core"]["core_score"], 1),
           "eot": round(r["eot"]["eot_score"], 1)}
    prev = []
    if hist.exists():
        with open(hist, newline="", encoding="utf-8") as f:
            prev = [p for p in csv.DictReader(f) if p["calls"] == calls and float(p["speed"]) == float(row["speed"])
                    and p["run"] != row["run"]]
    new = not hist.exists()
    with open(hist, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    if not prev:
        return "First run on these calls: this is your starting point."
    last, best = prev[-1], max(prev, key=lambda p: float(p["score"]))
    d = row["score"] - float(last["score"])
    arrow = "▲" if d > 0.05 else ("▼" if d < -0.05 else "=")
    return (f"{arrow} {d:+.1f} vs your last run ({float(last['score']):.1f}, {last['run']}).  "
            f"Best on these calls: {max(float(best['score']), row['score']):.1f}.")


def text(r: dict, run_dir: Path | None = None, limit: int = 8) -> str:
    ls = sorted(lines(r), key=lambda x: -x[0])
    mark = {0: "  ✓", 1: "  ~", 2: "  ✗"}
    wrong, ok = sum(1 for s, _ in ls if s == 2), sum(1 for s, _ in ls if s == 0)
    out = [headline(r), f"{ok} right, {len(ls) - ok - wrong} could be faster, {wrong} wrong:"]
    out += [f"{mark[s]} {t}" for s, t in ls[:limit]]
    if len(ls) > limit:
        out.append(f"    … {len(ls) - limit} more in the report")
    if run_dir is not None:
        out.append(f"Full report: {run_dir / 'report.md'}")
    return "\n".join(out)


def failures(r: dict, run_dir: Path, n: int = 8) -> str:
    """A failure table for WRITEUP.md: your worst cases, with a command to hear each one. You write the 'why'."""
    rows = sorted(lines(r), key=lambda x: -x[0])
    rows = [t for s, t in rows if s > 0][:n]
    out = ["| # | Call | What happened | Listen | Why it went wrong (your analysis) |", "|---|---|---|---|---|"]
    for i, t in enumerate(rows, 1):
        sid, what = t.split(": ", 1)
        out.append(f"| {i} | {sid} | {what} | `python -m ttbench.render --run {run_dir} --scenario {sid}` | |")
    return "\n".join(out)


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    run_dir = Path(args[0])
    r = json.loads((run_dir / "score.json").read_text(encoding="utf-8"))
    print(failures(r, run_dir) if "--failures" in args else text(r, run_dir, limit=100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
