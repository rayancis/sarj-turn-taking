"""Turns a plain Python class into a system the harness can talk to. You should not need to edit this file.

Your class (see my_system.py) gets told what is happening and sees every 20 ms of audio:

    on_scenario_start(context)          a new call starts (context: use_case, language)
    on_agent_start(text, duration_ms)   the agent starts an utterance; you get its full text
    on_agent_end()                      the agent finished that utterance
    on_frame(agent, caller) -> ...      20 ms of audio: two int16 numpy arrays of 320 samples (16 kHz)

on_frame returns nothing (keep doing what you are doing) or a decision: "STOP", "RESUME", "TAKE_TURN", or a dict
{"decision": "STOP", "label": "interruption", "confidence": 0.8} (label and confidence never change your score).

Everything else in INTERFACE.md (hello, ping/pong, frame parsing) is handled here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

import numpy as np
from websockets.asyncio.server import serve

from .protocol import DEFAULT_PORT

DECISIONS = ("STOP", "RESUME", "TAKE_TURN")


class System:
    """Base class: every method is optional. Override what you need."""

    def on_scenario_start(self, context: dict) -> None: ...
    def on_agent_start(self, text: str, duration_ms: int) -> None: ...
    def on_agent_end(self) -> None: ...
    def on_frame(self, agent: np.ndarray, caller: np.ndarray):
        return None


def git_version(folder: Path) -> str:
    """Your commit hash (plus '-dirty' if you have uncommitted changes); 'dev' outside a git repository."""
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=folder, capture_output=True, text=True, timeout=5)
        if h.returncode != 0:
            return "dev"
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=folder,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return h.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "dev"


def _as_messages(out) -> list[dict]:
    if out is None:
        return []
    items = out if isinstance(out, list) else [out]
    msgs = []
    for d in items:
        m = {"decision": d} if isinstance(d, str) else dict(d)
        if m.get("decision") not in DECISIONS:
            raise ValueError(f"on_frame returned {d!r}: a decision must be one of {DECISIONS}")
        msgs.append({"type": "decision", **m})
    return msgs


async def _serve(make_system, team: str, version: str, host: str, port: int, verbose: bool):
    async def handler(ws):
        system = make_system()
        await ws.send(json.dumps({"type": "hello", "team": team, "system_version": version, "tracks": ["core", "eot"]}))
        async for raw in ws:
            if isinstance(raw, bytes):                       # 1,280 bytes: 320 frames x [agent, caller] int16
                frame = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
                for m in _as_messages(system.on_frame(frame[:, 0], frame[:, 1])):
                    await ws.send(json.dumps(m))
                continue
            msg = json.loads(raw)
            t = msg["type"]
            if t == "ping":
                await ws.send(json.dumps({"type": "pong", "id": msg["id"]}))
            elif t == "session_end":
                break
            elif t == "scenario_start":
                if verbose:
                    print(f"  call {msg['scenario_id']}: {msg['context'].get('use_case', '')}", flush=True)
                system.on_scenario_start(msg.get("context", {}))
            elif t == "agent_utterance_start":
                system.on_agent_start(msg.get("text", ""), msg.get("duration_ms", 0))
            elif t == "agent_utterance_end":
                system.on_agent_end()

    async with serve(handler, host, port, max_size=2 ** 20, ping_interval=None):
        print(f"{team} ({version}) listening on ws://{host}:{port}", flush=True)
        await asyncio.Future()


def run(make_system, team: str, here: str | Path) -> None:
    """Command line for a system file: python my_system.py [--port 8765] [--host 0.0.0.0]."""
    p = argparse.ArgumentParser(description="Serve this system to the harness.")
    p.add_argument("--host", default="127.0.0.1",
                   help="127.0.0.1 for local tests; 0.0.0.0 when the organisers' harness must reach your laptop")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()
    version = git_version(Path(here).resolve().parent)
    try:
        asyncio.run(_serve(make_system, team, version, a.host, a.port, not a.quiet))
    except KeyboardInterrupt:
        pass
