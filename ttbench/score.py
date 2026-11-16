"""Score a run: rebuild what the agent would have done from the decisions, compare with the labels.

    python -m ttbench.score --data data/dev --run runs/my-first-run

Writes score.json (machine-readable) and report.md (human-readable) into the run folder.
The rules are the ones in SCORING.md; every number here is a pure function of (labels, run log).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from .dataset import GAP_MS

# Timing thresholds. Provisional until calibrated on human agents (SCORING.md, "Thresholds").
DEFAULT_THRESHOLDS = {
    "T_full_ms": 300,       # STOP latency with full credit
    "T_dead_ms": 1500,      # STOP later than evident + T_dead = miss
    "E_full_ms": 600,       # TAKE_TURN latency with full credit
    "E_dead_ms": 2000,      # TAKE_TURN credit reaches 0.4 here
    "E_miss_ms": 3000,      # no TAKE_TURN by turn_end + E_miss = miss
    "hiccup_ms": 500,       # a STOP taken back by RESUME within this many ms counts half
    "keep_tail_ms": 500,    # KEEP window = [onset, offset + keep_tail]
    "agent_tail_ms": 200,   # agent-active time = utterance + this tail
    "anticipate_ms": 300,   # a stop may start at most this long before the caller's onset to count as yielding to it
}
CLEAN_CONDITIONS = {"quiet", "ac-hum"}


# ---------------------------------------------------------------- credit curves
def stop_credit(latency_ms: float | None, th: dict) -> float:
    if latency_ms is None:
        return 0.0
    if latency_ms <= th["T_full_ms"]:
        return 1.0
    if latency_ms <= th["T_dead_ms"]:
        return 1.0 - 0.6 * (latency_ms - th["T_full_ms"]) / (th["T_dead_ms"] - th["T_full_ms"])
    return 0.0


def eot_credit(latency_ms: float | None, th: dict) -> float:
    if latency_ms is None or latency_ms > th["E_miss_ms"]:
        return 0.0
    if latency_ms <= th["E_full_ms"]:
        return 1.0
    if latency_ms <= th["E_dead_ms"]:
        return 1.0 - 0.6 * (latency_ms - th["E_full_ms"]) / (th["E_dead_ms"] - th["E_full_ms"])
    return 0.4 * (th["E_miss_ms"] - latency_ms) / (th["E_miss_ms"] - th["E_dead_ms"])


def harmonic(a: float, b: float) -> float:
    return 0.0 if a + b == 0 else 2 * a * b / (a + b)


# ---------------------------------------------------------------- behaviour reconstruction
@dataclass
class Silence:
    utt_id: str
    start: float
    end: float | None          # RESUME time, or None = stayed silent to the end of the utterance
    active_end: float = 0.0    # end of that utterance's agent-active time: silence never outlives it

    @property
    def effective_end(self) -> float:
        return self.end if self.end is not None else self.active_end

    def resumed_within(self, ms: float) -> bool:
        return self.end is not None and self.end - self.start <= ms


def agent_silences(label: dict, decisions: list[tuple[float, str]], th: dict) -> tuple[list[Silence], int]:
    """Rebuild SPEAKING/SILENT per agent utterance. Returns silent periods and the number of ignored decisions."""
    utts = [(u["utt_id"], u["start_ms"], u["end_ms"] + th["agent_tail_ms"]) for u in label["agent_utterances"]]
    state: dict[str, Silence | None] = {u[0]: None for u in utts}
    out: list[Silence] = []
    ignored = 0
    for t, d in sorted(decisions):
        if d not in ("STOP", "RESUME"):
            continue
        active = [u for u in utts if u[1] <= t <= u[2]]
        if not active:
            ignored += 1
            continue
        uid, _, a_end = active[-1]
        cur = state[uid]
        if d == "STOP" and cur is None:
            cur = Silence(uid, t, None, a_end)
            state[uid] = cur
            out.append(cur)
        elif d == "RESUME" and cur is not None:
            cur.end = t
            state[uid] = None
        else:
            ignored += 1           # STOP while silent / RESUME while speaking: no-op
    return out, ignored


def _deadline(ev: dict, th: dict) -> float:
    anchor = ev.get("evident_ms", ev["onset_ms"])
    return anchor + th["T_dead_ms"]


def find_yield(ev: dict, silences: list[Silence], th: dict) -> Silence | None:
    """The agent yields if it is silent from some moment <= deadline until the caller's offset."""
    deadline, onset, offset = _deadline(ev, th), ev["onset_ms"], ev.get("offset_ms", ev["onset_ms"])
    best = None
    for s in silences:
        if s.start > deadline or s.effective_end <= onset:
            continue
        if s.start < onset - th["anticipate_ms"]:          # stopped long before the caller spoke: not a yield to them
            continue
        if s.end is not None and s.end < offset:          # resumed while the caller was still talking
            continue
        if best is None or s.start < best.start:
            best = s
    return best


