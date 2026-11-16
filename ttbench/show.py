"""Look inside the example calls: who speaks when, what they say, and what a good agent should do.

    python -m ttbench.show              # all 24 development calls, one line each
    python -m ttbench.show GI-D01       # one call, as a timeline
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "dev"
NAMES = {"GI": "real interruption", "BC": "backchannel", "HT": "hesitation", "BG": "background sound",
         "NV": "cough, laugh or echo", "AM": "ambiguous word", "OV": "both start at once", "DV": "dialect variation"}


def s(ms) -> str:
    return f"{ms / 1000:5.1f}"


def should(ev: dict) -> str:
    if ev["kind"] == "end_of_turn":
        holds = [p for p in ev.get("pauses", []) if p["hold"] == "required"]
        wait = "; wait through the pause at " + ", ".join(f"{p['start_ms'] / 1000:.1f}–{p['end_ms'] / 1000:.1f} s" for p in holds) if holds else ""
        return f"TAKE_TURN soon after {ev['turn_end_ms'] / 1000:.1f} s (the caller finishes){wait}"
    if ev["expected"] == "STOP":
        return f"STOP: clear at {ev['evident_ms'] / 1000:.1f} s, full credit by {(ev['evident_ms'] + 300) / 1000:.1f} s"
    if ev["expected"] == "KEEP":
        return "KEEP talking: the caller does not want the floor"
    return f"either is fine (preferred: {ev.get('preferred') or 'none'})"


def overview() -> None:
    man = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    print(f"{len(man['scenarios'])} example calls (synthetic voices; see DATA.md). Look at one: python -m ttbench.show <id>\n")
    for e in man["scenarios"]:
        L = json.loads((DATA / "labels" / f"{e['scenario_id']}.json").read_text(encoding="utf-8"))
        ev = L["events"][0]
        what = NAMES.get(L["category"], L["category"])
        print(f"  {L['scenario_id']}  {what:<22} {L['dialect']:<14} {L['audio']['duration_ms'] / 1000:4.0f} s   good agent: "
              f"{'TAKE_TURN' if ev['kind'] == 'end_of_turn' else ev['expected']}")


def one(sid: str) -> None:
    L = json.loads((DATA / "labels" / f"{sid}.json").read_text(encoding="utf-8"))
    print(f"{sid} · {NAMES.get(L['category'], L['category'])} · {L['dialect']} · {L['context']['use_case']} · "
          f"{L['audio']['duration_ms'] / 1000:.1f} s\n")
    rows = [(u["start_ms"], u["end_ms"], "AGENT ", u["text"]) for u in L["agent_utterances"]]
    rows += [(u["start_ms"], u["end_ms"], "CALLER", u.get("text") or f"[{u.get('kind', 'sound')}]") for u in L.get("user_segments", [])]
    marks = {}
    for ev in L["events"]:
        for sg in ev.get("segment_ids", []):
            marks[sg] = should(ev)
    seg_by_start = {u["start_ms"]: u["seg_id"] for u in L.get("user_segments", [])}
    for a, b, who, txt in sorted(rows):
        print(f"  {s(a)}–{s(b)} s  {who}  {txt}")
        sg = seg_by_start.get(a) if who == "CALLER" else None
        if sg in marks:
            print(f"  {'':15}  ↳ {marks[sg]}")
    print("\nWhat a good agent does:")
    for ev in L["events"]:
        at = ev.get("onset_ms", 0) / 1000
        print(f"  at {at:.1f} s ({NAMES.get(ev.get('category', L['category']), '')}): {should(ev)}")
    print(f"\nAudio: data/dev/audio/{L['audio']['file']}  (left channel: agent, right channel: caller)")


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    overview() if not args else one(args[0].upper())
    return 0


if __name__ == "__main__":
    sys.exit(main())
