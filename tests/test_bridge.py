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
        assert status == 303, status
        assert "aria_phone_session=" in headers.get("Set-Cookie", "")
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


def test_pairing_single_use_expiry_and_rotation(monkeypatch):
    _setup_bridge()
    monkeypatch.setattr(bridge, '_BRIDGE_SERVER', types.SimpleNamespace(server_port=8777))
    monkeypatch.setattr(bridge, '_BRIDGE_ACTUAL_SCHEME', 'https')
    link = bridge.create_pairing_url()
    assert '?pair=' in link and _TOKEN not in link
    token = link.split('?pair=')[1]
    assert not bridge._consume_pairing('wrong')
    assert bridge._consume_pairing(token)
    assert not bridge._consume_pairing(token)
    old = bridge.create_pairing_url().split('?pair=')[1]
    new = bridge.create_pairing_url().split('?pair=')[1]
    assert not bridge._consume_pairing(old)
    bridge._PAIR_EXPIRES = time.monotonic() - 1
    assert not bridge._consume_pairing(new)


def test_pairing_requires_running_server(monkeypatch):
    import pytest
    monkeypatch.setattr(bridge, '_BRIDGE_SERVER', None)
    with pytest.raises(RuntimeError):
        bridge.create_pairing_url()


def test_pairing_http_cookie_exchange(monkeypatch):
    from http.server import ThreadingHTTPServer
    _setup_bridge()
    server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.BridgeHandler)
    monkeypatch.setattr(bridge, '_BRIDGE_SERVER', server)
    monkeypatch.setattr(bridge, '_BRIDGE_ACTUAL_SCHEME', 'http')
    path = '/?pair=' + bridge.create_pairing_url().split('?pair=')[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
        conn.request('GET', path); response = conn.getresponse()
        assert response.status == 303
        cookie = response.getheader('Set-Cookie').split(';', 1)[0]
        assert response.getheader('Cache-Control') == 'no-store'
        response.read()
        conn.request('GET', '/api/log', headers={'Cookie': cookie})
        response = conn.getresponse(); assert response.status == 200; response.read()
        conn.request('GET', path); response = conn.getresponse()
        assert response.status == 401; response.read()
    finally:
        conn.close(); server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_scan_opens_app_and_remembers_phone(monkeypatch):
    import urllib.request
    import http.cookiejar
    from http.server import ThreadingHTTPServer
    _setup_bridge()
    server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.BridgeHandler)
    monkeypatch.setattr(bridge, '_BRIDGE_SERVER', server)
    monkeypatch.setattr(bridge, '_BRIDGE_ACTUAL_SCHEME', 'http')
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    path = '/auto-login?pair=' + bridge.create_pairing_url().split('?pair=')[1]
    jar = http.cookiejar.CookieJar()
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        with browser.open(base + path, timeout=3) as response:
            assert response.url == base + '/'
            assert b'<title>A.R.I.A. Bridge</title>' in response.read()
        cookie = next(c for c in jar if c.name == 'aria_phone_session')
        assert cookie.expires is not None and cookie.value != _TOKEN
        # Restarting the session store and reopening the base URL requires no key.
        monkeypatch.setattr(bridge, '_SESSION_STORE', None)
        with browser.open(base + '/', timeout=3) as response:
            assert b'<title>A.R.I.A. Bridge</title>' in response.read()
        # A rescan from this phone reaches the app even though the key is spent.
        with browser.open(base + path, timeout=3) as response:
            assert b'<title>A.R.I.A. Bridge</title>' in response.read()
        with browser.open(urllib.request.Request(base + '/api/logout', data=b''), timeout=3) as response:
            assert b'Bridge - Login' in response.read()
        assert not bridge.phone_sessions().valid(cookie.value)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_legacy_signout_does_not_disconnect_other_phones(monkeypatch):
    from http.server import ThreadingHTTPServer
    _setup_bridge()
    token = bridge.phone_sessions().issue()
    server = ThreadingHTTPServer(('127.0.0.1', 0), bridge.BridgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
        conn.request('POST', '/api/logout', headers={'X-Bridge-Token': _TOKEN})
        response = conn.getresponse(); assert response.status == 303; response.read()
        assert bridge.phone_sessions().valid(token)
    finally:
        conn.close(); server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_bridge_pages_share_the_homepage_theme():
    # Alek, 2026-10-27: the upload + commands pages (and login)
    # still wore the old pink theme. Every bridge page carries
    # the homepage's accent and none carries the pink.
    from aria import bridge
    for page in (bridge.UPLOAD_HTML, bridge.BRIDGE_LOGIN_HTML,
                 bridge._commands_html()):
        assert '#64d9e7' in page
        assert '#ff5fa2' not in page
    assert 'Send a file' in bridge.UPLOAD_HTML
    assert 'Command reference' in bridge._commands_html()


def test_lock_endpoint_flow():
    """POST /api/lock (Alek, 2026-10-27): the phone can lock the
    laptop app. Pins: unauthed -> 401 and no lock; authed with a
    lock password set -> 200 and applock.lock() actually called;
    authed with NO password set -> 409 and no lock (locking with
    no password would strand the laptop). applock is faked in
    sys.modules — the handler imports it per request."""
    import json as _json
    _setup_bridge()
    _SETTINGS["phone_bridge_host"] = "127.0.0.1"

    fake = types.ModuleType("aria.applock")
    state = {"password": True, "locked": False, "lock_calls": 0}
    fake.password_is_set = lambda: state["password"]
    fake.is_locked = lambda: state["locked"]

    def _lock():
        state["lock_calls"] += 1
        state["locked"] = True
    fake.lock = _lock
    old_applock = sys.modules.get("aria.applock")
    sys.modules["aria.applock"] = fake

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

        def req(method, path, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port,
                                              timeout=5)
            try:
                conn.request(method, path, headers=headers or {})
                resp = conn.getresponse()
                return resp.status, resp.read()
            finally:
                conn.close()

        # Unauthed: refused, nothing locked.
        status, _b = req("POST", "/api/lock")
        assert status == 401, status
        assert state["lock_calls"] == 0 and state["locked"] is False
        # Authed, password set: locks.
        status, body = req("POST", "/api/lock",
                           {"X-Bridge-Token": _TOKEN})
        assert status == 200, (status, body)
        assert _json.loads(body)["locked"] is True
        assert state["lock_calls"] == 1 and state["locked"] is True
        # (No /api/status assertion here: that handler imports the
        # real hud module, which this harness's vision stub cannot
        # satisfy — a pre-existing harness limit, unchanged by the
        # lock work. The 'locked' status field is a guarded
        # three-line addition on that handler.)
        # No password set: 409, no lock — never strand the laptop.
        state["password"] = False
        state["locked"] = False
        status, _b = req("POST", "/api/lock",
                         {"X-Bridge-Token": _TOKEN})
        assert status == 409, status
        assert state["lock_calls"] == 1 and state["locked"] is False
    finally:
        bridge.ensure_bridge_cert = real_ensure
        if bridge._BRIDGE_SERVER is not None:
            bridge._BRIDGE_SERVER.shutdown()
            bridge._BRIDGE_SERVER.server_close()
            bridge._BRIDGE_SERVER = None
        if old_applock is None:
            sys.modules.pop("aria.applock", None)
        else:
            sys.modules["aria.applock"] = old_applock
    print("ok lock_endpoint_flow")


def test_pwa_icons_and_manifest_served():
    """The Home-screen icon is the desktop waveform-orb art (Alek,
    2026-10-28): the PNG icons and the web manifest are served
    unauthenticated (iOS fetches the touch icon outside the pairing
    flow), and the page links them."""
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

        for name in ("icon-180.png", "icon-192.png", "icon-512.png"):
            status, headers, body = req("/" + name)  # no auth on purpose
            assert status == 200, (name, status)
            assert headers.get("Content-Type") == "image/png", headers
            assert body[:8] == b"\x89PNG\r\n\x1a\n", name
            assert len(body) > 10000, (name, len(body))
        # iOS's well-known root probe gets the same 180 icon.
        status, headers, body = req("/apple-touch-icon.png")
        assert status == 200, status
        assert body[:8] == b"\x89PNG\r\n\x1a\n"
        _s180, _h180, body180 = req("/icon-180.png")
        assert body == body180
        status, headers, body = req("/manifest.webmanifest")
        assert status == 200, status
        import json as _json
        manifest = _json.loads(body.decode("utf-8"))
        sizes = {i["sizes"] for i in manifest["icons"]}
        assert {"192x192", "512x512"} <= sizes, sizes
        # The page itself links the touch icon + manifest.
        status, _h, body = req(
            "/", {"Cookie": f"aria_bridge_token={_TOKEN}"})
        assert status == 200
        assert b'rel="apple-touch-icon" href="/icon-180.png?v=orb1"' in body
        assert b'rel="manifest" href="/manifest.webmanifest?v=orb1"' in body
    finally:
        bridge.ensure_bridge_cert = real_ensure
        if bridge._BRIDGE_SERVER is not None:
            bridge._BRIDGE_SERVER.shutdown()
            bridge._BRIDGE_SERVER.server_close()
            bridge._BRIDGE_SERVER = None
    print("ok pwa_icons_and_manifest")