def keep_credit(window: tuple[float, float], silences: list[Silence], th: dict) -> tuple[float, str]:
    already = [s for s in silences if s.start < window[0] and s.effective_end > window[0]]
    if already:
        return 0.0, f"agent was already silent (stopped at {already[0].start:.0f} ms)"
    inside = [s for s in silences if window[0] <= s.start <= window[1]]
    if not inside:
        return 1.0, "kept speaking"
    if any(not s.resumed_within(th["hiccup_ms"]) for s in inside):
        return 0.0, f"false stop at {inside[0].start:.0f} ms"
    return max(0.0, 1.0 - 0.5 * len(inside)), f"hiccup (stopped and resumed) x{len(inside)}"


# ---------------------------------------------------------------- one scenario
def score_scenario(label: dict, decisions: list[tuple[float, str]], th: dict) -> dict:
    silences, ignored = agent_silences(label, decisions, th)
    scen_cat = label["category"]
    events_out = []
    claimed: list[tuple[float, float]] = []        # windows where a silence is NOT spurious
    utt_ends = [(u["start_ms"], u["end_ms"] + th["agent_tail_ms"]) for u in label["agent_utterances"]]

    for ev in label["events"]:
        cat = ev.get("category", scen_cat)
        r = {"event_id": ev["event_id"], "category": cat, "expected": ev["expected"], "kind": ev["kind"], "onset_ms": ev.get("onset_ms")}
        if ev["kind"] == "interruption":
            onset = ev["onset_ms"]
            if ev["expected"] in ("STOP", "EITHER"):
                deadline = _deadline(ev, th)
                rel_end = max([deadline] + [e for s, e in utt_ends if s <= deadline and e >= onset])
                y = find_yield(ev, silences, th)
            if ev["expected"] == "STOP":
                claimed.append((onset, rel_end))
                if y:
                    lat = max(0.0, y.start - ev["evident_ms"])
                    r.update(outcome="yield", latency_ms=round(lat, 1), credit=round(stop_credit(lat, th), 4),
                             detail=f"stopped at {y.start:.0f} ms ({lat:.0f} ms after evident)")
                else:
                    r.update(outcome="miss", latency_ms=None, credit=0.0, detail="did not stop in time (or resumed over the caller)")
            elif ev["expected"] == "KEEP":
                win = (onset, ev["offset_ms"] + th["keep_tail_ms"])
                claimed.append(win)
                c, why = keep_credit(win, silences, th)
                r.update(outcome="kept" if c == 1 else ("hiccup" if c > 0 else "false_stop"), credit=c, detail=why)
            else:  # EITHER
                win = (onset, max(ev["offset_ms"] + th["keep_tail_ms"], rel_end if y else 0))
                claimed.append(win)
                pref = ev.get("preferred")
                if y:
                    lat = max(0.0, y.start - ev["evident_ms"]) if "evident_ms" in ev else 0.0
                    base = 1.0 if pref in (None, "STOP") else 0.6
                    mult = stop_credit(lat, th) if "evident_ms" in ev else 1.0
                    r.update(outcome="yield", behaviour="STOP", latency_ms=round(lat, 1), credit=round(base * mult, 4),
                             detail=f"stopped at {y.start:.0f} ms")
                else:
                    kc, why = keep_credit((onset, ev["offset_ms"] + th["keep_tail_ms"]), silences, th)
                    base = 1.0 if pref in (None, "KEEP") else 0.6
                    r.update(outcome="kept" if kc == 1 else ("hiccup" if kc > 0 else "false_stop"), behaviour="KEEP",
                             credit=round(base * kc, 4), detail=why)
        else:  # end_of_turn
            tts = sorted(t for t, d in decisions if d == "TAKE_TURN")
            pauses = ev.get("pauses", [])
            items = []
            for p in pauses:
                if p["hold"] != "required":
                    continue
                bad = [t for t in tts if p["start_ms"] <= t < p["end_ms"]]
                items.append({"item": f"pause {p['start_ms']}-{p['end_ms']}", "passed": not bad,
                              **({"take_turn_at": bad[0]} if bad else {})})
            in_pause = lambda t: any(p["start_ms"] <= t < p["end_ms"] for p in pauses)  # noqa: E731
            talk = [t for t in tts if ev["onset_ms"] <= t < ev["turn_end_ms"] and not in_pause(t)]
            items.append({"item": "while the caller speaks", "passed": not talk, **({"take_turn_at": talk[0]} if talk else {})})
            after = [t for t in tts if t >= ev["turn_end_ms"]]
            lat = (after[0] - ev["turn_end_ms"]) if after else None
            r.update(outcome="eot", patience_items=items, latency_ms=None if lat is None else round(lat, 1),
                     credit=round(eot_credit(lat, th), 4),
                     detail="; ".join([f"TAKE_TURN at {i['take_turn_at']:.0f} ms "
                                       + ("while the caller was speaking" if i["item"].startswith("while") else "during a pause the caller had not finished")
                                       for i in items if not i["passed"]]
                                      + ["no TAKE_TURN after the caller finished" if lat is None else f"TAKE_TURN {lat:.0f} ms after turn end"]))
        events_out.append(r)

    spurious = [s for s in silences if not any(a <= s.start <= b for a, b in claimed)]
    weight = sum(0.5 if s.resumed_within(th["hiccup_ms"]) else 1.0 for s in spurious)
    protected = {"category": scen_cat if scen_cat != "NX" else events_out[0]["category"],
                 "credit": max(0.0, 1.0 - weight), "spurious_stops": [round(s.start, 1) for s in spurious],
                 "spurious_weight": weight}
    agent_ms = sum(u["end_ms"] - u["start_ms"] for u in label["agent_utterances"])
    return {"scenario_id": label["scenario_id"], "events": events_out, "protected": protected,
            "ignored_decisions": ignored, "agent_speech_ms": agent_ms}


