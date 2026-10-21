"""Phone bridge tests (2026-10-21 fix package, concern 6).

Root cause of "the phone bridge doesn't load": start_bridge_server
bound get_setting("phone_bridge_host", "127.0.0.1") — localhost —
while its banner printed the LAN URL as if live, so the phone
connected to an address nothing listened on. Second cause: the
per-machine cert's SANs never included the machine's LAN IP and
ensure_bridge_cert reused a stale cert forever, so the phone's
browser rejected the cert at the advertised address. Third: the
advertised URL carried no token, though every route is
token-gated. These tests pin:

  * bind resolution — no setting -> 0.0.0.0 (the bridge exists for
    the phone; auth is the token's job, unchanged), an explicit
    setting wins in both directions;
  * the advertised URL matches the bind class (LAN when wide,
    localhost when loopback) and carries the ?token= the root
    route exchanges for the auth cookie;
  * the cert-coverage predicate against real PEMs (the bundled
    cert is the pre-fix shape: no LAN IP -> does not cover);
  * ensure_bridge_cert refreshing a stale cert via temp files and
    keeping the old one when a refresh fails;
  * the real server on an ephemeral port: unauthed API -> 401,
    root without token -> login page, ?token= -> 302 + cookie,
    authed API -> 200.

Suite conventions: heavy modules stubbed in sys.modules at import;
bridge's imported config values (BRIDGE_TOKEN, WORKSPACE_DIR,
lan_ip, get_setting) are patched on the bridge module itself.
"""
import http.client
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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
    tempfile.mkdtemp(prefix="aria_bridge_"), "test.db")
memory.init_databases()
memory._embed = lambda text: None
bridge = importlib.import_module("aria.bridge")

_TOKEN = "test-token-123"
_LAN = "192.168.99.10"
_SETTINGS = {}


def _setup_bridge():
    bridge.BRIDGE_TOKEN = _TOKEN
    bridge.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria_bridge_ws_")
    bridge.lan_ip = lambda: _LAN
    bridge.get_setting = lambda key, default=None: _SETTINGS.get(
        key, default)
    bridge.BRIDGE_SCHEME = "https"
    bridge.BRIDGE_CERT = None
    bridge.BRIDGE_KEY = None
    _SETTINGS.clear()


def test_a_bind_host_resolution():
    _setup_bridge()
    assert bridge._bridge_bind_host() == "0.0.0.0"  # no setting: wide
    _SETTINGS["phone_bridge_host"] = "127.0.0.1"
    assert bridge._bridge_bind_host() == "127.0.0.1"  # override wins
    _SETTINGS["phone_bridge_host"] = "0.0.0.0"
    assert bridge._bridge_bind_host() == "0.0.0.0"
    _SETTINGS["phone_bridge_host"] = ""
    assert bridge._bridge_bind_host() == "0.0.0.0"  # empty = default
    print("ok bind_host_resolution")


def test_b_advertised_url_matches_bind_class():
    _setup_bridge()
    wide = bridge._advertised_url("0.0.0.0", 8777)
    assert wide == f"https://{_LAN}:8777/?token={_TOKEN}", wide
    loop = bridge._advertised_url("127.0.0.1", 8777)
    assert loop == f"https://127.0.0.1:8777/?token={_TOKEN}", loop
    # get_bridge_url follows the resolved bind: default -> LAN form,
    # a localhost override -> localhost form (never a LAN URL for a
    # bridge that isn't listening there).
    assert bridge.get_bridge_url() == \
        f"https://{_LAN}:{bridge.PHONE_BRIDGE_PORT}/?token={_TOKEN}"
    _SETTINGS["phone_bridge_host"] = "127.0.0.1"
    assert bridge.get_bridge_url().startswith("https://127.0.0.1:")
    # No token configured -> bare URL, no dangling query.
    bridge.BRIDGE_TOKEN = ""
    assert "?" not in bridge._advertised_url("0.0.0.0", 8777)
    print("ok advertised_url_matches_bind_class")


def test_c_cert_coverage_predicate():
    _setup_bridge()
    tmp = tempfile.mkdtemp(prefix="aria_bridge_cert_")
    bundled = os.path.join(tmp, "bundled.pem")
    with open(bundled, "w", encoding="utf-8") as f:
        f.write(bridge._BRIDGE_CERT_PEM)
    dns, ips = bridge._cert_san_names(bundled)
    assert {"aria-bridge", "localhost"} <= dns, dns
    assert ips == {"127.0.0.1"}, ips
    # The pre-fix failure shape: a LAN IP is NOT covered.
    assert bridge._cert_covers_machine(bundled, ip=_LAN) is False
    assert bridge._cert_covers_machine(bundled, ip="127.0.0.1") is True
    # Unparseable -> no names, no coverage (regenerate).
    junk = os.path.join(tmp, "junk.pem")
    with open(junk, "w", encoding="utf-8") as f:
        f.write("not a certificate\n")
    assert bridge._cert_san_names(junk) is None
    assert bridge._cert_covers_machine(junk, ip=_LAN) is False
    print("ok cert_coverage_predicate")


def test_c2_generated_cert_shape_covers_lan():
    # A cert WITH the LAN IP in its SANs covers this machine; one
    # minted for a DIFFERENT LAN IP does not (the IP-change case).
    if not shutil.which("openssl"):
        print("skip generated_cert_shape (no openssl)")
        return
    _setup_bridge()
    tmp = tempfile.mkdtemp(prefix="aria_bridge_cert2_")
    cert = os.path.join(tmp, "c.pem")
    key = os.path.join(tmp, "k.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key, "-out", cert, "-days", "30",
         "-subj", "/CN=aria-bridge",
         "-addext", "subjectAltName=DNS:aria-bridge,DNS:localhost,"
                    f"IP:127.0.0.1,IP:{_LAN}"],
        check=True, capture_output=True, timeout=60)
    assert bridge._cert_covers_machine(cert, ip=_LAN) is True
    assert bridge._cert_covers_machine(cert, ip="10.0.0.55") is False
    print("ok generated_cert_shape_covers_lan")


