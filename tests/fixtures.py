"""A tiny synthetic split (tones and noise bursts, no speech) so tests run anywhere, offline, in seconds."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ttbench.dataset import sha256_file, write_wav_stereo

SR = 16000


def _tone(ms, f=220, db=-20):
    t = np.arange(int(SR * ms / 1000)) / SR
    return (10 ** (db / 20)) * np.sin(2 * np.pi * f * t)


def _burst(ms, db=-18, seed=0):
    rng = np.random.default_rng(seed)
    return (10 ** (db / 20)) * rng.standard_normal(int(SR * ms / 1000)) * 0.5


def _place(buf, start_ms, sig):
    a = int(SR * start_ms / 1000)
    buf[a:a + len(sig)] += sig[: max(0, len(buf) - a)]


def _scenario(sid, cat, dur_ms, agent, user, events, dialect="sa-najdi", cond=("quiet",)):
    n = int(SR * dur_ms / 1000)
    a, u = np.zeros(n), np.zeros(n)
    utts = []
    for i, (s, e) in enumerate(agent):
        _place(a, s, _tone(e - s))
        utts.append({"utt_id": f"A{i + 1}", "start_ms": s, "end_ms": e, "text": "نص تجريبي"})
    for k, (s, e) in enumerate(user):
        _place(u, s, _burst(e - s, seed=k))
    audio = (np.stack([a, u], axis=1) * 32767).clip(-32768, 32767).astype(np.int16)
    label = {"schema_version": "1.0", "scenario_id": sid, "split": "dev", "category": cat, "difficulty": 1,
             "dialect": dialect, "speakers": [{"speaker_id": "S999", "role": "caller", "source": "synthetic", "gender": "m",
                                               "age_band": "25-34", "dialect": dialect}],
             "agent_voice": "agent-f1", "acoustic": {"conditions": list(cond)},
             "audio": {"file": f"{sid}.wav", "sample_rate": 16000, "channels": ["agent", "user"], "duration_ms": dur_ms,
                       "sha256": "0" * 64},
             "context": {"use_case": "test", "language": "ar"}, "agent_utterances": utts, "user_segments": [],
             "events": events, "provenance": {"script_ref": "tests", "production_mode": "synthetic",
                                              "rendered_on": "2026-11-14", "reviewed_by": ["none"],
                                              "label_status": "script_derived"}}
    return label, audio


def tiny_split() -> list[tuple[dict, np.ndarray]]:
    return [
        _scenario("GI-D90", "GI", 7000, [(0, 6500)], [(2000, 3500)],
                  [{"event_id": "GI-D90.e1", "kind": "interruption", "expected": "STOP", "onset_ms": 2000,
                    "evident_ms": 2300, "offset_ms": 3500, "deadline_ms": 3800, "rationale": "t"}]),
        _scenario("BC-D90", "BC", 7000, [(0, 6500)], [(2000, 2300)],
                  [{"event_id": "BC-D90.e1", "kind": "interruption", "expected": "KEEP", "onset_ms": 2000,
                    "offset_ms": 2300, "window_end_ms": 2800, "rationale": "t"}], dialect="gulf-kuwaiti",
                  cond=("phone-nb",)),
        _scenario("HT-D90", "HT", 9000, [(0, 2000)], [(2600, 3200), (4200, 5000)],
                  [{"event_id": "HT-D90.e1", "kind": "end_of_turn", "expected": "TAKE_TURN", "onset_ms": 2600,
                    "turn_end_ms": 5000, "pauses": [{"start_ms": 3200, "end_ms": 4200, "hold": "required"}],
                    "rationale": "t"}]),
        _scenario("AM-D90", "AM", 7000, [(0, 6500)], [(2000, 2300)],
                  [{"event_id": "AM-D90.e1", "kind": "interruption", "expected": "EITHER", "preferred": "KEEP",
                    "onset_ms": 2000, "offset_ms": 2300, "window_end_ms": 2800, "rationale": "t"}]),
    ]


def write_split(root: Path) -> Path:
    (root / "labels").mkdir(parents=True, exist_ok=True)
    entries = []
    for label, audio in tiny_split():
        wav = root / "audio" / label["audio"]["file"]
        write_wav_stereo(wav, audio)
        label["audio"]["sha256"] = sha256_file(wav)
        (root / "labels" / f"{label['scenario_id']}.json").write_text(json.dumps(label, ensure_ascii=False), encoding="utf-8")
        entries.append({"scenario_id": label["scenario_id"]})
    (root / "manifest.json").write_text(json.dumps({"dataset": "test", "split": "dev", "scenarios": entries}), encoding="utf-8")
    return root