# ---------------------------------------------------------------- aggregation
def aggregate(scen_results: list[dict], labels: dict[str, dict]) -> dict:
    by_cat: dict[str, dict] = {}

    def cat(c):
        return by_cat.setdefault(c, {"stop": [], "keep": [], "protected": []})

    either, eot_items, eot_credits = [], [], []
    for sr in scen_results:
        for e in sr["events"]:
            if e["kind"] == "end_of_turn":
                eot_items += [i["passed"] for i in e["patience_items"]]
                eot_credits.append(e["credit"])
            elif e["expected"] == "STOP":
                cat(e["category"])["stop"].append(e["credit"])
            elif e["expected"] == "KEEP":
                cat(e["category"])["keep"].append(e["credit"])
            else:
                either.append(e["credit"])
        cat(sr["protected"]["category"])["protected"].append(sr["protected"]["credit"])

    cats = {}
    for c, v in sorted(by_cat.items()):
        r_c = statistics.fmean(v["stop"]) if v["stop"] else None
        # Restraint: handling of labelled KEEP moments x no stray stops in clean agent speech
        keep_m = statistics.fmean(v["keep"]) if v["keep"] else None
        prot_m = statistics.fmean(v["protected"]) if v["protected"] else None
        s_c = (keep_m if keep_m is not None else 1.0) * (prot_m if prot_m is not None else 1.0) \
            if (keep_m is not None or prot_m is not None) else None
        cats[c] = {"R": r_c, "S": s_c, "n_stop": len(v["stop"]), "stop_yielded": sum(1 for x in v["stop"] if x > 0),
                   "n_keep": len(v["keep"]), "keep_passed": sum(1 for x in v["keep"] if x == 1.0),
                   "n_scenarios": len(v["protected"]), "protected_clean": sum(1 for x in v["protected"] if x == 1.0)}
    rs = [v["R"] for v in cats.values() if v["R"] is not None]
    ss = [v["S"] for v in cats.values() if v["S"] is not None]
    R = statistics.fmean(rs) if rs else 0.0
    S = statistics.fmean(ss) if ss else 0.0
    F = harmonic(R, S)
    A = statistics.fmean(either) if either else None
    core = 100 * (0.9 * F + 0.1 * A) if A is not None else 100 * F
    P = statistics.fmean(eot_items) if eot_items else None
    Q = statistics.fmean(eot_credits) if eot_credits else None
    return {"core": {"R": R, "S": S, "F": F, "A": A, "core_score": core}, "categories": cats,
            "eot_raw": {"P": P, "Q": Q, "n_events": len(eot_credits)}}


