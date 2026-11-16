"""Reference lines (not targets, not recommendations) and test fixtures.

    python -m ttbench.baselines b0 --port 8765      # never stops
    python -m ttbench.baselines b1 --port 8765      # stops at any sound in the caller channel

B0 and B1 only give the scoreboard a scale. "always" exists for tests and demos: it stops at the start of every
agent utterance, the worst possible restraint.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math

import numpy as np
from websockets.asyncio.server import serve

from .protocol import DEFAULT_PORT, FRAME_MS


class Policy:
    """Decide from one 20 ms frame; return a list of decision strings."""
    name = "base"

    def on_control(self, msg: dict) -> list[str]:
        return []

    def on_frame(self, agent: np.ndarray, user: np.ndarray) -> list[str]:
        return []


class NeverStop(Policy):
    name = "B0-never-stop"


class AlwaysStop(Policy):
    name = "always-stop"

    def on_control(self, msg):
        return ["STOP"] if msg["type"] == "agent_utterance_start" else []


class AnySound(Policy):
    """B1: STOP after 200 ms of caller-channel energy while the agent talks; TAKE_TURN after 700 ms of quiet."""
    name = "B1-any-sound"

    def __init__(self, threshold_db: float = -42.0):
        self.th = threshold_db
        self.agent_active = False
        self.loud_ms = 0
        self.quiet_ms = 0
        self.heard_caller = False
        self.stopped = False

    def on_control(self, msg):
        if msg["type"] == "agent_utterance_start":
            self.agent_active, self.stopped, self.loud_ms = True, False, 0
        elif msg["type"] == "agent_utterance_end":
            self.agent_active, self.heard_caller, self.quiet_ms = False, False, 0
        return []

    def on_frame(self, agent, user):
        rms = math.sqrt(float(np.mean(user.astype(np.float64) ** 2)) + 1e-9) / 32768
        loud = 20 * math.log10(rms + 1e-12) > self.th
        out = []
        if self.agent_active:
            self.loud_ms = self.loud_ms + FRAME_MS if loud else 0
            if self.loud_ms >= 200 and not self.stopped:
                self.stopped = True
                out.append("STOP")
        else:
            if loud:
                self.heard_caller, self.quiet_ms = True, 0
            elif self.heard_caller:
                self.quiet_ms += FRAME_MS
                if self.quiet_ms >= 700:
                    self.heard_caller = False
                    out.append("TAKE_TURN")
        return out


POLICIES = {"b0": NeverStop, "b1": AnySound, "always": AlwaysStop}


async def serve_policy(policy_factory, host: str = "127.0.0.1", port: int = DEFAULT_PORT, team: str | None = None,
                       ready: asyncio.Event | None = None, stop: asyncio.Event | None = None):
    async def handler(ws):
        pol = policy_factory()
        await ws.send(json.dumps({"type": "hello", "team": team or pol.name, "system_version": "reference",
                                  "tracks": ["core", "eot"]}))
        async for raw in ws:
            if isinstance(raw, bytes):
                frame = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
                for d in pol.on_frame(frame[:, 0], frame[:, 1]):
                    await ws.send(json.dumps({"type": "decision", "decision": d}))
                continue
            msg = json.loads(raw)
            if msg["type"] == "ping":
                await ws.send(json.dumps({"type": "pong", "id": msg["id"]}))
            elif msg["type"] == "session_end":
                break
            else:
                for d in pol.on_control(msg):
                    await ws.send(json.dumps({"type": "decision", "decision": d}))

    async with serve(handler, host, port, max_size=2 ** 20, ping_interval=None):
        if ready:
            ready.set()
        await (stop.wait() if stop else asyncio.Future())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("policy", choices=sorted(POLICIES))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = p.parse_args()
    print(f"Serving {POLICIES[a.policy].name} on ws://{a.host}:{a.port} (Ctrl-C to stop)")
    try:
        asyncio.run(serve_policy(POLICIES[a.policy], a.host, a.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
