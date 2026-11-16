"""The harness <-> system contract (see INTERFACE.md). Kept in one place so harness, examples and tests agree."""
from __future__ import annotations

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000          # 320
CHANNELS = ("agent", "user")
FRAME_BYTES = FRAME_SAMPLES * len(CHANNELS) * 2          # 1280: int16 LE, interleaved [agent, user]
DEFAULT_PORT = 8765

DECISIONS = ("STOP", "RESUME", "TAKE_TURN")
LABELS = ("interruption", "backchannel", "hesitation", "background", "nonverbal", "echo", "end_of_turn", "other")


def validate_client_message(msg: object) -> str | None:
    """Return an error string if a message sent BY a team's system is invalid, else None."""
    if not isinstance(msg, dict):
        return "message is not a JSON object"
    t = msg.get("type")
    if t == "hello":
        if not isinstance(msg.get("team"), str) or not msg["team"]:
            return "hello.team must be a non-empty string"
        if not isinstance(msg.get("system_version"), str) or not msg["system_version"]:
            return "hello.system_version must be a non-empty string (your git commit)"
        return None
    if t == "pong":
        return None if isinstance(msg.get("id"), int) else "pong.id must be an integer"
    if t == "decision":
        if msg.get("decision") not in DECISIONS:
            return f"decision must be one of {DECISIONS}"
        if "label" in msg and msg["label"] not in LABELS:
            return f"label must be one of {LABELS}"
        if "confidence" in msg and not (isinstance(msg["confidence"], (int, float)) and 0 <= msg["confidence"] <= 1):
            return "confidence must be a number in [0, 1]"
        return None
    return f"unknown message type {t!r}"
