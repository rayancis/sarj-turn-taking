from ttbench.protocol import FRAME_BYTES, validate_client_message as v


def test_frame_size():
    assert FRAME_BYTES == 1280


def test_messages():
    assert v({"type": "hello", "team": "T1", "system_version": "abc"}) is None
    assert v({"type": "hello", "team": ""}) is not None
    assert v({"type": "decision", "decision": "STOP"}) is None
    assert v({"type": "decision", "decision": "RESUME"}) is None
    assert v({"type": "decision", "decision": "TAKE_TURN", "confidence": 0.3, "label": "end_of_turn"}) is None
    assert v({"type": "decision", "decision": "PAUSE"}) is not None
    assert v({"type": "decision", "decision": "STOP", "confidence": 2}) is not None
    assert v({"type": "pong", "id": 3}) is None
    assert v([1, 2]) is not None
