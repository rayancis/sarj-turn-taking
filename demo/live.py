"""Live AI conversation: a Gemini Live voice agent whose barge-in is controlled by SARJ.

Who decides what (the point of the demo):
- Gemini Live runs with automatic activity detection DISABLED. It therefore never detects speech and never
  interrupts itself; it only learns about the caller through the activityStart / activityEnd signals this module
  sends (official API: realtimeInputConfig.automaticActivityDetection.disabled, activityHandling
  START_OF_ACTIVITY_INTERRUPTS).
- While the agent is speaking, the caller's microphone audio is NOT sent to Gemini. It goes, together with the agent
  audio the browser actually played in the same 20 ms frame, to SARJ v3 alone. Only when the controlling detector returns STOP does this module flush
  the browser's playback, send activityStart (which cancels any ongoing generation) and stream the caller's audio,
  starting with a bounded pre-roll so the beginning of the interruption is not lost. No STOP: the agent keeps talking
  and Gemini never hears the caller (a backchannel stays a backchannel).
- While the agent is silent, a simple level gate opens the caller's turn (activityStart + pre-roll) and the
  controlling detector's own end-of-turn output (TAKE_TURN, the kit's end-of-turn rule) closes it (activityEnd),
  with a silence fallback. These turn signals are logged separately from barge-in.

Frames from the browser: 1 flag byte + 1,280 bytes = one kit frame (320 samples x [agent, caller], int16 LE, 16 kHz).
Flag bit 0: agent audio was being played in this frame; bit 1: the playback queue was empty at its end.
The permanent API key stays on the server (GEMINI_API_KEY); the browser never receives it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass

import numpy as np

from ttbench.protocol import FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE
from ttbench.server import _as_messages

LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
MODEL_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}"
PREFERRED_MODELS = ("gemini-3.8-live", "gemini-3.1-flash-live-preview")   # official docs, 2026-11
# Feature flag (env LIVE_MODE): "off" = no live sessions; "hidden" = works, but the page shows the section only with
# ?live=1 (a controlled test); "on" = shown to every visitor. Without GEMINI_API_KEY it is always unavailable.
LIVE_DEFAULT_MODE = "hidden"
FRAME_BYTES = FRAME_SAMPLES * 2 * 2
OUT_RATE = 24000                    # Gemini Live output: 16-bit PCM, 24 kHz, mono
LOUD_DB = -42.0                     # the kit's loudness rule (idle caller-turn gate)
PRE_ROLL_IDLE_MS = 400              # audio sent before activityStart when the caller starts a turn
PRE_ROLL_BARGE_MS = 1500            # ... and when SARJ stops the agent (bounded caller pre-roll)
QUIET_FALLBACK_MS = 1500            # end the caller's turn after this much quiet if no TAKE_TURN came
MAX_USER_TURN_MS = 20000
SEND_EVERY_FRAMES = 2               # caller audio to Gemini in 40 ms chunks (docs: 20-40 ms)

SYSTEM_INSTRUCTION = (
    "أنت موظف خدمة عملاء لطيف في شركة توصيل سعودية، تتكلم باللهجة السعودية البيضاء بشكل طبيعي وودود. "
    "ردودك قصيرة إلى متوسطة: من جملتين إلى أربع جمل. "
    "المتصل قد يقاطعك في أي لحظة؛ إذا قاطعك فاعتبر أنه لم يسمع بقية كلامك، وتعامل مباشرة مع ما قاله. "
    "إذا قال كلمة موافقة قصيرة مثل «إيه» أو «طيب» أثناء كلامك فأنت لن تسمعها، فهذا طبيعي. "
    "لا تذكر أنك نموذج ذكاء اصطناعي إلا إذا سُئلت.")
GREETING_PROMPT = "(بدأت المكالمة الآن. رحّب بالمتصل باختصار، عرّف بنفسك، واسأله كيف تقدر تساعده.)"


def loudness_db(samples: np.ndarray) -> float:
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2)) / 32768
    return 20 * np.log10(rms + 1e-12)


KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def find_api_key(env) -> tuple[str | None, str | None]:
    """(variable name, key). Accepts GEMINI_API_KEY or GOOGLE_API_KEY, tolerating case, spaces around the name and
    quotes or spaces around the value (common when pasting into a hosting dashboard). The key is never logged."""
    for name in KEY_NAMES:
        v = (env.get(name) or "").strip().strip("'\"").strip()
        if v:
            return name, v
    for k, v in env.items():
        if k.strip().upper().replace("-", "_") in KEY_NAMES:
            v = (v or "").strip().strip("'\"").strip()
            if v:
                return k, v
    return None, None


def similar_key_names(env) -> list[str]:
    """Names (never values) of variables that look like a Gemini key, for diagnosing a misnamed variable."""
    return sorted(repr(k) for k in env if "GEMINI" in k.upper() or "GOOGLE" in k.upper())


@dataclass
class LiveSettings:
    api_key: str | None = None
    url: str = LIVE_URL
    model: str | None = None            # None: first available of PREFERRED_MODELS
    voice: str | None = None
    max_concurrent: int = 2
    max_seconds: int = 180
    per_hour: int = 30
    per_day: int = 100
    verify_model: bool = True
    mode: str = LIVE_DEFAULT_MODE
    key_variable: str | None = None
    v3_verified: bool = False  # set only after genuine connection + v3-controlled interruption validation

    @classmethod
    def from_env(cls) -> "LiveSettings":
        e = os.environ
        url = e.get("GEMINI_LIVE_URL") or LIVE_URL
        key_name, key = find_api_key(e)
        return cls(api_key=key, key_variable=key_name, url=url, model=e.get("GEMINI_LIVE_MODEL") or None,
                   voice=e.get("GEMINI_LIVE_VOICE") or None,
                   max_concurrent=int(e.get("LIVE_MAX_CONCURRENT", "2")), max_seconds=int(e.get("LIVE_MAX_SECONDS", "180")),
                   per_hour=int(e.get("LIVE_PER_HOUR", "30")), per_day=int(e.get("LIVE_PER_DAY", "100")),
                   verify_model=url == LIVE_URL,
                   mode=(e.get("LIVE_MODE") or LIVE_DEFAULT_MODE).strip().lower(),
                   v3_verified=e.get("LIVE_V3_VERIFIED") == "1")


class LiveGate:
    """Process-wide usage limits for the paid-for resource: concurrency, sessions per hour and per day, and a
    cooldown after Gemini reports exhausted quota. In memory (a restart resets the counters)."""

    def __init__(self, s: LiveSettings):
        self.s = s
        self.lock = threading.Lock()
        self.active = 0
        self.starts: deque = deque()
        self.cooldown_until = 0.0
        self.cooldown_reason = ""
        self.model: str | None = s.model
        self.model_checked = 0.0
        self.model_error: str | None = None
        self.checks: list = []
        self.last_check = 0.0

    def status(self) -> dict:
        now = time.time()
        if self.s.mode == "off":
            return {"enabled": False, "visible": False, "reason": "The live agent is switched off on this server."}
        if not self.s.api_key:
            return {"enabled": False, "visible": False,
                    "reason": "The live agent is not configured on this server (no Gemini API key)."}
        visible = self.s.mode == "on" and self.s.v3_verified
        if now < self.cooldown_until:
            return {"enabled": False, "visible": visible, "reason": self.cooldown_reason}
        return {"enabled": True, "visible": visible, "model": self.model or PREFERRED_MODELS[0],
                "max_seconds": self.s.max_seconds, "max_concurrent": self.s.max_concurrent,
                "busy": self.active >= self.s.max_concurrent}

    def acquire(self) -> None:
        now = time.time()
        with self.lock:
            st = self.status()
            if not st["enabled"]:
                raise LiveRefused(st["reason"])
            while self.starts and now - self.starts[0] > 86400:
                self.starts.popleft()
            if self.active >= self.s.max_concurrent:
                raise LiveRefused(f"The live agent is busy ({self.s.max_concurrent} conversations at once). Try again in a minute.")
            if sum(1 for t in self.starts if now - t < 3600) >= self.s.per_hour:
                raise LiveRefused("The hourly limit of live conversations is reached. Try again later.")
            if len(self.starts) >= self.s.per_day:
                raise LiveRefused("The daily limit of live conversations is reached. Try again tomorrow.")
            self.active += 1
            self.starts.append(now)

    def release(self) -> None:
        with self.lock:
            self.active = max(0, self.active - 1)

    def quota_exhausted(self, detail: str) -> None:
        with self.lock:
            self.cooldown_until = time.time() + 900
            self.cooldown_reason = "The Gemini free-tier quota is exhausted for now. The live agent is paused for 15 minutes."
            self.model_error = detail

    def check(self) -> dict:
        """Model availability as the API reports it now (no key or secret in the result)."""
        self.checks = []
        try:
            model = self.resolve_model(force=True)
            return {"ok": True, "model": model, "tried": self.checks}
        except LiveRefused as e:
            return {"ok": False, "error": str(e), "tried": self.checks}

    def resolve_model(self, force: bool = False) -> str:
        """The configured model, or the first of PREFERRED_MODELS that the API reports as supporting the Live API
        (checked against the models endpoint, cached for an hour)."""
        with self.lock:
            if not force and self.model and (not self.s.verify_model or time.time() - self.model_checked < 3600):
                return self.model
        candidates = [self.s.model] if self.s.model else list(PREFERRED_MODELS)
        if not self.s.verify_model:
            return candidates[0]
        last = None
        for m in candidates:
            req = urllib.request.Request(MODEL_URL.format(model=m), headers={"x-goog-api-key": self.s.api_key or ""})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    info = json.loads(r.read())
                methods = info.get("supportedGenerationMethods", [])
                self.checks.append({"model": m, "status": 200, "live": "bidiGenerateContent" in methods,
                                    "display_name": info.get("displayName")})
                if "bidiGenerateContent" in methods or not methods:
                    with self.lock:
                        self.model, self.model_checked = m, time.time()
                    return m
                last = f"{m}: no Live API support"
            except urllib.error.HTTPError as e:
                self.checks.append({"model": m, "status": e.code})
                last = f"{m}: HTTP {e.code}"
                if e.code in (401, 403):
                    raise LiveRefused("The Gemini API key was rejected. Check GEMINI_API_KEY on the server.")
                if e.code == 429:
                    self.quota_exhausted(last)
                    raise LiveRefused(self.cooldown_reason)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                self.checks.append({"model": m, "error": type(e).__name__})
                last = f"{m}: {type(e).__name__}"
        raise LiveRefused(f"No Gemini Live model is available ({last}).")


class LiveRefused(Exception):
    pass


# ------------------------------------------------------------------------------------------------ WebSocket (server)
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSClosed(Exception):
    pass


class ServerWebSocket:
    """Minimal RFC 6455 server side over an http.server connection (text, binary, ping/pong, close)."""

    MAX_MESSAGE = 1 << 16

    def __init__(self, rfile, wfile):
        self.r, self.w = rfile, wfile
        self.wlock = threading.Lock()
        self.closed = False

    @staticmethod
    def accept_key(key: str) -> str:
        return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()

    def _read(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.r.read(n - len(data))
            if not chunk:
                raise WSClosed("connection closed")
            data += chunk
        return data

    def recv(self):
        """Next text (str) or binary (bytes) message."""
        parts, kind = [], None
        while True:
            b1, b2 = self._read(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            masked, n = b2 & 0x80, b2 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            if n > self.MAX_MESSAGE or not masked:
                self.close(1009 if masked else 1002)
                raise WSClosed("message too large" if masked else "unmasked client frame")
            mask = self._read(4)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n))) if n < 64 else \
                (np.frombuffer(self._read(n), np.uint8) ^ np.resize(np.frombuffer(mask, np.uint8), n)).tobytes()
            if op == 0x8:
                self.close()
                raise WSClosed("closed by peer")
            if op == 0x9:
                self._send(0xA, payload)
                continue
            if op == 0xA:
                continue
            if op in (0x1, 0x2):
                kind, parts = op, [payload]
            elif op == 0x0 and kind:
                parts.append(payload)
            if sum(len(p) for p in parts) > self.MAX_MESSAGE:
                self.close(1009)
                raise WSClosed("message too large")
            if fin and kind:
                data = b"".join(parts)
                return data.decode("utf-8") if kind == 0x1 else data

    def _send(self, op: int, payload: bytes) -> None:
        n = len(payload)
        head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else
                                     bytes([126]) + n.to_bytes(2, "big") if n < 65536 else
                                     bytes([127]) + n.to_bytes(8, "big"))
        with self.wlock:
            if self.closed and op != 0x8:
                raise WSClosed("closed")
            try:
                self.w.write(head + payload)
                self.w.flush()
            except OSError as e:
                self.closed = True
                raise WSClosed(str(e))

    def send_json(self, obj) -> None:
        self._send(0x1, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def send_bytes(self, data: bytes) -> None:
        self._send(0x2, data)

    def close(self, code: int = 1000) -> None:
        if not self.closed:
            try:
                self._send(0x8, code.to_bytes(2, "big"))
            except WSClosed:
                pass
            self.closed = True


# ------------------------------------------------------------------------------------------------ Gemini client
class GeminiLive:
    """One Live API connection (websockets sync client). The key is sent in the x-goog-api-key header, not the URL."""

    def __init__(self, settings: LiveSettings, model: str, on_message, on_close):
        from websockets.sync.client import connect
        headers = {"x-goog-api-key": settings.api_key or ""}
        kwargs = {"additional_headers": headers, "open_timeout": 15, "max_size": 1 << 24, "ping_interval": 20}
        if settings.url.startswith("wss://"):
            kwargs["ssl"] = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
        else:
            kwargs["proxy"] = None              # a local test server: never through an HTTP proxy
        self._cm = connect(settings.url, **kwargs)        # websockets >= 17: a context manager
        self.ws = self._cm.__enter__()
        self.lock = threading.Lock()
        self.on_message, self.on_close = on_message, on_close
        setup = {"model": f"models/{model}",
                 "generationConfig": {"responseModalities": ["AUDIO"]},
                 "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
                 "realtimeInputConfig": {"automaticActivityDetection": {"disabled": True},
                                         "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
                                         "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY"},
                 "inputAudioTranscription": {}, "outputAudioTranscription": {}}
        if settings.voice:
            setup["generationConfig"]["speechConfig"] = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": settings.voice}}}
        self.send({"setup": setup})
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def send(self, obj) -> None:
        with self.lock:
            self.ws.send(json.dumps(obj))

    def audio(self, pcm16k: bytes) -> None:
        self.send({"realtimeInput": {"audio": {"mimeType": "audio/pcm;rate=16000",
                                               "data": base64.b64encode(pcm16k).decode()}}})

    def _loop(self):
        code, reason = None, ""
        try:
            for raw in self.ws:
                self.on_message(json.loads(raw))
        except Exception as e:  # noqa: BLE001 - reported to the session
            reason = str(e)
        finally:
            try:
                code = self.ws.close_code
                reason = self.ws.close_reason or reason
            except Exception:  # noqa: BLE001
                pass
            self.on_close(code, reason)

    def close(self):
        try:
            self._cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------------------------------------ one conversation
@dataclass
class Turn:
    """Playback bookkeeping for one Gemini response."""
    gen: int
    complete: bool = False          # Gemini sent turnComplete for it
    started: bool = False           # first audio of it reached the browser
    interrupted: bool = False


class LiveSession:
    def __init__(self, ws: ServerWebSocket, models: dict, gate: LiveGate, controller: str = "v3",
                 clock=time.monotonic):
        self.ws, self.gate, self.clock = ws, gate, clock
        if controller != "v3":
            raise LiveRefused("Only SARJ v3 can control this conversation.")
        if "v3" not in models or not models["v3"].available:
            raise LiveRefused("SARJ v3 is unavailable; the conversation cannot start.")
        self.controller = "v3"
        self.models = models
        self.detectors = {mid: models[mid].new_system() for mid in ("v3",)}
        for d in self.detectors.values():
            d.on_scenario_start({"use_case": "live conversation", "language": "ar"})
        self.t0 = clock()
        self.n = 0                              # frames received
        self.agent_talking = False              # an agent utterance is open for the detectors
        self.utt = 0
        self.gen = 0                            # playback generation in the browser
        self.turn: Turn | None = None           # the Gemini response currently produced/played
        self.awaiting_reply = False
        self.user_open = False                  # caller activity open on the Gemini side
        self.user_open_frame = 0
        self.user_open_by = ""
        self.quiet_frames = 0
        self.loud_recent: deque = deque(maxlen=3)
        self.ring: deque = deque(maxlen=PRE_ROLL_BARGE_MS // FRAME_MS)
        self.out_buf = bytearray()
        self.gemini: GeminiLive | None = None
        self.lock = threading.RLock()
        self.ended = False
        self.end_reason = ""
        self.log = deque(maxlen=512)
        self.stats = {"frames": 0, "stops": {"v3": 0}, "barge_ins": 0, "activity_start_after_stop": 0,
                      "activity_start_turn": 0, "activity_end": 0, "gemini_interrupted": 0,
                      "gemini_interrupted_unrequested": 0, "agent_audio_ms": 0, "caller_audio_to_gemini_ms": 0,
                      "playback_stop_ms": deque(maxlen=128)}
        self.pending_stop_frame: int | None = None
        self.requested_interrupt = 0
        self.requested_interrupt_cause = None
        self.drop_old = False

    # ---- helpers
    def t_ms(self) -> float:
        return round((self.clock() - self.t0) * 1000, 1)

    def event(self, kind: str, **data) -> None:
        e = {"type": "event", "kind": kind, "t_ms": self.t_ms(), "frame": self.n, **data}
        self.log.append(e)
        self._to_browser(e)

    def _to_browser(self, obj) -> None:
        try:
            self.ws.send_json(obj)
        except WSClosed:
            self.end("browser disconnected")

    # ---- Gemini side
    def connect(self, settings: LiveSettings) -> None:
        model = self.gate.resolve_model()
        self.model = model
        self.gemini = GeminiLive(settings, model, self.on_gemini, self.on_gemini_closed)
        self.event("gemini_connecting", model=model)

    def on_gemini(self, msg: dict) -> None:
        with self.lock:
            if self.ended:
                return
            if "setupComplete" in msg:
                self.event("gemini_ready", model=self.model)
                self._to_browser({"type": "ready", "model": self.model, "controller": self.controller,
                                  "max_seconds": self.gate.s.max_seconds})
                self.awaiting_reply = True
                self.gemini.send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": GREETING_PROMPT}]}],
                                                    "turnComplete": True}})
                return
            sc = msg.get("serverContent")
            if sc:
                self._server_content(sc)
            if "goAway" in msg:
                self.event("gemini_go_away", time_left=msg["goAway"].get("timeLeft"))
                self._to_browser({"type": "notice", "message": "Gemini will close this conversation soon (session limit)."})
            if "usageMetadata" in msg:
                self._to_browser({"type": "usage", "tokens": msg["usageMetadata"].get("totalTokenCount")})

    def _server_content(self, sc: dict) -> None:
        if sc.get("interrupted"):
            # With automatic activity detection disabled Gemini cannot detect speech itself: an interruption must
            # follow an activityStart we sent. Anything else would be Gemini interrupting on its own.
            self.stats["gemini_interrupted"] += 1
            requested = self.requested_interrupt > 0
            cause = self.requested_interrupt_cause if requested else None
            self.requested_interrupt = 0
            self.requested_interrupt_cause = None
            if not requested:
                self.stats["gemini_interrupted_unrequested"] += 1
            self.event("gemini_interrupted", requested_by_us=requested,
                       authorization="v3_STOP" if cause == "barge_in" else ("idle_turn" if cause else "none"))
            if self.turn:
                self.turn.interrupted = True
        for part in (sc.get("modelTurn") or {}).get("parts", []):
            blob = part.get("inlineData")
            if not blob or not blob.get("mimeType", "").startswith("audio/pcm"):
                continue
            if self.drop_old:                   # the rest of a response SARJ already stopped
                continue
            if self.turn is None or self.turn.complete or self.turn.interrupted:
                if self.user_open:              # a response to a turn we have not finished: never play over the caller
                    continue
                self.gen += 1
                # A manual activity signal for an earlier response cannot authorize an interruption of this one.
                self.requested_interrupt = 0
                self.requested_interrupt_cause = None
                self.turn = Turn(self.gen)
                self.awaiting_reply = False
                self.event("agent_response_started", gen=self.gen)
            pcm = base64.b64decode(blob["data"])
            self.stats["agent_audio_ms"] += len(pcm) * 1000 // (2 * OUT_RATE)
            try:
                self.ws.send_bytes(bytes([self.turn.gen & 0xFF]) + pcm)
            except WSClosed:
                self.end("browser disconnected")
                return
        if sc.get("outputTranscription", {}).get("text"):
            self._to_browser({"type": "caption", "who": "agent", "text": sc["outputTranscription"]["text"]})
        if sc.get("inputTranscription", {}).get("text"):
            self._to_browser({"type": "caption", "who": "user", "text": sc["inputTranscription"]["text"]})
        if sc.get("turnComplete"):
            if self.drop_old:
                self.drop_old = False
                if self.turn:
                    self.turn.complete = True
                self.event("gemini_turn_complete", gen=self.turn.gen if self.turn else None, interrupted=True)
            elif self.turn and not self.turn.complete:
                self.turn.complete = True
                self.event("gemini_turn_complete", gen=self.turn.gen, interrupted=self.turn.interrupted)
            elif self.awaiting_reply:
                self.awaiting_reply = False
                self.event("gemini_turn_complete", gen=None, note="no audio (the model chose not to answer)")

    def on_gemini_closed(self, code, reason) -> None:
        text = f"{code or ''} {reason or ''}".strip()
        low = text.lower()
        if any(k in low for k in ("quota", "resource_exhausted", "rate limit", "exceeded")):
            self.gate.quota_exhausted(text)
            msg = self.gate.cooldown_reason
        elif self.ended:
            return
        else:
            msg = f"The Gemini connection closed ({text or 'no reason given'})."
        self._to_browser({"type": "error", "message": msg, "fatal": True})
        self.end("gemini closed: " + text)

    def _send_activity(self, kind: str, why: str) -> None:
        if not self.gemini:
            return
        self.gemini.send({"realtimeInput": {kind: {}}})
        if kind == "activityStart":
            self.user_open, self.user_open_frame, self.user_open_by = True, self.n, why
            self.quiet_frames = 0
            self.requested_interrupt += 1
            self.requested_interrupt_cause = why
            self.stats["activity_start_after_stop" if why == "barge_in" else "activity_start_turn"] += 1
        else:
            self.user_open = False
            self.stats["activity_end"] += 1
            self.awaiting_reply = True
        self.event(kind, why=why, authorization="v3_STOP" if why == "barge_in" else "idle_turn")

    def _send_caller(self, frames: list[bytes]) -> None:
        self.out_buf += b"".join(frames)
        if len(self.out_buf) >= SEND_EVERY_FRAMES * FRAME_SAMPLES * 2:
            self.gemini.audio(bytes(self.out_buf))
            self.stats["caller_audio_to_gemini_ms"] += len(self.out_buf) * 1000 // (2 * SAMPLE_RATE)
            self.out_buf.clear()

    def _flush_caller(self) -> None:
        if self.out_buf and self.gemini:
            self.gemini.audio(bytes(self.out_buf))
            self.stats["caller_audio_to_gemini_ms"] += len(self.out_buf) * 1000 // (2 * SAMPLE_RATE)
            self.out_buf.clear()

    # ---- browser frames: the SARJ loop, every 20 ms
    def on_frame(self, data: bytes) -> None:
        if len(data) != 1 + FRAME_BYTES:
            raise ValueError("bad frame")
        flags, raw = data[0], data[1:]
        playing, queue_empty = bool(flags & 1), bool(flags & 2)
        frame = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)      # as ttbench.server parses a harness frame
        agent, caller = frame[:, 0], frame[:, 1]
        with self.lock:
            if self.ended:
                return
            n = self.n
            self.stats["frames"] += 1
            if self.pending_stop_frame is not None and not playing:
                self.stats["playback_stop_ms"].append((n - self.pending_stop_frame) * FRAME_MS)
                self.event("agent_playback_stopped", after_stop_ms=(n - self.pending_stop_frame) * FRAME_MS)
                self.pending_stop_frame = None
            # agent utterance boundaries, from what was actually played
            if not self.agent_talking and playing and self.turn and not self.turn.interrupted:
                self.agent_talking = True
                self.turn.started = True
                self.utt += 1
                for d in self.detectors.values():
                    d.on_agent_start("", 0)
                self.event("agent_utterance_start", utt=self.utt, gen=self.turn.gen)
            elif self.agent_talking and not playing and queue_empty and self.turn and self.turn.complete:
                self._end_utterance("played to the end")
            # Sole production detector, using the synchronized played-agent/caller frame
            decisions = {}
            for mid, d in self.detectors.items():
                for m in _as_messages(d.on_frame(agent, caller)):
                    decisions[mid] = m["decision"]
                    controls = mid == self.controller
                    if m["decision"] == "STOP":
                        self.stats["stops"][mid] += 1
                    self._to_browser({"type": "decision", "model": mid, "decision": m["decision"], "frame": n,
                                      "t_ms": n * FRAME_MS, "utt": self.utt if self.agent_talking else None,
                                      "controls": controls})
            loud = loudness_db(caller) > LOUD_DB
            self.loud_recent.append(loud)
            self.ring.append(caller.astype("<i2").tobytes())
            ctrl = decisions.get(self.controller)
            if self.agent_talking:
                if ctrl == "STOP":
                    self._barge_in(n)
            elif self.user_open:
                self._send_caller([self.ring[-1]])
                self.quiet_frames = 0 if loud else self.quiet_frames + 1
                if ctrl == "TAKE_TURN":
                    self._close_user("TAKE_TURN")
                elif self.quiet_frames * FRAME_MS >= QUIET_FALLBACK_MS:
                    self._close_user("silence fallback")
                elif (n - self.user_open_frame) * FRAME_MS >= MAX_USER_TURN_MS:
                    self._close_user("max turn length")
            elif sum(self.loud_recent) >= 2 and not playing:
                pre = list(self.ring)[-(PRE_ROLL_IDLE_MS // FRAME_MS):]
                self._send_activity("activityStart", "caller_turn")
                self._send_caller(pre)
            self.n += 1

    def _end_utterance(self, why: str) -> None:
        self.agent_talking = False
        for d in self.detectors.values():
            d.on_agent_end()
        self.event("agent_utterance_end", utt=self.utt, why=why)

    def _barge_in(self, n: int) -> None:
        """The controlling detector said STOP while the agent was speaking."""
        self.stats["barge_ins"] += 1
        old = self.turn.gen if self.turn else None
        self.gen += 1
        self._to_browser({"type": "flush", "gen": self.gen, "stopped_gen": old, "frame": n,
                          "by": self.controller})
        if self.turn:
            self.turn.interrupted = True        # nothing more of this response is played
            if not self.turn.complete:
                self.drop_old = True            # Gemini may still be sending it: drop it until its turnComplete
        self.pending_stop_frame = n
        self._end_utterance(f"{self.controller} STOP")
        self._send_activity("activityStart", "barge_in")
        self._send_caller(list(self.ring))      # pre-roll: the start of the interruption, before SARJ decided

    def _close_user(self, why: str) -> None:
        self._flush_caller()
        self._send_activity("activityEnd", why)

    # ---- lifecycle
    def summary(self) -> dict:
        s = dict(self.stats)
        stops = s.pop("playback_stop_ms")
        s["playback_stop_after_stop_ms"] = {"n": len(stops), "max": max(stops) if stops else None,
                                            "median": sorted(stops)[len(stops) // 2] if stops else None}
        s["controller"] = self.controller
        s["duration_s"] = round(self.n * FRAME_MS / 1000, 1)
        s["sarj_controlled_every_barge_in"] = (s["gemini_interrupted_unrequested"] == 0
                                                and s["barge_ins"] == s["activity_start_after_stop"])
        return s

    def end(self, reason: str) -> None:
        with self.lock:
            if self.ended:
                return
            self.ended = True
            self.end_reason = reason
        if self.gemini:
            self.gemini.close()
        try:
            self.ws.send_json({"type": "ended", "reason": reason, "summary": self.summary()})
        except WSClosed:
            pass


def serve_live(ws: ServerWebSocket, models: dict, gate: LiveGate, settings: LiveSettings) -> None:
    """Run one conversation on an accepted browser WebSocket (called from the HTTP handler thread)."""
    try:
        first = ws.recv()
        start = json.loads(first) if isinstance(first, str) else {}
        if start.get("type") != "start":
            ws.send_json({"type": "error", "message": "expected a start message", "fatal": True})
            return
        if start.get("controller", "v3") != "v3" or "v3" not in models or not models["v3"].available:
            ws.send_json({"type": "error", "message": "Only an available SARJ v3 can start this conversation.", "fatal": True})
            return
        try:
            gate.acquire()
        except LiveRefused as e:
            ws.send_json({"type": "error", "message": str(e), "fatal": True})
            return
        try:
            session = LiveSession(ws, models, gate, "v3")
        except Exception as exc:
            gate.release()
            ws.send_json({"type": "error", "message": f"SARJ v3 could not start: {type(exc).__name__}", "fatal": True})
            return
        try:
            try:
                session.connect(settings)
            except LiveRefused as e:
                ws.send_json({"type": "error", "message": str(e), "fatal": True})
                return
            except Exception as e:  # noqa: BLE001 - network or handshake failure, shown to the user
                ws.send_json({"type": "error", "message": f"Could not connect to Gemini: {type(e).__name__}", "fatal": True})
                return
            deadline = time.monotonic() + settings.max_seconds
            while not session.ended:
                if time.monotonic() > deadline:
                    session.end(f"time limit ({settings.max_seconds} s)")
                    break
                msg = ws.recv()
                if isinstance(msg, bytes):
                    session.on_frame(msg)
                else:
                    m = json.loads(msg)
                    if m.get("type") == "end":
                        session.end("ended by the user")
        except (WSClosed, ValueError, OSError) as e:
            session.end(f"browser: {e}")
        finally:
            session.end(session.end_reason or "closed")
            gate.release()
    except (WSClosed, ValueError, OSError):
        pass
    finally:
        ws.close()
