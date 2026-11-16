"""Load a benchmark split (manifest + labels + stereo WAVs) and lay it out as one continuous stream."""
from __future__ import annotations

import hashlib
import json
import random
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .protocol import FRAME_SAMPLES, SAMPLE_RATE

GAP_MS = 1500


def read_wav_stereo(path: Path) -> np.ndarray:
    """Return int16 array shape (n, 2): column 0 agent, column 1 user."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getsampwidth() != 2 or w.getnchannels() != 2:
            raise ValueError(f"{path}: need 16 kHz, 16-bit, stereo; got {w.getframerate()} Hz, "
                             f"{8 * w.getsampwidth()}-bit, {w.getnchannels()} ch")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return data.reshape(-1, 2)


def write_wav_stereo(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.ascontiguousarray(data, dtype="<i2").tobytes())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class Scenario:
    scenario_id: str
    label: dict
    audio: np.ndarray                      # (n, 2) int16, padded to whole frames
    opaque_id: str = ""
    start_ms: int = 0                      # position in the stream
    agent_utterances: list = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return len(self.audio) * 1000 // SAMPLE_RATE


def load_split(data_dir: Path, only: list[str] | None = None) -> list[Scenario]:
    data_dir = Path(data_dir)
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    scenarios = []
    for entry in manifest["scenarios"]:
        sid = entry["scenario_id"]
        if only and sid not in only:
            continue
        label = json.loads((data_dir / "labels" / f"{sid}.json").read_text(encoding="utf-8"))
        audio = read_wav_stereo(data_dir / "audio" / label["audio"]["file"])
        pad = (-len(audio)) % FRAME_SAMPLES
        if pad:
            audio = np.vstack([audio, np.zeros((pad, 2), dtype=np.int16)])
        scenarios.append(Scenario(sid, label, audio))
    if only:
        missing = set(only) - {s.scenario_id for s in scenarios}
        if missing:
            raise KeyError(f"not in {data_dir}: {sorted(missing)}")
    return scenarios


def lay_out_stream(scenarios: list[Scenario], seed: int | None = None, shuffle: bool = False,
                   opaque: bool = False) -> list[Scenario]:
    """Assign stream positions (and opaque ids for blind runs). Scenarios are separated by GAP_MS of silence."""
    rng = random.Random(seed)
    order = list(scenarios)
    if shuffle:
        rng.shuffle(order)
    t = 0
    for s in order:
        s.opaque_id = f"s{rng.getrandbits(32):08x}" if opaque else s.scenario_id
        s.start_ms = t
        s.agent_utterances = [
            {"utt_id": u["utt_id"], "start_ms": t + u["start_ms"], "end_ms": t + u["end_ms"], "text": u["text"],
             **({"words": u["words"]} if "words" in u else {})}
            for u in s.label["agent_utterances"]]
        t += s.duration_ms + GAP_MS
    return order
