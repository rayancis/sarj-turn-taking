"""Combine scored runs into one scoreboard (Markdown + CSV).

    python -m ttbench.leaderboard runs/T01 runs/T02 runs/B0 runs/B1 --out scoreboard

Rows are sorted by Core score; ties by Restraint S, then p90 latency. End-of-turn is a separate column (own track).
Also writes events.csv: one row per labelled moment, with what each run did there (outcome, latency, credit), next to
the moment's category, dialect and acoustic conditions. It shows which ideas handle which situations, not only who won.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

CATS = ["GI", "BC", "HT", "BG", "NV", "AM", "OV", "DV"]


def row(run: Path) -> dict:
    s = json.loads((run / "score.json").read_text(encoding="utf-8"))
    cats = s["categories"]

    def pass_rate(c):
        v = cats.get(c)
        if not v:
            return None
        n = v["n_stop"] + v["n_keep"] + v["n_scenarios"]
        ok = v["stop_yielded"] + v["keep_passed"] + v["protected_clean"]
        return round(ok / n, 2) if n else None

    return {"run": run.name, "team": s.get("team"), "core": round(s["core"]["core_score"], 1), "R": round(s["core"]["R"], 2),
            "S": round(s["core"]["S"], 2), "eot": round(s["eot"]["eot_score"], 1) if s["eot"]["attempted"] else None,
            "lat_median": s["latency_ms"]["median"], "lat_p90": s["latency_ms"]["p90"],
            "false_stops_per_min": s["rates"]["false_stops_per_min"], "misses": s["rates"]["misses"],
            "dialect_gap": None if s["slices"]["dialect_gap"] is None else round(s["slices"]["dialect_gap"], 2),
            "noise_gap": None if s["slices"]["noise_gap"] is None else round(s["slices"]["noise_gap"], 2),
            "rtt_median": (s.get("rtt_ms") or {}).get("median"), **{c: pass_rate(c) for c in CATS}}


def write_events(runs: list[Path], rows: list[dict], path: Path) -> None:
    """Per-moment comparison across runs: the raw material for 'which approach handles what, and why'."""
    scores = {r.name: json.loads((r / "score.json").read_text(encoding="utf-8")) for r in runs}
    data = Path(next(iter(json.loads((r / "run.json").read_text(encoding="utf-8")).get("dataset", {}).get("path", "")
                      for r in runs), ""))
    order = [r["run"] for r in rows]
    table: dict[str, dict] = {}
    for name in order:
        for e in scores[name]["events"]:
            t = table.setdefault(e["event_id"], {"event": e["event_id"], "scenario": e["scenario_id"], "category": e["category"],
                                                 "expected": e["expected"]})
            t[f"{name}:outcome"], t[f"{name}:latency_ms"], t[f"{name}:credit"] = e["outcome"], e.get("latency_ms"), e["credit"]
    for t in table.values():
        lp = data / "labels" / f"{t['scenario']}.json"
        if lp.exists():
            L = json.loads(lp.read_text(encoding="utf-8"))
            t["dialect"], t["conditions"], t["elicitation"] = L.get("dialect"), "+".join(L["acoustic"]["conditions"]), L.get("elicitation")
    cols = ["event", "scenario", "category", "expected", "dialect", "conditions", "elicitation"] + \
           [f"{n}:{k}" for n in order for k in ("outcome", "latency_ms", "credit")]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(table.values())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+", type=Path)
    p.add_argument("--out", type=Path, default=Path("scoreboard"))
    a = p.parse_args(argv)
    rows = sorted((row(r) for r in a.runs), key=lambda r: (-r["core"], -r["S"], r["lat_p90"] or 1e9))
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / "scoreboard.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cols = ["team", "core", "R", "S", "eot", "lat_median", "lat_p90", "false_stops_per_min", "misses", *CATS,
            "dialect_gap", "noise_gap", "rtt_median"]
    md = ["| # | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for i, r in enumerate(rows, 1):
        md.append(f"| {i} | " + " | ".join("–" if r[c] is None else str(r[c]) for c in cols) + " |")
    (a.out / "scoreboard.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    write_events(a.runs, rows, a.out / "events.csv")
    print("\n".join(md))


if __name__ == "__main__":
    main()
