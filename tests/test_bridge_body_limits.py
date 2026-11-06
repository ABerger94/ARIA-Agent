"""Fix 10 (B4#7): authenticated HTTP routes accept unbounded
or negative body lengths.

do_POST did `length = int(self.headers.get("Content-Length", 0))`
with no try/except and no bounds; /api/voice and /api/ask then
read `rfile.read(length)` unbounded, and a negative length
reached `rfile.read(-1)` — read-until-EOF, stalling the handler
thread until socket timeout/close. The fix factors a
_read_body(max_bytes) helper (bad length -> 400, length < 0 or
over the cap -> 413) and applies per-route caps: ask JSON 1 MiB,
voice audio 25 MiB (FILEPROC_TRANSCRIBE_MAX_BYTES).
"""
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
_speech.tts_bytes_for_bridge = lambda text: (b"", "audio/wav")
_speech.transcribe_audio = lambda audio, mime: "hello"
sys.modules["aria.speech"] = _speech

import importlib  # noqa: E402

bridge = importlib.import_module("aria.bridge")  # noqa: E402


def _start_server():
    bridge.BRIDGE_TOKEN = "tok-body-test"
    bridge.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria_body_ws_")
    bridge._SESSION_STORE = None
    bridge.lan_ip = lambda: "127.0.0.1"
    bridge.get_setting = lambda key, default=None: default
    bridge._BRIDGE_PROCESS_CALL = lambda text, flag: "echo:" + text
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.BridgeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _raw_post(port, path, headers, body=b"", timeout=5.0):
    """Send a raw POST (headers exactly as given — including a
    bogus Content-Length) and return (status, body-bytes), or
    None when no response arrives within `timeout` (a hang)."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        lines = [f"POST {path} HTTP/1.1", "Host: x",
                 "X-Bridge-Token: tok-body-test"]
        lines += [f"{k}: {v}" for k, v in headers.items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf += chunk
        head, rest = buf.split(b"\r\n\r\n", 1)
        status = int(head.split(b" ", 2)[1])
        length = 0
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        while len(rest) < length:
            chunk = sock.recv(65536)
            if not chunk:
                break
            rest += chunk
        return status, rest[:length]
    except (socket.timeout, TimeoutError, OSError):
        return None
    finally:
        sock.close()


def test_ask_negative_content_length_rejected():
    server = _start_server()
    try:
        got = _raw_post(server.server_address[1], "/api/ask",
                        {"Content-Length": "-1"})
        assert got is not None, "no response: handler hung on read(-1)"
        status, _ = got
        assert status in (400, 413), f"expected 400/413, got {status}"
    finally:
        server.shutdown()


def test_ask_oversized_body_rejected_without_hang():
    server = _start_server()
    try:
        # 2 MiB declared, no body bytes sent: the server must
        # refuse from the header alone, not wait for the body.
        got = _raw_post(server.server_address[1], "/api/ask",
                        {"Content-Length": "2097152",
                         "Content-Type": "application/json"})
        assert got is not None, "no response: handler waited for 2 MiB body"
        status, _ = got
        assert status == 413, f"expected 413, got {status}"
    finally:
        server.shutdown()


def test_ask_garbage_content_length_rejected():
    server = _start_server()
    try:
        got = _raw_post(server.server_address[1], "/api/ask",
                        {"Content-Length": "abc",
                         "Content-Type": "application/json"})
        assert got is not None, "no response to garbage Content-Length"
        status, _ = got
        assert status == 400, f"expected 400, got {status}"
    finally:
        server.shutdown()


def test_voice_oversized_body_rejected_without_hang():
    server = _start_server()
    try:
        # 26 MiB > the 25 MiB transcribe cap: refuse from header.
        got = _raw_post(server.server_address[1], "/api/voice",
                        {"Content-Length": str(26 * 1024 * 1024),
                         "Content-Type": "audio/webm"})
        assert got is not None, "no response: handler waited for 26 MiB body"
        status, _ = got
        assert status == 413, f"expected 413, got {status}"
    finally:
        server.shutdown()


def test_small_ask_and_voice_still_work():
    server = _start_server()
    try:
        port = server.server_address[1]
        body = json.dumps({"text": "hi", "want_audio": False}).encode()
        got = _raw_post(port, "/api/ask",
                        {"Content-Length": str(len(body)),
                         "Content-Type": "application/json"}, body)
        assert got is not None and got[0] == 200, got
        assert json.loads(got[1])["reply"] == ["echo:hi"]
        audio = b"\x00" * 1024
        got = _raw_post(port, "/api/voice",
                        {"Content-Length": str(len(audio)),
                         "Content-Type": "audio/webm"}, audio)
        assert got is not None and got[0] == 200, got
    finally:
        server.shutdown()
