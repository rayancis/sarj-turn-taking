"""Real-time evaluation harness.

Connects to a team's WebSocket server, streams a benchmark split frame by frame at real-time pace,
sends the control messages, and logs every message the system sends back, stamped with the stream
time at the moment it ARRIVED. Then (unless --no-score) scores the run.

    python -m ttbench.harness --data data/dev --url ws://127.0.0.1:8765 --out runs/my-first-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from . import __version__
from .dataset import GAP_MS, Scenario, lay_out_stream, load_split, sha256_file
from .protocol import CHANNELS, FRAME_BYTES, FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE, validate_client_message

PING_EVERY_FRAMES = 250          # every 5 s of stream
HELLO_TIMEOUT_S = 10
TAIL_MS = 1000                   # silence streamed after the last scenario


def build_stream(scenarios: list[Scenario]) -> tuple[np.ndarray, dict[int, list[dict]]]:
    """One (n, 2) int16 array for the whole session + control messages keyed by frame index."""
    end_ms = max(s.start_ms + s.duration_ms for s in scenarios) + TAIL_MS
    n_frames = -(-end_ms // FRAME_MS)
    stream = np.zeros((n_frames * FRAME_SAMPLES, 2), dtype=np.int16)
    controls: dict[int, list[dict]] = {}

    def at(ms: int, msg: dict, ceil: bool = False):
        f = -(-ms // FRAME_MS) if ceil else ms // FRAME_MS
        controls.setdefault(f, []).append(msg)

    for s in scenarios:
        a = s.start_ms * SAMPLE_RATE // 1000
        stream[a:a + len(s.audio)] = s.audio
        at(s.start_ms, {"type": "scenario_start", "scenario_id": s.opaque_id, "stream_ms": s.start_ms,
                        "context": {"use_case": s.label["context"]["use_case"], "language": "ar"}})
        for u in s.agent_utterances:
            msg = {"type": "agent_utterance_start", "utt_id": u["utt_id"], "stream_ms": u["start_ms"],
                   "text": u["text"], "duration_ms": u["end_ms"] - u["start_ms"]}
            if "words" in u:
                msg["words"] = u["words"]
            at(u["start_ms"], msg)
            at(u["end_ms"], {"type": "agent_utterance_end", "utt_id": u["utt_id"], "stream_ms": u["end_ms"]},
               ceil=True)
        end = s.start_ms + s.duration_ms
        at(end, {"type": "scenario_end", "scenario_id": s.opaque_id, "stream_ms": end}, ceil=True)
    # keep control messages in stream-time order within a frame
    for msgs in controls.values():
        msgs.sort(key=lambda m: (m["stream_ms"], m["type"] != "scenario_end"))
    return stream, controls


async def run_session(url: str, scenarios: list[Scenario], out_dir: Path, speed: float = 1.0,
                      start_at: float | None = None, session_id: str = "dev", quiet: bool = False) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stream, controls = build_stream(scenarios)
    n_frames = len(stream) // FRAME_SAMPLES
    frame_ns = FRAME_MS * 1_000_000 / speed
    log_f = open(out_dir / "log.jsonl", "w", encoding="utf-8")
    seq = 0
    meta: dict = {"harness_version": __version__, "url": url, "speed": speed, "session_id": session_id,
                  "official": speed == 1.0, "frames_planned": n_frames, "errors": [], "rtt_ms": []}
    t0_ns = None
    pings: dict[int, int] = {}

    def say(text: str):
        if not quiet:
            print(text, flush=True)

    def log(direction: str, msg: dict, stream_ms: float | None):
        nonlocal seq
        seq += 1
        log_f.write(json.dumps({"seq": seq, "dir": direction, "t_mono_ns": time.monotonic_ns(),
                                "stream_ms": None if stream_ms is None else round(stream_ms, 2), "msg": msg},
                               ensure_ascii=False) + "\n")

    def stream_ms_now() -> float | None:
        return None if t0_ns is None else (time.monotonic_ns() - t0_ns) / 1e6 * speed

    async with connect(url, max_size=2 ** 20, ping_interval=None, open_timeout=10) as ws:
        # 1. hello
        try:
            raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise SystemExit(f"No hello within {HELLO_TIMEOUT_S} s. Your server must send "
                             '{"type":"hello","team":...,"system_version":...} right after the connection opens.')
        hello = json.loads(raw)
        err = validate_client_message(hello)
        if err or hello.get("type") != "hello":
            raise SystemExit(f"First message must be a valid hello: {err or hello}")
        meta["hello"] = hello
        log("rx", hello, None)
        say(f"Connected: team {hello['team']} ({hello['system_version']}), tracks {hello.get('tracks', ['core'])}")

        session_start = {"type": "session_start", "session_id": session_id, "sample_rate": SAMPLE_RATE,
                         "frame_ms": FRAME_MS, "channels": list(CHANNELS)}
        if start_at:
            wait = start_at - time.time()
            if wait > 0:
                say(f"Waiting {wait:.1f} s for the shared start signal ...")
                await asyncio.sleep(wait)
        await ws.send(json.dumps(session_start))
        log("tx", session_start, 0.0)

        # 2. receiver: stamps every message on arrival
        decisions = 0

        async def receiver():
            nonlocal decisions
            try:
                async for raw in ws:
                    sm = stream_ms_now()
                    try:
                        msg = json.loads(raw)
                    except (TypeError, ValueError):
                        meta["errors"].append({"stream_ms": sm, "error": "non-JSON message"})
                        continue
                    err = validate_client_message(msg)
                    if err:
                        meta["errors"].append({"stream_ms": sm, "error": err, "msg": msg})
                        log("rx_invalid", msg, sm)
                        continue
                    if msg["type"] == "pong" and msg["id"] in pings:
                        meta["rtt_ms"].append(round((time.monotonic_ns() - pings.pop(msg["id"])) / 1e6, 2))
                    if msg["type"] == "decision":
                        decisions += 1
                    log("rx", msg, sm)
            except ConnectionClosed:
                pass

        # 3. sender: real-time pacing against an absolute clock (no drift)
        t0_ns = time.monotonic_ns()
        rx_task = asyncio.create_task(receiver())
        max_lag_ms = 0.0
        ping_id = 0
        try:
            for n in range(n_frames):
                target = t0_ns + n * frame_ns
                delay = (target - time.monotonic_ns()) / 1e9
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    max_lag_ms = max(max_lag_ms, -delay * 1000)
                for msg in controls.get(n, []):
                    await ws.send(json.dumps(msg, ensure_ascii=False))
                    log("tx", msg, n * FRAME_MS)
                    if msg["type"] == "scenario_start":
                        say(f"  [{n * FRAME_MS / 1000:7.1f} s] scenario {msg['scenario_id']}")
                frame = stream[n * FRAME_SAMPLES:(n + 1) * FRAME_SAMPLES]
                await ws.send(frame.astype("<i2").tobytes())
                if n % PING_EVERY_FRAMES == 0:
                    ping_id += 1
                    pings[ping_id] = time.monotonic_ns()
                    await ws.send(json.dumps({"type": "ping", "id": ping_id}))
            await ws.send(json.dumps({"type": "session_end"}))
            log("tx", {"type": "session_end"}, stream_ms_now())
            await asyncio.sleep(1.0)
            meta["completed"] = True
        except ConnectionClosed as e:
            meta["completed"] = False
            meta["errors"].append({"stream_ms": stream_ms_now(), "error": f"connection closed by the system: {e}"})
            say(f"Connection closed by the system at stream {stream_ms_now():.0f} ms")
        finally:
            rx_task.cancel()
            log_f.close()

    meta.update({"frames_sent": n_frames if meta.get("completed") else None, "max_send_lag_ms": round(max_lag_ms, 2),
                 "decisions_received": decisions, "frame_bytes": FRAME_BYTES, "gap_ms": GAP_MS,
                 "scenarios": [{"scenario_id": s.scenario_id, "opaque_id": s.opaque_id, "start_ms": s.start_ms,
                                "duration_ms": s.duration_ms} for s in scenarios]})
    if meta["rtt_ms"]:
        r = sorted(meta["rtt_ms"])
        meta["rtt_summary_ms"] = {"median": r[len(r) // 2], "max": r[-1], "n": len(r)}
    (out_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True, type=Path, help="split folder with manifest.json, labels/, audio/")
    p.add_argument("--url", default="ws://127.0.0.1:8765", help="your system's WebSocket server")
    p.add_argument("--out", required=True, type=Path, help="run folder to write log.jsonl, run.json, score.json, report.md")
    p.add_argument("--speed", type=float, default=1.0,
                   help="1.0 = real time (official). >1 only for quick local debugging; latency is then not meaningful")
    p.add_argument("--scenarios", help="comma-separated scenario ids (default: all)")
    p.add_argument("--shuffle", action="store_true", help="random scenario order (use with --seed)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--opaque", action="store_true", help="send random scenario ids (as in the final)")
    p.add_argument("--start-at", type=float, default=None, help="unix time for a shared start across teams")
    p.add_argument("--session-id", default="dev")
    p.add_argument("--no-score", action="store_true", help="log only (e.g. a sealed run)")
    a = p.parse_args(argv)

    only = a.scenarios.split(",") if a.scenarios else None
    scenarios = lay_out_stream(load_split(a.data, only), seed=a.seed, shuffle=a.shuffle, opaque=a.opaque)
    total_s = (scenarios[-1].start_ms + scenarios[-1].duration_ms) / 1000
    print(f"Streaming {len(scenarios)} scenarios, {total_s:.0f} s of audio, speed {a.speed}x "
          f"(about {total_s / a.speed:.0f} s) to {a.url}")
    try:
        meta = asyncio.run(run_session(a.url, scenarios, a.out, a.speed, a.start_at, a.session_id))
    except OSError as e:
        print(f"Could not connect to {a.url}: {e}. Is your server running? See TROUBLESHOOTING.md.", file=sys.stderr)
        return 2
    manifest = a.data / "manifest.json"
    meta["dataset"] = {"path": str(a.data), "manifest_sha256": sha256_file(manifest)}
    (a.out / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Done: {meta['decisions_received']} decisions, {len(meta['errors'])} protocol errors, "
          f"max send lag {meta['max_send_lag_ms']} ms. Log: {a.out / 'log.jsonl'}")
    if not a.no_score:
        from .score import score_run_dir
        result = score_run_dir(a.data, a.out)
        print(f"Core score {result['core']['core_score']:.1f}  (R {result['core']['R']:.2f}, "
              f"S {result['core']['S']:.2f})   Report: {a.out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
