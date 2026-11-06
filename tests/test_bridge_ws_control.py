"""Fix 9 (B4#6): WebSocket control frames corrupt fragmented
messages.

_ws_read_frame concatenated EVERY frame's payload until ANY
frame carried FIN and recorded only the first opcode. Per RFC
6455 §5.4/§5.5, control frames (ping 0x9, pong 0xA, close 0x8)
may be injected between fragments and must be handled, not
appended: an interleaved ping (FIN=1, opcode 0x9) was appended
to in-progress application JSON and its FIN prematurely
completed the message.

The fix: a control frame arriving mid-fragment is returned
immediately as its own frame (the in-progress fragment is
stashed and resumed on the next call) instead of being
appended; a new data frame mid-message is rejected, and a
continuation with no message in progress is rejected.
"""
import io
import os
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


_pkg("aria", PKG)
_pkg("aria.tools", os.path.join(PKG, "tools"))
_pkg("aria.agent", os.path.join(PKG, "agent"))
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
_vision = types.ModuleType("aria.vision")
_vision.get_face_frame_jpeg = lambda: None
_vision.publish_phone_frame = lambda jpeg: True
_vision.get_phone_frame_jpeg = lambda: None
_vision.get_phone_frame_status = lambda: {"active": False, "age_s": -1.0}
_vision.describe_phone_view = lambda q="": "[stubbed]"
sys.modules["aria.vision"] = _vision
_speech = types.ModuleType("aria.speech")
_speech.tts_bytes_for_bridge = lambda text: (b"", "no speech in tests")
_speech.transcribe_audio = lambda audio, mime: ""
sys.modules["aria.speech"] = _speech

import importlib  # noqa: E402

bridge = importlib.import_module("aria.bridge")  # noqa: E402


def _frame(payload, opcode=0x1, fin=True):
    head = bytes([(0x80 if fin else 0x00) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    return head + payload


def test_interleaved_ping_surfaces_separately():
    # Fragmented text frame, ping injected between fragments,
    # final continuation: the ping must surface as its own frame
    # and the reassembled payload must be clean JSON.
    stream = io.BytesIO(
        _frame(b'{"type":', opcode=0x1, fin=False)
        + _frame(b"ping", opcode=0x9, fin=True)
        + _frame(b'"pong"}', opcode=0x0, fin=True))
    opcode, payload = bridge._ws_read_frame(stream)
    assert (opcode, payload) == (0x9, b"ping"), (
        f"ping should surface separately, got {(opcode, payload)!r}")
    opcode, payload = bridge._ws_read_frame(stream)
    assert opcode == 0x1, opcode
    assert payload == b'{"type":"pong"}', payload
    import json
    assert json.loads(payload) == {"type": "pong"}


def test_interleaved_pong_and_close_surface_separately():
    stream = io.BytesIO(
        _frame(b"ab", opcode=0x1, fin=False)
        + _frame(b"", opcode=0xA, fin=True)
        + _frame(b"cd", opcode=0x0, fin=True))
    assert bridge._ws_read_frame(stream) == (0xA, b"")
    assert bridge._ws_read_frame(stream) == (0x1, b"abcd")


def test_new_data_frame_mid_message_rejected():
    stream = io.BytesIO(
        _frame(b"ab", opcode=0x1, fin=False)
        + _frame(b"cd", opcode=0x1, fin=True))
    try:
        bridge._ws_read_frame(stream)
    except ConnectionError as e:
        assert "data frame" in str(e) or "fragment" in str(e)
    else:
        raise AssertionError("new data frame mid-message was accepted")


def test_continuation_without_fragment_rejected():
    stream = io.BytesIO(_frame(b"cd", opcode=0x0, fin=True))
    try:
        bridge._ws_read_frame(stream)
    except ConnectionError as e:
        assert "continuation" in str(e)
    else:
        raise AssertionError("stray continuation frame was accepted")


def test_fragmented_control_frame_rejected():
    # Control frames must never be fragmented (RFC 6455 §5.5).
    stream = io.BytesIO(_frame(b"pi", opcode=0x9, fin=False))
    try:
        bridge._ws_read_frame(stream)
    except ConnectionError as e:
        assert "control" in str(e)
    else:
        raise AssertionError("fragmented ping was accepted")


def test_plain_fragmented_message_still_reassembles():
    stream = io.BytesIO(
        _frame(b"ab", opcode=0x1, fin=False)
        + _frame(b"cd", opcode=0x0, fin=True))
    assert bridge._ws_read_frame(stream) == (0x1, b"abcd")


def test_standalone_ping_still_reads():
    assert bridge._ws_read_frame(
        io.BytesIO(_frame(b"hi", opcode=0x9))) == (0x9, b"hi")
