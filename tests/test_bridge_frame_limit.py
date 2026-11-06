"""Fix 8 (B4#5): companion's valid response sizes exceed the
bridge's frame limit.

bridge._ws_read_frame capped every node message at 1 MiB, but
desktop_node permits 2 MiB read_file results and uncapped
screenshots, and its own client sets max_msg_size=4 MiB. A
permitted ~1.5 MiB result was rejected and the node dropped.
The fix raises the post-hello node-message cap to 4 MiB while
keeping the unauthenticated hello/handshake at 1 MiB.
"""
import base64
import io
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import types
from http.server import ThreadingHTTPServer

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
from aria import node_hub  # noqa: E402
from aria.node_hub import NodeHubError  # noqa: E402


def _masked_frame(payload: bytes, opcode=0x1):
    mask = b"\x05\x06\x07\x08"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, 0x80 | n])
    elif n < 65536:
        head = bytes([0x80 | opcode, 0xFE]) + struct.pack("!H", n)
    else:
        head = bytes([0x80 | opcode, 0xFF]) + struct.pack("!Q", n)
    return head + mask + masked


def _read_server_frame(sock, timeout=10.0):
    sock.settimeout(timeout)
    buf = b""
    try:
        while len(buf) < 2:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf += chunk
        n = buf[1] & 0x7F
        off = 2
        if n == 126:
            while len(buf) < 4:
                buf += sock.recv(4096)
            n = int.from_bytes(buf[2:4], "big")
            off = 4
        elif n == 127:
            while len(buf) < 10:
                buf += sock.recv(4096)
            n = int.from_bytes(buf[2:10], "big")
            off = 10
        while len(buf) < off + n:
            chunk = sock.recv(65536)
            if not chunk:
                return None
            buf += chunk
        return buf[0] & 0x0F, buf[off:off + n]
    except (socket.timeout, TimeoutError, OSError):
        return None


def _connect_token_node(name):
    bridge.BRIDGE_TOKEN = "tok-frame-test"
    bridge.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria_frame_ws_")
    bridge._SESSION_STORE = None
    bridge.lan_ip = lambda: "127.0.0.1"
    bridge.get_setting = lambda key, default=None: default
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.BridgeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=10)
    key = base64.b64encode(b"0123456789abcdef").decode()
    client.sendall((
        "GET /node/ws HTTP/1.1\r\nHost: x\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += client.recv(4096)
    assert b"101" in resp.split(b"\r\n", 1)[0]
    hello = json.dumps({"type": "hello", "node": name, "kind": "desktop",
                        "key": "tok-frame-test",
                        "capabilities": ["read_file"]}).encode()
    client.sendall(_masked_frame(hello))
    opcode, data = _read_server_frame(client)
    assert json.loads(data) == {"type": "welcome", "ok": True}
    return server, client


def _answer_with_result(client, size, timeout=15):
    """Read the server's command frame, answer it with a result
    whose data carries a `size`-byte string, and return the frame."""
    opcode, data = _read_server_frame(client, timeout=timeout)
    assert opcode == 0x1, opcode
    cmd = json.loads(data)
    assert cmd["type"] == "command"
    result = json.dumps({"type": "result", "id": cmd["id"], "ok": True,
                         "data": {"content": "x" * size}}).encode()
    try:
        client.sendall(_masked_frame(result))
    except OSError:
        # The server rejects an over-cap frame from its header
        # alone and closes the socket mid-send; the send racing
        # that close is the rejection itself, not a test failure.
        pass
    return cmd["id"]


def test_result_between_1_and_4_mib_accepted():
    # A permitted ~1.5 MiB read_file result must reach the caller,
    # not be rejected by the old 1 MiB frame cap.
    name = "frame-node-8"
    server, client = _connect_token_node(name)
    outcome = {}
    try:
        def call():
            try:
                outcome["result"] = node_hub.HUB.send_command(
                    name, "read_file", {"path": "big.txt"}, timeout=15)
            except Exception as e:  # noqa: BLE001 - captured for assert
                outcome["error"] = e

        t = threading.Thread(target=call, daemon=True)
        t.start()
        _answer_with_result(client, 1_500_000)
        t.join(15)
        assert "error" not in outcome, outcome.get("error")
        assert len(outcome["result"]["content"]) == 1_500_000
        assert any(n["name"] == name for n in node_hub.HUB.nodes())
    finally:
        client.close()
        server.shutdown()
        node_hub.HUB.unregister(name)


def test_result_over_4_mib_still_rejected():
    # The cap is raised, not removed: a >4 MiB result frame must
    # still be refused and the node dropped.
    name = "frame-node-8b"
    server, client = _connect_token_node(name)
    outcome = {}
    try:
        def call():
            try:
                outcome["result"] = node_hub.HUB.send_command(
                    name, "read_file", {"path": "huge.txt"}, timeout=15)
            except Exception as e:  # noqa: BLE001 - captured for assert
                outcome["error"] = e

        t = threading.Thread(target=call, daemon=True)
        t.start()
        _answer_with_result(client, 4_500_000)
        t.join(15)
        assert "error" in outcome, "over-cap result was accepted"
        assert isinstance(outcome["error"], NodeHubError)
        for _ in range(50):
            if not any(n["name"] == name for n in node_hub.HUB.nodes()):
                break
            threading.Event().wait(0.1)
        assert not any(n["name"] == name for n in node_hub.HUB.nodes())
    finally:
        client.close()
        server.shutdown()
        node_hub.HUB.unregister(name)


def test_hello_still_capped_at_1_mib():
    # The unauthenticated hello/handshake keeps the 1 MiB cap: a
    # 1.5 MiB frame read at the default cap must be refused.
    payload = b"x" * 1_500_000
    frame = bytes([0x81, 0xFF]) + struct.pack("!Q", len(payload)) + payload
    try:
        bridge._ws_read_frame(io.BytesIO(frame))
    except ConnectionError as e:
        assert "too large" in str(e)
    else:
        raise AssertionError("oversized hello frame was accepted")
