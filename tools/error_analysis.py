"""Remaining-error breakdown for a system on a split (diagnosis only: it reads labels, the detector never does).

    python tools/error_analysis.py --system my_system.py                 # dev
    python tools/error_analysis.py --system my_system.py --data data/extra/xdev

For every labelled interruption event and every stray stop it prints the scorer's verdict next to signal
measurements taken from the caller channel:

  dur      length of the labelled sound (ms)
  rel      its level relative to the caller's own speech earlier in the same call, in dB: 90th percentile of the
           300-3400 Hz band level inside the event minus the same statistic over the caller's turns while the agent
           was silent (the caller reference; "-" when the caller did not speak before the event)
  snr      the same 90th percentile minus the 10th percentile of the band level over the whole call (dB)
  voiced   share of the event's frames that are periodic (>= 0.45)
  echo     median over the event of the best normalised cross-correlation between the caller frame and the agent
           channel at lags 0-500 ms (1.0 = the caller channel is a delayed copy of the agent)

The last block groups the lost Restraint by cause, using the scenario category/subtype and the label kinds.
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

KIT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(KIT), str(KIT / "tools")]

from replay import load_system, replay  # noqa: E402
from ttbench.dataset import lay_out_stream, load_split  # noqa: E402
from ttbench.score import load_labels, score  # noqa: E402

SR, N = 16000, 320


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)


def band_levels(x: np.ndarray) -> np.ndarray:
    win = np.hanning(N)
    f = np.fft.rfftfreq(N, 1 / SR)
    band = (f >= 300) & (f <= 3400)
    fr = x[:len(x) // N * N].reshape(-1, N).astype(np.float64) / 32768 * win
    p = (np.abs(np.fft.rfft(fr, axis=1)) ** 2)[:, band].sum(axis=1) / (N * np.sum(win ** 2) / 2)
    return np.maximum(-120.0, 10 * np.log10(p + 1e-30))


def periodicities(x: np.ndarray, fn) -> np.ndarray:
    n = len(x) // N
    out = np.zeros(n)
    prev = np.zeros(N, np.int16)
    for i in range(n):
        cur = x[i * N:(i + 1) * N]
        out[i] = fn(np.concatenate([prev, cur]))
        prev = cur
    return out


def echo_similarity(agent: np.ndarray, caller: np.ndarray, a_ms: int, b_ms: int, max_lag_ms: int = 500) -> float:
    """Median over 100 ms caller blocks of the peak normalised cross-correlation with the earlier agent signal."""
    vals = []
    block, lag = SR // 10, SR * max_lag_ms // 1000
    for s in range(a_ms * SR // 1000, b_ms * SR // 1000 - block, block):
        c = caller[s:s + block].astype(np.float64)
        c -= c.mean()
        ref = agent[max(0, s - lag):s + block].astype(np.float64)
        if len(ref) < block + 1 or np.sum(c ** 2) == 0:
            continue
        num = np.correlate(ref, c, "valid")
        den = np.sqrt(np.convolve(ref ** 2, np.ones(block), "valid") * np.sum(c ** 2)) + 1e-9
        vals.append(float(np.max(np.abs(num) / den)))
    return float(np.median(vals)) if vals else 0.0


def caller_reference(label: dict, lv: np.ndarray, before_ms: int) -> float | None:
    """90th percentile band level of the caller's own turns (kind 'speech', agent silent) that end before before_ms."""
    agent = [(u["start_ms"], u["end_ms"]) for u in label["agent_utterances"]]
    frames = []
    for seg in label.get("user_segments", []):
        if seg["kind"] != "speech" or seg["end_ms"] > before_ms:
            continue
        if any(a < seg["end_ms"] and seg["start_ms"] < b for a, b in agent):
            continue
        frames += list(lv[seg["start_ms"] // 20:seg["end_ms"] // 20])
    return float(np.percentile(frames, 90)) if frames else None


def cause(label: dict, ev: dict | None) -> str:
    sub = label.get("subtype", "")
    kinds = {s["seg_id"]: s["kind"] for s in label.get("user_segments", [])}
    if "echo" in sub:
        return "agent echo"
    if ev is None:
        return "stray: " + ("background speech" if "tv" in sub or "side_speech" in sub else sub or "unknown")
    seg_kinds = {kinds.get(s) for s in ev.get("segment_ids", [])}
    if "tv" in sub or "side_speech" in sub or "side_speech" in seg_kinds:
        return "background speech"
    if label["category"] == "BG":
        return "background non-speech (horn, engine, noise)"
    if "nonverbal" in seg_kinds or label["category"] == "NV":
        return "caller non-verbal (cough, laugh, breath)"
    if "filler" in seg_kinds:
        return "caller filler / hesitation"
    if "backchannel" in seg_kinds or label["category"] in ("BC", "DV", "OV", "HT"):
        return "caller backchannel / supportive speech"
    return "other"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--system", default="my_system.py")
    p.add_argument("--data", type=Path, default=KIT / "data" / "dev")
    p.add_argument("--json", type=Path, default=None, help="also write the rows as JSON")
    a = p.parse_args(argv)

    make, team = load_system(Path(a.system).resolve())
    periodicity = make.on_frame.__globals__["periodicity"]      # the system's own voicing measure

    scenarios = lay_out_stream(load_split(a.data, None))
    log, meta = replay(make, scenarios, team)
    labels, manifest = load_labels(a.data)
    res = score(labels, meta, log, manifest.get("thresholds"))
    by_scen: dict[str, list[dict]] = {}
    for e in res["events"]:
        by_scen.setdefault(e["scenario_id"], []).append(e)
    stray = {s["scenario_id"]: s for s in res["scenarios"]}

    rows, lost = [], {}
    cats = res["categories"]
    print(f"{'event':12s} {'exp':6s} {'outcome':10s} {'dur':>5s} {'rel':>6s} {'snr':>5s} {'voiced':>6s} {'echo':>5s}  cause / detail")
    for sid, label in labels.items():
        x = read_wav(a.data / "audio" / label["audio"]["file"])
        lv = band_levels(x[:, 1])
        per = periodicities(x[:, 1], periodicity)
        floor = float(np.percentile(lv, 10))
        n_keep = sum(1 for e in by_scen.get(sid, []) if e["expected"] == "KEEP")
        for e in by_scen.get(sid, []):
            if e["kind"] != "interruption":
                continue
            ev = next(v for v in label["events"] if v["event_id"] == e["event_id"])
            a_ms, b_ms = ev["onset_ms"], ev["offset_ms"]
            seg = lv[a_ms // 20:max(a_ms // 20 + 1, b_ms // 20)]
            ref = caller_reference(label, lv, a_ms)
            p90 = float(np.percentile(seg, 90))
            row = {"scenario": sid, "event": e["event_id"], "category": e["category"], "expected": e["expected"],
                   "outcome": e["outcome"], "credit": e["credit"], "dur_ms": b_ms - a_ms,
                   "rel_caller_db": None if ref is None else round(p90 - ref, 1), "snr_db": round(p90 - floor, 1),
                   "voiced": round(float(np.mean(per[a_ms // 20:max(a_ms // 20 + 1, b_ms // 20)] >= 0.45)), 2),
                   "echo": round(echo_similarity(x[:, 0], x[:, 1], a_ms, b_ms), 2),
                   "cause": cause(label, ev) if e["expected"] != "STOP" else "", "detail": e.get("detail", "")}
            rows.append(row)
            rel = "-" if row["rel_caller_db"] is None else f"{row['rel_caller_db']:+.1f}"
            print(f"{row['event']:12s} {row['expected']:6s} {row['outcome']:10s} {row['dur_ms']:5d} {rel:>6s} "
                  f"{row['snr_db']:5.1f} {row['voiced']:6.2f} {row['echo']:5.2f}  {row['cause']} | {row['detail']}")
            if e["expected"] == "KEEP" and e["credit"] < 1:
                lost.setdefault(row["cause"], []).append(f"{e['event_id']} ({e['category']})")
        for t in stray[sid]["spurious_stops"]:
            c = cause(label, None)
            rows.append({"scenario": sid, "event": f"{sid}.stray@{t:.0f}", "category": label["category"],
                         "expected": "-", "outcome": "stray_stop", "cause": c})
            print(f"{sid + '.stray':12s} {'-':6s} {'stray_stop':10s} {'':5s} {'':6s} {'':5s} {'':6s} {'':5s}  {c} | stop at {t:.0f} ms")
            lost.setdefault(c, []).append(f"{sid} stray@{t:.0f}")

    c = res["core"]
    print(f"\nCore {c['core_score']:.2f}  R {c['R']:.3f}  S {c['S']:.3f}  misses {res['rates']['misses']}  "
          f"false stops {res['rates']['false_stops']}")
    print("Restraint per category: " + "  ".join(f"{k} {v['S']:.2f}" for k, v in cats.items() if v["S"] is not None))
    print("\nLost KEEP events and stray stops by cause:")
    for k, v in sorted(lost.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(v):2d}  {k}: {', '.join(v)}")
    if a.json:
        a.json.write_text(json.dumps({"core": c, "categories": cats, "rows": rows}, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