def _seed_machine_cert(ws):
    mcert = os.path.join(ws, "aria_bridge_machine.pem")
    mkey = os.path.join(ws, "aria_bridge_machine_key.pem")
    with open(mcert, "w", encoding="utf-8") as f:
        f.write(bridge._BRIDGE_CERT_PEM)  # stale shape: no LAN IP
    with open(mkey, "w", encoding="utf-8") as f:
        f.write(bridge._BRIDGE_KEY_PEM)
    return mcert, mkey


def test_d_ensure_refreshes_stale_cert():
    _setup_bridge()
    mcert, _mkey = _seed_machine_cert(bridge.WORKSPACE_DIR)
    calls = []

    def fake_gen(cert_p, key_p):
        calls.append((cert_p, key_p))
        with open(cert_p, "w", encoding="utf-8") as f:
            f.write("FRESH-CERT")
        with open(key_p, "w", encoding="utf-8") as f:
            f.write("FRESH-KEY")
        return True

    real_gen = bridge._generate_machine_cert
    bridge._generate_machine_cert = fake_gen
    try:
        bridge.ensure_bridge_cert()
    finally:
        bridge._generate_machine_cert = real_gen
    assert calls, "stale cert was not regenerated"
    with open(mcert, encoding="utf-8") as f:
        assert f.read() == "FRESH-CERT"
    assert bridge.BRIDGE_SCHEME == "https"
    assert bridge.BRIDGE_CERT == mcert
    print("ok ensure_refreshes_stale_cert")


def test_d2_ensure_keeps_covering_cert_and_failed_refresh():
    _setup_bridge()
    mcert, _mkey = _seed_machine_cert(bridge.WORKSPACE_DIR)
    calls = []

    def fake_gen(cert_p, key_p):
        calls.append(cert_p)
        return False  # generation unavailable/failing

    real_gen = bridge._generate_machine_cert
    real_cov = bridge._cert_covers_machine
    bridge._generate_machine_cert = fake_gen
    try:
        # Covering cert: no regeneration attempted at all.
        bridge._cert_covers_machine = lambda p, ip=None: True
        bridge.ensure_bridge_cert()
        assert calls == [], calls
        assert bridge.BRIDGE_CERT == mcert
        # Stale cert + failed refresh: old cert kept, still selected.
        bridge._cert_covers_machine = real_cov
        bridge.ensure_bridge_cert()
        assert calls, "stale cert should have attempted a refresh"
        with open(mcert, encoding="utf-8") as f:
            assert "BEGIN CERTIFICATE" in f.read()  # old cert intact
        assert bridge.BRIDGE_CERT == mcert
        assert not os.path.exists(mcert + ".new")
    finally:
        bridge._generate_machine_cert = real_gen
        bridge._cert_covers_machine = real_cov
    print("ok ensure_keeps_covering_cert_and_failed_refresh")


def test_e_server_auth_flow():
    _setup_bridge()
    _SETTINGS["phone_bridge_host"] = "127.0.0.1"

    def fake_ensure():
        bridge.BRIDGE_SCHEME = "http"
        bridge.BRIDGE_CERT = None
        bridge.BRIDGE_KEY = None

    real_ensure = bridge.ensure_bridge_cert
    bridge.ensure_bridge_cert = fake_ensure
    bridge._BRIDGE_SERVER = None
    t = threading.Thread(target=bridge.start_bridge_server,
                         kwargs={"port": 0}, daemon=True)
    t.start()
    try:
        deadline = time.time() + 10
        while bridge._BRIDGE_SERVER is None and time.time() < deadline:
            time.sleep(0.05)
        srv = bridge._BRIDGE_SERVER
        assert srv is not None, "bridge server did not start"
        port = srv.server_address[1]

        def req(path, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port,
                                              timeout=5)
            try:
                conn.request("GET", path, headers=headers or {})
                resp = conn.getresponse()
                body = resp.read()
                return resp.status, dict(resp.getheaders()), body
            finally:
                conn.close()

        # Unauthed API call is refused.
        status, _h, _b = req("/api/log")
        assert status == 401, status
        # Wrong token is refused too.
        status, _h, _b = req("/api/log", {"X-Bridge-Token": "nope"})
        assert status == 401, status
        # Root without a token serves the login page, not the app.
        status, _h, body = req("/")
        assert status == 200 and b"Bridge - Login" in body, status
        # The advertised ?token= form exchanges for the auth cookie.
        status, headers, _b = req(f"/?token={_TOKEN}")
        assert status == 302, status
        assert "aria_bridge_token=" in headers.get("Set-Cookie", "")
        # Authed API call works (header auth).
        status, _h, body = req("/api/log", {"X-Bridge-Token": _TOKEN})
        assert status == 200 and body == b"[]", (status, body)
        # Cookie auth reaches the dashboard.
        status, _h, body = req(
            "/", {"Cookie": f"aria_bridge_token={_TOKEN}"})
        assert status == 200 and b"<title>A.R.I.A. Bridge</title>" in body
    finally:
        bridge.ensure_bridge_cert = real_ensure
        if bridge._BRIDGE_SERVER is not None:
            bridge._BRIDGE_SERVER.shutdown()
            bridge._BRIDGE_SERVER.server_close()
            bridge._BRIDGE_SERVER = None
    print("ok server_auth_flow")
