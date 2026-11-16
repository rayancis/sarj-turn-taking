"""Render what the caller would have heard: the agent channel goes quiet at each STOP (80 ms fade) and
comes back at RESUME, mixed with the caller channel. For the showcase, and for your own debugging.

    python -m ttbench.render --run runs/<name> --scenario BC-D03          # writes runs/<name>/BC-D03-heard.wav
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

from .dataset import read_wav_stereo
from .score import DEFAULT_THRESHOLDS, agent_silences, load_labels

SR = 16000
FADE = int(0.08 * SR)


def heard(audio: np.ndarray, label: dict, decisions: list[tuple[float, str]]) -> np.ndarray:
    agent = audio[:, 0].astype(np.float64)
    gain = np.ones(len(agent))
    utt_end = {u["utt_id"]: u["end_ms"] for u in label["agent_utterances"]}
    for s in agent_silences(label, decisions, DEFAULT_THRESHOLDS)[0]:
        a = int(SR * s.start / 1000)
        b = int(SR * (s.end if s.end is not None else utt_end[s.utt_id] + 200) / 1000)
        gain[a:b] = 0
        gain[a:a + FADE] = np.linspace(1, 0, len(gain[a:a + FADE]))
    mix = 0.5 * agent * gain + 0.5 * audio[:, 1].astype(np.float64)
    return np.clip(mix * 1.6, -32768, 32767).astype(np.int16)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "dev")
    p.add_argument("--run", required=True, type=Path)
    p.add_argument("--scenario", required=True)
    p.add_argument("--out", type=Path, help="default: <run>/<scenario>-heard.wav")
    a = p.parse_args(argv)
    a.out = a.out or a.run / f"{a.scenario}-heard.wav"
    labels, _ = load_labels(a.data)
    label = labels[a.scenario]
    meta = json.loads((a.run / "run.json").read_text(encoding="utf-8"))
    pos = next(s for s in meta["scenarios"] if s["scenario_id"] == a.scenario)
    dec = []
    for line in (a.run / "log.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        m = d["msg"]
        if d["dir"] == "rx" and m.get("type") == "decision" and d["stream_ms"] is not None:
            t = d["stream_ms"] - pos["start_ms"]
            if 0 <= t < pos["duration_ms"]:
                dec.append((t, m["decision"]))
    audio = read_wav_stereo(a.data / "audio" / label["audio"]["file"])
    out = heard(audio, label, dec)
    beep = (0.25 * 32767 * np.sin(2 * np.pi * 880 * np.arange(int(0.15 * SR)) / SR)).astype(np.int16)
    for t, d in dec:                                   # a short beep where your agent would start answering
        if d == "TAKE_TURN":
            i = int(SR * t / 1000)
            seg = out[i:i + len(beep)]
            out[i:i + len(seg)] = np.clip(seg.astype(np.int32) + beep[:len(seg)], -32768, 32767).astype(np.int16)
    with wave.open(str(a.out), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(out.tobytes())
    print(f"{a.out}: {len(out) / SR:.1f} s. Your agent goes quiet at each STOP; a beep marks each TAKE_TURN "
          f"({sum(1 for _, d in dec if d == 'STOP')} STOP, {sum(1 for _, d in dec if d == 'TAKE_TURN')} TAKE_TURN).")


if __name__ == "__main__":
    main()
