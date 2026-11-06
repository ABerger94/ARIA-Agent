"""Fix 15 (B5#11): node results are not bound to their
originating connection.

send_command stored the intended node in the pending entry,
but handle_result popped by id alone and never compared the
delivering connection against it, and the bridge called
hub.handle_result(msg) with no connection identity. A connected
node could answer another node's command id.

The fix threads the delivering node's name through —
handle_result(msg, node_name=None) — and drops (returns False,
pending entry kept) on mismatch. Binding is by node NAME, not
socket identity, so a restarted phone re-registering under the
same name can still answer. node_name=None keeps the old
behaviour for callers with no connection identity.
"""
import base64
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
from aria.node_hub import NodeHub  # noqa: E402


def _hub_with_two_nodes():
    hub = NodeHub()
    frames = {}
    hub.register("node-a", kind="android", capabilities=[],
                 send=lambda f: frames.setdefault("a", f))
    hub.register("node-b", kind="android", capabilities=[],
                 send=lambda f: frames.setdefault("b", f))
    return hub, frames


def test_result_from_wrong_node_rejected_and_pending_kept():
    hub, frames = _hub_with_two_nodes()
    outcome = {}

    def call():
        try:
            outcome["result"] = hub.send_command(
                "node-a", "list_apps", {}, timeout=5)
        except Exception as e:  # noqa: BLE001 - captured for assert
            outcome["error"] = e

    t = threading.Thread(target=call, daemon=True)
    t.start()
    for _ in range(50):
        if "a" in frames:
            break
        threading.Event().wait(0.1)
    cid = frames["a"]["id"]
    result = {"type": "result", "id": cid, "ok": True,
              "data": {"apps": ["from-b"]}}
    # B delivers A's command id: rejected, and A's command is
    # still waiting — the pending entry must survive the attempt.
    assert hub.handle_result(dict(result), node_name="node-b") is False
    threading.Event().wait(0.5)
    assert "result" not in outcome and "error" not in outcome
    assert cid in hub._pending
    # A itself delivers it: accepted, and the future completes.
    assert hub.handle_result(dict(result), node_name="node-a") is True
    t.join(5)
    assert outcome.get("result") == {"apps": ["from-b"]}
    assert cid not in hub._pending


def test_result_without_node_name_still_routes():
    # Backward compatibility: callers with no connection identity
    # (existing tests/tools) keep the old by-id routing.
    hub, frames = _hub_with_two_nodes()
    outcome = {}

    def call():
        outcome["result"] = hub.send_command("node-a", "list_apps", {},
                                             timeout=5)

    t = threading.Thread(target=call, daemon=True)
    t.start()
    for _ in range(50):
        if "a" in frames:
            break
        threading.Event().wait(0.1)
    cid = frames["a"]["id"]
    assert hub.handle_result({"id": cid, "ok": True,
                              "data": {"apps": []}}) is True
    t.join(5)
    assert outcome.get("result") == {"apps": []}


def test_unknown_id_still_rejected():
    hub, _ = _hub_with_two_nodes()
    assert hub.handle_result({"id": "nope", "ok": True, "data": {}},
                             node_name="node-a") is False


# --- bridge wiring: the delivering socket's registered name is
# what reaches handle_result -------------------------------

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


def _connect_node(port, name):
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
    hello = json.dumps({"type": "hello", "node": name, "kind": "android",
                        "key": "tok-bind-test",
                        "capabilities": ["apps"]}).encode()
    client.sendall(_masked_frame(hello))
    opcode, data = _read_server_frame(client)
    assert json.loads(data) == {"type": "welcome", "ok": True}
    return client


def test_bridge_rejects_cross_node_result():
    """Two live nodes: a command sent to A, answered on B's
    socket with A's id, must NOT complete A's future; A's own
    answer must."""
    bridge.BRIDGE_TOKEN = "tok-bind-test"
    bridge.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria_bind_ws_")
    bridge._SESSION_STORE = None
    bridge.lan_ip = lambda: "127.0.0.1"
    bridge.get_setting = lambda key, default=None: default
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.BridgeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    sock_a = _connect_node(port, "bind-node-a")
    sock_b = _connect_node(port, "bind-node-b")
    outcome = {}
    try:
        def call():
            try:
                outcome["result"] = node_hub.HUB.send_command(
                    "bind-node-a", "list_apps", {}, timeout=10)
            except Exception as e:  # noqa: BLE001 - captured for assert
                outcome["error"] = e

        t = threading.Thread(target=call, daemon=True)
        t.start()
        opcode, data = _read_server_frame(sock_a)
        assert opcode == 0x1
        cmd = json.loads(data)
        assert cmd["type"] == "command"
        wrong = json.dumps({"type": "result", "id": cmd["id"],
                            "ok": True,
                            "data": {"apps": ["from-b"]}}).encode()
        sock_b.sendall(_masked_frame(wrong))
        threading.Event().wait(0.7)
        assert "result" not in outcome and "error" not in outcome, (
            f"cross-node result completed A's command: {outcome}")
        right = json.dumps({"type": "result", "id": cmd["id"],
                            "ok": True,
                            "data": {"apps": ["from-a"]}}).encode()
        sock_a.sendall(_masked_frame(right))
        t.join(10)
        assert outcome.get("result") == {"apps": ["from-a"]}, outcome
    finally:
        sock_a.close()
        sock_b.close()
        server.shutdown()
        node_hub.HUB.unregister("bind-node-a")
        node_hub.HUB.unregister("bind-node-b")