def score(labels: dict[str, dict], run_meta: dict, log_lines: list[dict], thresholds: dict | None = None) -> dict:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    layout = sorted(run_meta["scenarios"], key=lambda s: s["start_ms"])
    per_scen: dict[str, list[tuple[float, str]]] = {s["scenario_id"]: [] for s in layout}
    any_take_turn = False
    unattributed = 0
    for line in log_lines:
        msg = line.get("msg", {})
        if line.get("dir") != "rx" or msg.get("type") != "decision" or line.get("stream_ms") is None:
            continue
        t = line["stream_ms"]
        any_take_turn |= msg["decision"] == "TAKE_TURN"
        owner = next((s for s in layout if s["start_ms"] <= t < s["start_ms"] + s["duration_ms"] + GAP_MS), None)
        if owner is None:
            unattributed += 1
            continue
        per_scen[owner["scenario_id"]].append((t - owner["start_ms"], msg["decision"]))

    scen_results = [score_scenario(labels[sid], per_scen[sid], th) for sid in per_scen]
    agg = aggregate(scen_results, labels)

    # EOT track: optional, reported separately (not in the core score)
    P, Q = agg["eot_raw"]["P"], agg["eot_raw"]["Q"]
    eot = {"attempted": any_take_turn, "P": P, "Q": Q, "n_events": agg["eot_raw"]["n_events"],
           "eot_score": (100 * harmonic(P, Q) if any_take_turn and P is not None else 0.0)}

    # diagnostics
    events = [e | {"scenario_id": sr["scenario_id"]} for sr in scen_results for e in sr["events"]]
    lats = sorted(e["latency_ms"] for e in events if e["kind"] == "interruption" and e["expected"] == "STOP"
                  and e["outcome"] == "yield")
    agent_min = sum(sr["agent_speech_ms"] for sr in scen_results) / 60000
    false_stops = sum(1 for e in events if e["expected"] == "KEEP" and e["credit"] == 0) + \
        sum(sum(1 for _ in sr["protected"]["spurious_stops"]) for sr in scen_results)
    hiccups = sum(1 for e in events if e.get("outcome") == "hiccup")
    misses = sum(1 for e in events if e["expected"] == "STOP" and e["outcome"] == "miss")

    def slice_f(pred) -> dict | None:
        sub = [sr for sr in scen_results if pred(labels[sr["scenario_id"]])]
        if not sub:
            return None
        a = aggregate(sub, labels)["core"]
        return {"n_scenarios": len(sub), "R": a["R"], "S": a["S"], "F": a["F"]}

    slices = {
        "najdi": slice_f(lambda l: l["dialect"] == "sa-najdi"),
        "other_dialects": slice_f(lambda l: l["dialect"] != "sa-najdi"),
        "clean_audio": slice_f(lambda l: set(l["acoustic"]["conditions"]) <= CLEAN_CONDITIONS),
        "noisy_or_phone": slice_f(lambda l: not set(l["acoustic"]["conditions"]) <= CLEAN_CONDITIONS),
        "scripted": slice_f(lambda l: l.get("elicitation", "scripted") == "scripted"),
        "naturalistic": slice_f(lambda l: l.get("elicitation") == "naturalistic"),
    }
    for a, b, name in (("najdi", "other_dialects", "dialect_gap"), ("clean_audio", "noisy_or_phone", "noise_gap")):
        slices[name] = (slices[a]["F"] - slices[b]["F"]) if slices[a] and slices[b] else None

    label_status = sorted({l.get("provenance", {}).get("label_status", "unknown") for l in labels.values()})
    return {
        "team": run_meta.get("hello", {}).get("team"), "system_version": run_meta.get("hello", {}).get("system_version"),
        "official_speed": run_meta.get("official", False), "speed": run_meta.get("speed"),
        "thresholds": th, "label_status": label_status,
        "core": agg["core"], "eot": eot, "categories": agg["categories"],
        "latency_ms": {"n": len(lats), "median": statistics.median(lats) if lats else None,
                       "p90": lats[min(len(lats) - 1, int(0.9 * len(lats)))] if lats else None},
        "rates": {"agent_speech_min": round(agent_min, 3), "false_stops": false_stops, "hiccups": hiccups,
                  "false_stops_per_min": round(false_stops / agent_min, 3) if agent_min else None,
                  "misses": misses, "n_stop_events": sum(1 for e in events if e["expected"] == "STOP"),
                  "unattributed_decisions": unattributed,
                  "ignored_decisions": sum(sr["ignored_decisions"] for sr in scen_results)},
        "slices": slices, "rtt_ms": run_meta.get("rtt_summary_ms"), "protocol_errors": len(run_meta.get("errors", [])),
        "events": events, "scenarios": [{"scenario_id": sr["scenario_id"], **sr["protected"]} for sr in scen_results],
    }


# ---------------------------------------------------------------- files
def load_labels(data_dir: Path) -> tuple[dict[str, dict], dict]:
    manifest = json.loads((Path(data_dir) / "manifest.json").read_text(encoding="utf-8"))
    labels = {e["scenario_id"]: json.loads((Path(data_dir) / "labels" / f"{e['scenario_id']}.json").read_text(encoding="utf-8"))
              for e in manifest["scenarios"]}
    return labels, manifest


def score_run_dir(data_dir: Path, run_dir: Path) -> dict:
    from .report import write_report
    labels, manifest = load_labels(data_dir)
    run_meta = json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
    log = [json.loads(l) for l in (Path(run_dir) / "log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    result = score(labels, run_meta, log, manifest.get("thresholds"))
    (Path(run_dir) / "score.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(result, labels, Path(run_dir) / "report.md")
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--run", required=True, type=Path)
    a = p.parse_args(argv)
    r = score_run_dir(a.data, a.run)
    print(f"Core score {r['core']['core_score']:.1f}  R {r['core']['R']:.2f}  S {r['core']['S']:.2f}  "
          f"EOT {r['eot']['eot_score']:.1f}{'' if r['eot']['attempted'] else ' (not attempted)'}   -> {a.run / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
