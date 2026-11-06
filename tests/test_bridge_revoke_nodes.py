"""Fix 7 (B4#4): revoking remembered sessions must disconnect
authenticated node sockets.

A node that authenticated with a remembered-session token used to
stay connected (and able to receive commands) indefinitely after
revoke_phone_sessions(), because _handle_node_ws authenticated
once at hello and never revalidated. These tests pin the fix:
after revoke, the next frame from that node is refused and the
socket is closed with 4401, and the node is unregistered so a
further command is refused.
"""
import base64
import io
import json
import os
import socket
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


def _send_client(sock, payload: bytes, opcode=0x1):
    mask = b"\x05\x06\x07\x08"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    if len(payload) < 126:
        head = bytes([0x80 | opcode, 0x80 | len(payload)])
    elif len(payload) < 65536:
        head = bytes([0x80 | opcode, 0xFE]) + len(payload).to_bytes(2, "big")
    else:
        head = bytes([0x80 | opcode, 0xFF]) + len(payload).to_bytes(8, "big")
    sock.sendall(head + mask + masked)


def _read_server_frame(sock, timeout=5.0):
    """Read one unmasked server frame, or None on timeout/close."""
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


def _connect_session_node(monkeypatch, name):
    """Start a bridge server, issue a session token, connect a fake
    node with it, and return (server, client, token)."""
    bridge.BRIDGE_TOKEN = "tok-revoke-test"
    bridge.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria_revoke_ws_")
    bridge._SESSION_STORE = None
    bridge.lan_ip = lambda: "127.0.0.1"
    bridge.get_setting = lambda key, default=None: default
    token = bridge.phone_sessions().issue()
    assert bridge.phone_sessions().valid(token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.BridgeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
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
    hello = json.dumps({"type": "hello", "node": name, "kind": "android",
                        "key": token,
                        "capabilities": ["apps"]}).encode()
    _send_client(client, hello)
    opcode, data = _read_server_frame(client)
    assert opcode == 0x1, opcode
    assert json.loads(data) == {"type": "welcome", "ok": True}
    assert any(n["name"] == name for n in node_hub.HUB.nodes())
    return server, client, token


def test_revoke_disconnects_session_node(monkeypatch):
    name = "revoke-node-7"
    server, client, token = _connect_session_node(monkeypatch, name)
    try:
        # Revoke every remembered session, then speak again: the
        # next frame must be refused and the socket closed 4401 —
        # not answered with a pong as if nothing happened.
        bridge.revoke_phone_sessions()
        assert not bridge.phone_sessions().valid(token)
        _send_client(client, json.dumps({"type": "ping"}).encode())
        frame = _read_server_frame(client, timeout=5.0)
        assert frame is not None, "server sent nothing after revoke"
        opcode, payload = frame
        assert opcode == 0x8, f"expected close frame, got opcode {opcode}"
        assert len(payload) >= 2 and int.from_bytes(payload[:2], "big") == 4401, (
            f"expected close code 4401, got {payload!r}")
        # The node is unregistered, so a further command is refused.
        for _ in range(50):
            if not any(n["name"] == name for n in node_hub.HUB.nodes()):
                break
            threading.Event().wait(0.1)
        assert not any(n["name"] == name for n in node_hub.HUB.nodes())
        try:
            node_hub.HUB.send_command(name, "list_apps", {}, timeout=2)
            raise AssertionError("command to revoked node was accepted")
        except NodeHubError as e:
            assert e.code == "no_node"
    finally:
        client.close()
        server.shutdown()
        node_hub.HUB.unregister(name)


def test_bridge_token_node_survives_revoke(monkeypatch):
    """BRIDGE_TOKEN nodes are unaffected by design: revoking
    remembered sessions must NOT disconnect them."""
    bridge.BRIDGE_TOKEN = "tok-revoke-test"
    bridge.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria_revoke_ws_")
    bridge._SESSION_STORE = None
    bridge.lan_ip = lambda: "127.0.0.1"
    bridge.get_setting = lambda key, default=None: default
    name = "token-node-7"
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.BridgeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
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
        hello = json.dumps({"type": "hello", "node": name, "kind": "android",
                            "key": "tok-revoke-test",
                            "capabilities": []}).encode()
        _send_client(client, hello)
        opcode, data = _read_server_frame(client)
        assert json.loads(data) == {"type": "welcome", "ok": True}
        bridge.revoke_phone_sessions()
        _send_client(client, json.dumps({"type": "ping"}).encode())
        frame = _read_server_frame(client, timeout=5.0)
        assert frame is not None, "token node got no reply after revoke"
        opcode, payload = frame
        assert opcode == 0x1 and json.loads(payload) == {"type": "pong"}, (
            f"token node should still get pong, got opcode {opcode} {payload!r}")
        assert any(n["name"] == name for n in node_hub.HUB.nodes())
    finally:
        client.close()
        server.shutdown()
        node_hub.HUB.unregister(name)
