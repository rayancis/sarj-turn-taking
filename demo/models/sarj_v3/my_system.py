"""Entry point for the Sarj turn-taking benchmark.

All logic lives in tt/ (pure, testable). This file only adapts it to the kit's
`System` interface and makes sure nothing ever raises out of a callback.

Kit interface (ttbench/server.py): on_scenario_start(context),
on_agent_start(text, duration_ms), on_agent_end(), on_frame(agent, caller)
-> None | "STOP" | "RESUME" | "TAKE_TURN".
"""
from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tt.config import Config  # noqa: E402
from tt.decider import Decider  # noqa: E402
from tt.words import WordsWorker, shared_backend  # noqa: E402

try:  # the kit provides the base class and the WebSocket runner
    from ttbench.server import System, run  # type: ignore
except Exception:  # pragma: no cover - lets tools/ run offline without the kit
    run = None

    class System:  # minimal stand-in
        def on_scenario_start(self, context): ...


TEAM = "my-team"            # TODO: real team name


def _config_from_env() -> Config:
    """TT_CFG="stop_after_ms=480,soft_stop=1" for scorer sweeps."""
    cfg = Config()
    spec = os.environ.get("TT_CFG", "").strip()
    for item in filter(None, (s.strip() for s in spec.split(","))):
        k, v = item.split("=", 1)
        cfg.override(**{k.strip(): v.strip()})
    return cfg


class MySystem(System):
    def __init__(self):
        self.cfg = _config_from_env()
        self.words = self._load_words()      # loaded once, before the first call
        # one line for dashboards / the demo page: which layers are really running
        self.demo_note = ("acoustics + words (Whisper tiny, offline)" if self.words is not None
                          else "acoustics only (words layer off or ASR model missing)")
        self.dec = Decider(self.cfg, words=self.words)
        self.debug = bool(os.environ.get("TT_DEBUG"))
        self._slow = 0

    # ------------------------------------------------------------- callbacks
    def _load_words(self):
        """Offline ASR for the words layer; None (acoustics only) if unavailable."""
        if not self.cfg.use_words:
            return None
        path = self.cfg.asr_model_dir
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
        try:
            return WordsWorker(shared_backend(path))
        except Exception:
            # no model / no faster-whisper: run on acoustics alone, never crash
            print(f"[tt] words layer disabled: cannot load ASR from {path}", file=sys.stderr)
            return None

    def on_scenario_start(self, context=None):
        """New call: fresh echo/noise/caller-level state (one connection serves many calls)."""
        try:
            self.dec = Decider(self.cfg, words=self.words)
        except Exception:
            self._err()

    def on_agent_start(self, *args, **kwargs):
        try:
            self.dec.agent_start(*args, **kwargs)
        except Exception:
            self._err()

    def on_agent_end(self, *args, **kwargs):
        try:
            self.dec.agent_end(*args, **kwargs)
        except Exception:
            self._err()

    def on_frame(self, agent, caller):
        t0 = time.perf_counter()
        try:
            action = self.dec.frame(agent, caller)
        except Exception:
            self._err()
            action = None
        dt = time.perf_counter() - t0
        if dt > 0.010:
            self._slow += 1
            if self.debug:
                print(f"[tt] slow frame {dt*1000:.1f} ms", file=sys.stderr)
        if action and self.debug:
            print(f"[tt] {self.dec.log[-1]}", file=sys.stderr)
        return action

    def _err(self):
        if getattr(self, "debug", True):
            traceback.print_exc()


if __name__ == "__main__":
    run(MySystem, TEAM, __file__)
