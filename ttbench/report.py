"""Human-readable Markdown report for one scored run."""
from __future__ import annotations

from pathlib import Path

CAT_NAMES = {"GI": "Genuine interruption", "BC": "Backchannel", "HT": "Hesitation", "BG": "Background",
             "NV": "Caller non-speech / echo", "AM": "Ambiguous", "OV": "Overlap", "DV": "Dialect variation"}


def _f(x, nd=2):
    return "–" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def write_report(r: dict, labels: dict, path: Path) -> None:
    c, e = r["core"], r["eot"]
    L = []
    L.append(f"# Turn-taking benchmark report: {r.get('team') or 'unknown team'}")
    L.append("")
    if not r.get("official_speed"):
        L.append(f"> **Unofficial run at {r.get('speed')}x speed.** Latency figures are not meaningful; use speed 1.0 for real numbers.")
        L.append("")
    if r["label_status"] != ["human_validated"]:
        L.append(f"> **Labels: {', '.join(r['label_status'])}.** Development labels are derived from the scripts of "
                 "synthetic recordings and are not yet validated by native listeners.")
        L.append("")
    from .summary import lines as _plain
    ls = sorted(_plain(r), key=lambda x: -x[0])
    L.append("## In plain words")
    L.append("")
    L.extend(f"- {'✓' if s == 0 else ('~' if s == 1 else '✗')} {t}" for s, t in ls)
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append("| Core score | Responsiveness R | Restraint S | F (harmonic) | Ambiguity A | End-of-turn (separate track) |")
    L.append("|---|---|---|---|---|---|")
    eot = f"{e['eot_score']:.1f}" if e["attempted"] else "not attempted"
    L.append(f"| **{c['core_score']:.1f}** / 100 | {_f(c['R'])} | {_f(c['S'])} | {_f(c['F'])} | {_f(c['A'])} | {eot} |")
    L.append("")
    lat, rt = r["latency_ms"], r["rates"]
    L.append(f"- Stop latency after the intent was evident: median {_f(lat['median'], 0)} ms, p90 {_f(lat['p90'], 0)} ms "
             f"({lat['n']} yields)")
    L.append(f"- Missed interruptions: {rt['misses']} of {rt['n_stop_events']} · false stops: {rt['false_stops']} "
             f"({_f(rt['false_stops_per_min'])} per minute of agent speech) · hiccups (stop + quick RESUME): {rt['hiccups']}")
    if r.get("rtt_ms"):
        L.append(f"- Network round trip: median {r['rtt_ms']['median']} ms, max {r['rtt_ms']['max']} ms")
    L.append(f"- Protocol errors: {r['protocol_errors']} · decisions outside agent speech (ignored): {rt['ignored_decisions']}")
    L.append("")
    L.append("## By category")
    L.append("")
    L.append("| Category | R | S | genuine stops caught | backchannels/noise kept | scenarios with no stray stop |")
    L.append("|---|---|---|---|---|---|")
    for k, v in r["categories"].items():
        L.append(f"| {k} {CAT_NAMES.get(k, '')} | {_f(v['R'])} | {_f(v['S'])} | {v['stop_yielded']}/{v['n_stop']} | "
                 f"{v['keep_passed']}/{v['n_keep']} | {v['protected_clean']}/{v['n_scenarios']} |")
    L.append("")
    L.append("## Slices")
    L.append("")
    L.append("| Slice | scenarios | R | S | F |")
    L.append("|---|---|---|---|---|")
    for k, v in r["slices"].items():
        if isinstance(v, dict):
            L.append(f"| {k} | {v['n_scenarios']} | {_f(v['R'])} | {_f(v['S'])} | {_f(v['F'])} |")
    L.append(f"\nDialect gap (Najdi F − other F): {_f(r['slices'].get('dialect_gap'))} · "
             f"noise gap (clean F − noisy F): {_f(r['slices'].get('noise_gap'))}")
    L.append("")
    L.append("## Every event")
    L.append("")
    L.append("| Scenario | Event | Category | Expected | Outcome | Credit | What the agent did |")
    L.append("|---|---|---|---|---|---|---|")
    for ev in r["events"]:
        L.append(f"| {ev['scenario_id']} | {ev['event_id']} | {ev['category']} | {ev['expected']} | {ev['outcome']} | "
                 f"{ev['credit']:.2f} | {ev.get('detail', '')} |")
    stray = [s for s in r["scenarios"] if s["spurious_stops"]]
    if stray:
        L.append("")
        L.append("## Stops when nothing was happening")
        L.append("")
        for s in stray:
            L.append(f"- {s['scenario_id']}: at {', '.join(f'{t:.0f}' for t in s['spurious_stops'])} ms (scenario time)")
    L.append("")
    L.append("Scoring rules: SCORING.md. Times are scenario time in ms, measured when your decision reached the harness.")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
