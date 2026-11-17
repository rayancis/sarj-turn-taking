"""Streaming wrappers for pretrained neural VADs, fed with the challenge's 20 ms / 320-sample frames.

Each wrapper returns one speech probability per 20 ms frame and keeps its recurrent state between frames;
reset() starts a new call. Models are loaded from .local/models (python tools/get_models.py).

Buffering and delay (16 kHz):
  Silero VAD v6 works on 512-sample (32 ms) windows plus 64 samples of context. 20 ms frames are queued and the
  model runs whenever 512 new samples are available (5 frames = 3.125 windows), so a frame's probability is the
  most recent window's output: it can lag the newest audio by up to 31 ms and covers the last 32 ms.
  TEN VAD works on 160-sample (10 ms) hops, so each 20 ms frame is exactly two hops and there is no extra buffering;
  the frame's probability is the larger of the two.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

MODELS = Path(__file__).resolve().parent / ".local" / "models"


class SileroStream:
    name, licence = "silero-vad 6.2.3 (ONNX)", "MIT"
    WINDOW, CONTEXT = 512, 64

    _session = None                 # loaded once per process, shared by every instance (the state is per instance)

    def __init__(self, path: Path | None = None):
        if SileroStream._session is None:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = opts.intra_op_num_threads = 1
            SileroStream._session = ort.InferenceSession(
                str(path or MODELS / "silero_vad" / "silero_vad" / "data" / "silero_vad.onnx"),
                sess_options=opts, providers=["CPUExecutionProvider"])
        self.session = SileroStream._session
        self.sr = np.array(16000, dtype=np.int64)
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(self.CONTEXT, dtype=np.float32)
        self.pending = np.zeros(0, dtype=np.float32)
        self.prob = 0.0

    def __call__(self, frame: np.ndarray) -> float:
        self.pending = np.concatenate([self.pending, frame.astype(np.float32) / 32768.0])
        while len(self.pending) >= self.WINDOW:
            chunk, self.pending = self.pending[:self.WINDOW], self.pending[self.WINDOW:]
            x = np.concatenate([self.context, chunk])[None, :]
            out, self.state = self.session.run(None, {"input": x, "state": self.state, "sr": self.sr})
            self.context = x[0, -self.CONTEXT:]
            self.prob = float(out[0, 0])
        return self.prob


class TenStream:
    name, licence = "ten-vad 1.0.6.9 (native)", "Apache-2.0 with additional conditions (Agora)"
    HOP = 160

    def __init__(self):
        sys.path.insert(0, str(MODELS / "ten_vad"))
        from ten_vad import TenVad
        self._cls = TenVad
        self.reset()

    def reset(self):
        self.vad = self._cls(self.HOP, 0.5)             # the library has no state reset: a new handle per call

    def __call__(self, frame: np.ndarray) -> float:
        frame = np.ascontiguousarray(frame, dtype=np.int16)
        return max(self.vad.process(frame[:self.HOP])[0], self.vad.process(frame[self.HOP:])[0])


def load(name: str):
    return {"silero": SileroStream, "ten": TenStream}[name]()
