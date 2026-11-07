"""Pass 27 #14: an unauthenticated connection must not stall the
phone bridge's TLS accept loop.

Before: start_bridge_server wrapped the LISTENING socket in TLS,
so accept() performed the handshake synchronously in the accept
loop — one TCP client that sent no TLS handshake prevented a
second client from connecting until the first closed. Now the
listener stays plain TCP and each accepted connection is wrapped
with a deferred handshake under a per-operation timeout, so the
handshake happens in the connection's own thread; a semaphore
caps concurrent connections.

Pinned here against the real _BridgeHTTPServer on ephemeral
ports, with a throwaway self-signed cert from openssl:
- a silent TLS client does not delay a second client's handshake
  + HTTP response;
- a stalled handshake is closed by the server after the
  connection timeout (shortened for the test);
- at the connection cap, process_request refuses immediately
  instead of spawning another handler thread;
- (control) an idle plain-HTTP client likewise does not stall
  the next request.

Run per-file (own process), like every suite in this repo.
"""
import http.client
import importlib
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
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

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_bridge_tls_"), "test.db")
memory.init_databases()
memory._embed = lambda text: None
bridge = importlib.import_module("aria.bridge")

bridge.BRIDGE_TOKEN = "test-token-123"
bridge.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria_bridge_tls_ws_")
bridge.get_setting = lambda key, default=None: default


def _make_cert(tmp):
    """Throwaway self-signed cert for the test server."""
    cert = os.path.join(tmp, "cert.pem")
    key = os.path.join(tmp, "key.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", key, "-out", cert, "-days", "1", "-nodes",
         "-subj", "/CN=localhost"],
        check=True, capture_output=True, timeout=30)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    return ctx


def _client_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _start(srv):
    t = threading.Thread(target=srv.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    return t


def _stop(srv):
    try:
        srv.shutdown()
    except Exception:
        pass
    try:
        srv.server_close()
    except Exception:
        pass


def test_tls_accept_loop_not_stalled_by_silent_client(tmp_path):
    """The reproduction: client 1 connects and never handshakes;
    client 2 must still complete a TLS handshake and get an HTTP
    response within seconds."""
    ctx = _make_cert(str(tmp_path))
    srv = bridge._BridgeHTTPServer(
        ("127.0.0.1", 0), bridge.BridgeHandler, ssl_context=ctx)
    _start(srv)
    try:
        port = srv.server_address[1]
        silent = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            conn = http.client.HTTPSConnection(
                "127.0.0.1", port, timeout=5, context=_client_ctx())
            try:
                conn.request("GET", "/")
                resp = conn.getresponse()
                resp.read()
                status = resp.status
            finally:
                conn.close()
        finally:
            silent.close()
        # Any HTTP status proves the accept loop served client 2
        # while client 1 was still silent (the root route is
        # token-gated, so 401/200 are both fine — a stall would
        # have raised a timeout instead).
        assert isinstance(status, int) and 100 <= status < 600, status
    finally:
        _stop(srv)


def test_stalled_handshake_times_out(tmp_path):
    """A client that never handshakes is closed by the server
    after the connection timeout, not held forever."""
    ctx = _make_cert(str(tmp_path))
    srv = bridge._BridgeHTTPServer(
        ("127.0.0.1", 0), bridge.BridgeHandler, ssl_context=ctx)
    srv._CONN_TIMEOUT_S = 0.5
    _start(srv)
    try:
        port = srv.server_address[1]
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.settimeout(3)
        try:
            try:
                data = sock.recv(1)
            except (ConnectionResetError, socket.timeout) as exc:
                raise AssertionError(
                    f"server did not close the stalled handshake: {exc}")
            assert data == b"", f"expected EOF, got {data!r}"
        finally:
            sock.close()
    finally:
        _stop(srv)


def test_connection_cap_refuses_at_capacity():
    class _Tiny(bridge._BridgeHTTPServer):
        _MAX_CONNECTIONS = 1

    srv = _Tiny(("127.0.0.1", 0), bridge.BridgeHandler)
    try:
        assert srv._conn_sem.acquire(blocking=False)  # hold the permit
        closed = []

        class _FakeReq:
            def close(self):
                closed.append(True)

        before = threading.active_count()
        srv.process_request(_FakeReq(), ("127.0.0.1", 1234))
        assert closed == [True], "refused connection was not closed"
        assert threading.active_count() == before, \
            "refusal spawned a handler thread anyway"
        # The held permit was not released by the refusal.
        assert srv._conn_sem.acquire(blocking=False) is False
        srv._conn_sem.release()
    finally:
        srv.server_close()


def test_plain_http_idle_client_does_not_stall(tmp_path):
    srv = bridge._BridgeHTTPServer(
        ("127.0.0.1", 0), bridge.BridgeHandler)
    _start(srv)
    try:
        port = srv.server_address[1]
        idle = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            conn = http.client.HTTPConnection(
                "127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", "/")
                resp = conn.getresponse()
                resp.read()
                assert 100 <= resp.status < 600, resp.status
            finally:
                conn.close()
        finally:
            idle.close()
    finally:
        _stop(srv)
