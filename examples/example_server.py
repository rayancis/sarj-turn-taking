"""A bare WebSocket system, written without the ttbench helpers. Use it as a reference if you build your system in
another language, or want full control of the protocol (INTERFACE.md). Otherwise start from my_system.py.

    python examples/example_server.py --port 8765
    python -m ttbench.harness --data data/dev --url ws://127.0.0.1:8765 --out runs/example

It answers the handshake and pings, tracks when the agent is talking, and makes a deliberately naive decision from
loudness. It is not a suggested approach.
"""
import argparse
import asyncio
import json

import numpy as np
from websockets.asyncio.server import serve

TEAM = "T00-example"            # your team code
VERSION = "dev"                 # put your git commit here for the final


class MySystem:
    def __init__(self):
        self.agent_speaking = False
        self.agent_text = ""
        self.loud_frames = 0        # consecutive loud frames
        self.quiet_frames = 0       # consecutive quiet frames
        self.stopped = False
        self.caller_talked = False

    # ---- context from the harness (use it or ignore it)
    def on_message(self, msg: dict):
        if msg["type"] == "agent_utterance_start":
            self.agent_speaking, self.agent_text, self.stopped = True, msg["text"], False
        elif msg["type"] == "agent_utterance_end":
            self.agent_speaking, self.caller_talked = False, False

    # ---- one 20 ms frame: agent and user are int16 arrays of 320 samples
    def decide(self, agent: np.ndarray, user: np.ndarray) -> list[dict]:
        level_db = 20 * np.log10(np.sqrt(np.mean(user.astype(np.float64) ** 2)) / 32768 + 1e-9)
        loud = level_db > -40
        self.loud_frames = self.loud_frames + 1 if loud else 0
        self.quiet_frames = 0 if loud else self.quiet_frames + 1
        out = []
        if self.agent_speaking:
            if not self.stopped and self.loud_frames == 15:            # 300 ms of sound -> stop
                self.stopped = True
                out.append({"type": "decision", "decision": "STOP", "label": "interruption", "confidence": 0.5})
            elif self.stopped and self.quiet_frames == 25:             # quiet for 500 ms -> carry on
                self.stopped = False
                out.append({"type": "decision", "decision": "RESUME"})
        else:
            if loud:
                self.caller_talked = True
            elif self.caller_talked and self.quiet_frames == 40:       # 800 ms of quiet -> take the turn
                self.caller_talked = False
                out.append({"type": "decision", "decision": "TAKE_TURN", "label": "end_of_turn"})
        return out


async def handler(ws):
    system = MySystem()
    await ws.send(json.dumps({"type": "hello", "team": TEAM, "system_version": VERSION, "tracks": ["core", "eot"]}))
    async for raw in ws:
        if isinstance(raw, bytes):                                     # audio: 1280 bytes, int16 LE, [agent, user]
            frame = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
            for msg in system.decide(frame[:, 0], frame[:, 1]):
                await ws.send(json.dumps(msg))
            continue
        msg = json.loads(raw)
        if msg["type"] == "ping":
            await ws.send(json.dumps({"type": "pong", "id": msg["id"]}))
        elif msg["type"] == "session_end":
            break
        else:
            if msg["type"] == "scenario_start":
                print(f"scenario {msg['scenario_id']}: {msg['context']['use_case']}", flush=True)
            system.on_message(msg)


async def main(host, port):
    async with serve(handler, host, port, max_size=2 ** 20, ping_interval=None):
        print(f"Example system listening on ws://{host}:{port}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0", help="0.0.0.0 so the organisers' harness can reach you on the LAN")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    try:
        asyncio.run(main(a.host, a.port))
    except KeyboardInterrupt:
        pass
