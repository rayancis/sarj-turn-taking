"""A local stand-in for the Gemini Live WebSocket API, for tests and offline browser checks (no key, no network).

It follows the documented protocol (setup -> setupComplete; realtimeInput activityStart / audio / activityEnd;
clientContent; serverContent with modelTurn audio, outputTranscription, interrupted, generationComplete,
turnComplete) and RECORDS everything it receives, so tests can prove who interrupted whom:
- with automatic activity detection disabled it never decides on its own that the caller spoke;
- an activityStart during generation cancels the response ("interrupted", then "turnComplete");
- audio received outside an activity is counted (it must stay 0).
Each response is the given agent audio (24 kHz PCM16), streamed faster than real time as Gemini does.

    python -m tests.fake_gemini --port 8790 --agent-wav data/dev/audio/GI-D01.wav --from-s 7.9 --seconds 12
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import threading
import time
import wave
from pathlib import Path

import numpy as np


def agent_audio_24k(wav: Path, from_s: float = 0.0, seconds: float = 10.0) -> bytes:
    """Channel 0 (agent) of a kit WAV, cut and resampled 16 -> 24 kHz (linear), as PCM16 bytes."""
    with wave.open(str(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels())[:, 0].astype(float)
    x = x[int(from_s * 16000):int((from_s + seconds) * 16000)]
    t = np.arange(int(len(x) * 1.5)) / 1.5
    return np.interp(t, np.arange(len(x)), x).round().astype("<i2").tobytes()


class FakeGemini:
    def __init__(self, response_pcm24: bytes, chunk_s: float = 0.2, chunk_delay_s: float = 0.02,
                 text: str = "أهلاً وسهلاً، معك سارة من خدمة التوصيل، كيف أقدر أساعدك اليوم؟"):
        self.pcm = response_pcm24
        self.chunk = int(24000 * chunk_s) * 2
        self.delay = chunk_delay_s
        self.text = text
        self.log: list[dict] = []
        self.setup: dict | None = None
        self.activity_open = False
        self.audio_outside_activity = 0
        self.audio_in_activity = 0
        self.port = None
        self.loop = None
        self.headers: dict = {}

    def note(self, kind, **data):
        self.log.append({"t": time.monotonic(), "kind": kind, **data})

    async def handler(self, ws):
        self.headers = dict(ws.request.headers)
        gen_task = None
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if "setup" in msg:
                    self.setup = msg["setup"]
                    self.note("setup")
                    await ws.send(json.dumps({"setupComplete": {}}))
                elif "clientContent" in msg:
                    self.note("clientContent")
                    gen_task = await self._cancel(ws, gen_task)
                    gen_task = asyncio.ensure_future(self.respond(ws))
                elif "realtimeInput" in msg:
                    ri = msg["realtimeInput"]
                    if "activityStart" in ri:
                        self.note("activityStart", generating=bool(gen_task and not gen_task.done()))
                        self.activity_open = True
                        gen_task = await self._cancel(ws, gen_task)
                    elif "activityEnd" in ri:
                        self.note("activityEnd")
                        self.activity_open = False
                        await ws.send(json.dumps({"serverContent": {"inputTranscription": {"text": "(كلام المتصل)"}}}))
                        gen_task = asyncio.ensure_future(self.respond(ws))
                    elif "audio" in ri:
                        n = len(base64.b64decode(ri["audio"]["data"]))
                        if self.activity_open:
                            self.audio_in_activity += n
                        else:
                            self.audio_outside_activity += n
        finally:
            if gen_task:
                gen_task.cancel()

    async def _cancel(self, ws, task):
        if task and not task.done():
            task.cancel()
            self.note("interrupted")
            await ws.send(json.dumps({"serverContent": {"interrupted": True}}))
            await ws.send(json.dumps({"serverContent": {"turnComplete": True}}))
        return None

    async def respond(self, ws):
        self.note("response_start")
        await ws.send(json.dumps({"serverContent": {"outputTranscription": {"text": self.text}}}))
        for i in range(0, len(self.pcm), self.chunk):
            part = self.pcm[i:i + self.chunk]
            await ws.send(json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {
                "mimeType": "audio/pcm;rate=24000", "data": base64.b64encode(part).decode()}}]}}}))
            await asyncio.sleep(self.delay)
        await ws.send(json.dumps({"serverContent": {"generationComplete": True}}))
        await ws.send(json.dumps({"serverContent": {"turnComplete": True}}))
        self.note("response_complete")

    def start(self, port: int = 0) -> str:
        """Run in a background thread; returns the ws:// URL."""
        ready = threading.Event()

        def run():
            from websockets.asyncio.server import serve

            async def main():
                async with serve(self.handler, "127.0.0.1", port, max_size=1 << 24) as srv:
                    self.port = srv.sockets[0].getsockname()[1]
                    self.loop = asyncio.get_running_loop()
                    ready.set()
                    await asyncio.Future()
            asyncio.run(main())

        threading.Thread(target=run, daemon=True).start()
        ready.wait(10)
        return f"ws://127.0.0.1:{self.port}"

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.log]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--agent-wav", type=Path, required=True)
    p.add_argument("--from-s", type=float, default=0.0)
    p.add_argument("--seconds", type=float, default=10.0)
    a = p.parse_args()
    fake = FakeGemini(agent_audio_24k(a.agent_wav, a.from_s, a.seconds))
    print(fake.start(a.port), flush=True)
    try:
        while True:
            time.sleep(5)
            print(json.dumps({"received": fake.kinds()[-12:], "audio_outside_activity": fake.audio_outside_activity,
                              "audio_in_activity": fake.audio_in_activity}, ensure_ascii=False), flush=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
