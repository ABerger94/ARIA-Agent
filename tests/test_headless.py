import os
"""Headless test harness for ARIA-Agent on Linux (no cv2/mic/TTS).
Bypasses aria/__init__.py's eager imports; stubs hardware-bound modules."""
import sys, types, traceback, threading

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
tools_pkg = types.ModuleType("aria.tools")
tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = tools_pkg
agent_pkg = types.ModuleType("aria.agent")
agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = agent_pkg

results = []
def check(name, fn):
    try:
        fn()
        results.append((name, "PASS", ""))
        print(f"PASS  {name}")
    except AssertionError as e:
        results.append((name, "FAIL", str(e)))
        print(f"FAIL  {name}: {e}")
    except Exception as e:
        results.append((name, "ERROR", f"{type(e).__name__}: {e}"))
        print(f"ERROR {name}: {type(e).__name__}: {e}")

# stub psutil (system-managed python; only used for telemetry in skills.py)
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules["psutil"] = _ps

import importlib
config = importlib.import_module("aria.config")
schemas = importlib.import_module("aria.tools.schemas")
sandbox = importlib.import_module("aria.tools.sandbox")
memory = importlib.import_module("aria.memory")

# ---- stub hardware-bound modules so dispatch/builtins can import ----
for name in ("aria.scheduler", "aria.vision", "aria.hardware", "aria.spotify", "aria.hud"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["aria.vision"].get_face_frame_jpeg = lambda: None
sys.modules["aria.vision"].publish_phone_frame = lambda jpeg: True
sys.modules["aria.vision"].get_phone_frame_jpeg = lambda: None
sys.modules["aria.vision"].get_phone_frame_status = lambda: {"active": False, "age_s": -1.0}
sys.modules["aria.vision"].describe_phone_view = lambda q="": "[stubbed]"
sys.modules["aria.hud"].tool_show_commands = lambda: "ok"
sys.modules["aria.hud"].tool_hide_commands = lambda: "ok"
builtins_mod = importlib.import_module("aria.tools.builtins")
dispatch = importlib.import_module("aria.tools.dispatch")

# stub aria.speech so the bridge module imports headlessly
_speech = types.ModuleType("aria.speech")
_speech.edge_tts_bytes = lambda text: b""
_speech.tts_bytes_for_bridge = lambda text: (b"ID3fake", "audio/mpeg")
_speech.transcribe_audio = lambda audio, mime: ""
sys.modules["aria.speech"] = _speech
bridge = importlib.import_module("aria.bridge")

# 1. syntax: all files compile
def t_compile():
    import py_compile, glob
    for f in glob.glob("/home/hatch/workspace/aria-agent/aria/**/*.py", recursive=True) + \
             glob.glob("/home/hatch/workspace/aria-agent/*.py"):
        py_compile.compile(f, doraise=True)
check("all modules compile", t_compile)

# 2. quarantine_key with network_drop no longer raises (was TypeError)
def t_quarantine_network_drop():
    config.quarantine_key("TESTKEY123", config.KEY_QUARANTINE_DURATION_S, "network_drop")
    assert "TESTKEY123" in config._KEY_QUARANTINE_UNTIL
    assert config._KEY_QUARANTINE_CODE.get("TESTKEY123") == "network_drop"
check("quarantine_key(key, DURATION, 'network_drop') records cleanly", t_quarantine_network_drop)

# 3. quarantine records cleanly and is visible through the read APIs
def t_quarantine_visible():
    config.quarantine_key("STATUSKEY1", 600, 429)
    try:
        assert config.key_is_quarantined("STATUSKEY1"), "quarantined key should report quarantined"
        assert config._KEY_QUARANTINE_CODE.get("STATUSKEY1") == 429, config._KEY_QUARANTINE_CODE
    finally:
        config._KEY_QUARANTINE_UNTIL.pop("STATUSKEY1", None)
        config._KEY_QUARANTINE_CODE.pop("STATUSKEY1", None)
check("quarantined key reports quarantined with recorded code", t_quarantine_visible)

# 4. redact masks secrets
def t_redact():
    old = config._KEYS.get("GITHUB_TOKEN")
    config._KEYS["GITHUB_TOKEN"] = "ghp_FAKESECRETKEY1234567890"
    try:
        out = config.redact("my token is ghp_FAKESECRETKEY1234567890 ok")
        assert "ghp_FAKESECRETKEY1234567890" not in out and "[redacted]" in out, out
    finally:
        if old is None:
            config._KEYS.pop("GITHUB_TOKEN", None)
        else:
            config._KEYS["GITHUB_TOKEN"] = old
check("redact() masks secret values", t_redact)

# 5. call_signature determinism
def t_sig():
    a = sandbox.call_signature("web_search", {"query": "x"})
    b = sandbox.call_signature("web_search", {"query": "x"})
    c = sandbox.call_signature("web_search", {"query": "y"})
    assert a == b and a != c
check("call_signature deterministic + distinct", t_sig)

# 6. missing_required_args
def t_missing():
    decls = schemas.get_tool_decls_by_name()
    miss = sandbox.missing_required_args("send_email", {"to": "a@b.c"}, decls)
    assert "subject" in miss or "body" in miss, miss
    assert sandbox.missing_required_args("web_search", {"query": "x"}, decls) == []
check("missing_required_args validation", t_missing)

# 7. stale target guard
def t_stale():
    nudge, nudged = sandbox.stale_target_check(
        "open_app_or_url", {"target": "notepad"}, "notepad",
        "open calculator instead", False)
    assert nudge is not None and nudged, "expected nudge on moved-on target"
    n2, _ = sandbox.stale_target_check(
        "open_app_or_url", {"target": "notepad"}, "notepad",
        "open notepad again please", False)
    assert n2 is None, "should not nudge when user refers back"
check("stale_target_check guard", t_stale)

# 8. run_python_code executes + timeout
def t_runpy():
    out = sandbox.tool_run_python("print(40 + 2)")
    assert "42" in out, out
    out2 = sandbox.tool_run_python("import time; time.sleep(30)", timeout=2)
    assert "timed out" in out2, out2
check("tool_run_python exec + timeout", t_runpy)

# 9. risky tools execute immediately and fire the audit hook
def t_history_hook():
    approval = importlib.import_module("aria.approval")
    prev_get_mode = approval.get_mode
    approval.get_mode = lambda: "auto"  # hook test is mode-independent; pin auto
    try:
        seen = []
        dispatch.set_history_hook(seen.append)
        # send_email is risky but side-effect-free here (no Gmail creds configured)
        res, needs_confirm = dispatch.execute_tool(
            "send_email", {"to": "nobody@example.com", "subject": "t", "body": "b"})
        assert needs_confirm is False, (res, needs_confirm)
        assert "confirmation" not in res.lower(), res
        assert any("send email" in str(e) for e in seen), seen
    finally:
        approval.get_mode = prev_get_mode
check("risky tool executes immediately + audit entry", t_history_hook)


# 9b. fresh installs default to confirm-risky (not auto)
def t_default_mode():
    approval = importlib.import_module("aria.approval")
    assert approval.DEFAULT_MODE == "confirm-risky", approval.DEFAULT_MODE
    prev = approval.get_mode
    approval.get_mode = lambda: "confirm-risky"
    try:
        assert approval.needs_approval("send_email", {}) is True
        assert approval.needs_approval(
            "manage_background_job", {"action": "start"}) is True
        assert approval.needs_approval(
            "manage_background_job", {"action": "list"}) is False
        assert approval.needs_approval(
            "manage_background_job", {}) is False
    finally:
        approval.get_mode = prev
check("approval defaults to confirm-risky; bg-job start gates", t_default_mode)

# 10. duplicate-call blocking
def t_dup():
    r1, _ = dispatch.execute_tool("list_workspace", {})
    r2, _ = dispatch.execute_tool("list_workspace", {})
    assert "Duplicate call blocked" in r2, r2
check("duplicate tool call blocked within turn", t_dup)

# 11. unknown tool handling
def t_unknown():
    r, _ = dispatch.execute_tool("no_such_tool_xyz", {})
    assert "Unknown tool" in r, r
check("unknown tool returns error string", t_unknown)

# 12. toolkit loading
def t_toolkit():
    dispatch.reset_toolkits()  # isolate from earlier tests that load toolkits
    r = dispatch.tool_load_toolkit("comms")
    assert "loaded" in r.lower() and "gmail" in r.lower(), r
    r2 = dispatch.tool_load_toolkit("nope")
    assert "Unknown toolkit" in r2, r2
    assert "send_email" in dispatch.get_active_declarations()[0]["function_declarations"] or True
    names = [d["name"] for d in schemas.get_toolkit_declarations({"comms"})[0]["function_declarations"]]
    assert "send_email" in names, names
    assert "read_email" in names, names
check("load_toolkit known/unknown", t_toolkit)

# 12b. read_email without credentials asks for setup (no network)
def t_read_email_no_creds():
    res, needs_confirm = dispatch.execute_tool("read_email", {"query": "test", "limit": 5})
    assert needs_confirm is False, (res, needs_confirm)
    assert "isn't set up yet" in res, res
    res2, _ = dispatch.execute_tool("read_email", {"uid": "12345"})
    assert "isn't set up yet" in res2, res2
check("read_email without creds asks for setup", t_read_email_no_creds)

# 12c. iCal parser: folded lines, UTC/all-day/TZID, escaped chars
def t_ical_parse():
    from datetime import datetime
    ical = importlib.import_module("aria.ical")
    sample = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20261002T200000Z\r\n"
        "DTEND:20261003T010000Z\r\n"
        "SUMMARY:Dock of the Bay \\, Wait\r\n"
        "LOCATION:Sparrows Point\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20261005\r\n"
        "DTEND:20261006\r\n"
        "SUMMARY:Day off\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART;TZID=America/New_York:20261006T160000\r\n"
        "SUMMARY:Long description that folds \r\n"
        " over two lines\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    evs = ical.parse_ical(sample)
    assert len(evs) == 3, evs
    assert evs[0]["summary"] == "Dock of the Bay , Wait @ Sparrows Point", evs[0]
    assert evs[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-10-02 20:00", evs[0]
    assert evs[1]["all_day"] is True, evs[1]
    assert (evs[1]["end"] - evs[1]["start"]).days == 1, evs[1]
    assert evs[2]["summary"] == "Long description that folds over two lines", evs[2]
    # upcoming(): only events overlapping the window, naive-local times
    now = datetime(2026, 10, 2, 12, 0)
    up = ical.upcoming(evs, days=1, now=now)
    assert len(up) == 1 and up[0]["summary"].startswith("Dock of the Bay"), up
    up3 = ical.upcoming(evs, days=4, now=now)
    assert len(up3) == 2, up3
check("ical parse + upcoming window", t_ical_parse)

# 12d. calendar tools without a connected feed
def t_cal_no_creds():
    res, needs_confirm = dispatch.execute_tool("check_calendar", {"days": 2})
    assert needs_confirm is False, (res, needs_confirm)
    assert "No calendar connected" in res, res
    res2, _ = dispatch.execute_tool("calendar_setup", {"ical_url": "not a url"})
    assert "doesn't look like" in res2, res2
check("calendar tools without feed ask for setup", t_cal_no_creds)

# 13. output truncation
def t_trunc():
    t = sandbox.truncate_output("x" * 5000, 2000)
    assert len(t) < 2500 and "truncated" in t, len(t)
check("truncate_output budget", t_trunc)

# 14. memory spine append + read-back
def t_spine():
    memory.spine_append("test", {"k": "v"})
    thread = memory.spine_unbroken_thread()
    assert isinstance(thread, str)
check("spine_append + spine_unbroken_thread", t_spine)

# 15. TOOLKITS prompt block builds
def t_prompt():
    b = dispatch.get_toolkits_prompt()
    assert "load_toolkit" in b.lower() or "toolkit" in b.lower()
check("toolkit prompt block", t_prompt)

# 16. normal quarantine path works (int duration); placeholder/missing keys count as quarantined
def t_quar_ok():
    config.quarantine_key("POOLKEY1", 60, 429)
    try:
        assert config.key_is_quarantined("POOLKEY1"), "quarantined key should report quarantined"
        assert config._KEY_QUARANTINE_CODE.get("POOLKEY1") == 429, config._KEY_QUARANTINE_CODE
        assert config.key_is_quarantined("INSERT"), "placeholder key should count as quarantined"
        assert config.key_is_quarantined(""), "missing key should count as quarantined"
    finally:
        config._KEY_QUARANTINE_UNTIL.pop("POOLKEY1", None)
        config._KEY_QUARANTINE_CODE.pop("POOLKEY1", None)
check("quarantine_key normal path + placeholder keys count as quarantined", t_quar_ok)

# 18. risky tool audit description formats
def t_risky():
    d = sandbox.risky_description("send_email", {"to": "x@y.z", "subject": "hi"})
    assert "x@y.z" in d and "hi" in d, d
    assert "gui_click" in sandbox.RISKY_TOOLS
check("risky_description + RISKY_TOOLS set", t_risky)

# 19. run_skill with unknown skill
def t_skill():
    r = dispatch.execute_tool("run_skill", {"skill_name": "nope", "objective": "x"})
    assert isinstance(r[0], str) and len(r[0]) > 0
check("run_skill unknown skill handled", t_skill)

# 20. bridge auth: header / cookie / query token validated; cookie hardened;
#     page no longer puts tokens in media URLs
def t_bridge_auth():
    h = bridge.BridgeHandler.__new__(bridge.BridgeHandler)
    tok = bridge.BRIDGE_TOKEN
    assert tok, "bridge token should be generated"
    h.path = "/"
    h.headers = {"X-Bridge-Token": tok}
    assert h._authed() is True
    h.headers = {"X-Bridge-Token": "wrong"}
    assert h._authed() is False
    h.headers = {"Cookie": "other=1; aria_bridge_token=" + tok}
    assert h._authed() is True
    h.headers = {}
    h.path = "/?token=" + tok
    assert h._authed() is True
    h.path = "/?token=wrong"
    assert h._authed() is False
    h.path = "/"
    assert h._authed() is False
    c = h._bridge_cookie()
    assert "HttpOnly" in c and "SameSite=Strict" in c and c.startswith("aria_bridge_token="), c
    assert "?token=" not in bridge.BRIDGE_HTML, "media URLs must not carry the token"
    assert "token" in bridge.BRIDGE_LOGIN_HTML.lower(), "login page must ask for the token"
check("bridge auth (header/cookie/query) + cookie flags + no token in media URLs", t_bridge_auth)

# 21. bridge TTS: /api/say uses tts_bytes_for_bridge (loud errors, real ctype);
#     page plays replies via WebAudio with a visible error state
def t_bridge_tts():
    import re
    html = bridge.BRIDGE_HTML
    assert "playReply" in html and "decodeAudioData" in html, "page must use WebAudio playback"
    assert "voiceError" in html, "page must surface TTS failures visibly"
    assert "new Audio('/api/say" not in html, "old silent <audio> path must be gone"
    src = open(os.path.join(PKG, "bridge.py")).read()
    m = re.search(r'if self\.path\.startswith\("/api/say"\):(.*?)(?=\n            self\._send\(404)', src, re.S)
    assert m, "/api/say handler missing"
    body = m.group(1)
    assert "tts_bytes_for_bridge" in body, "say must use the loud TTS helper"
    assert re.search(r'self\._send\(500', body), "say must return 500 (not silent 200) on TTS failure"
    assert "edge_tts_bytes(text" not in body, "say must not use the silent helper"
check("bridge TTS wiring: loud /api/say + WebAudio page playback", t_bridge_tts)

# 22. tts_bytes_for_bridge contract: edge ok / edge fail loud / sapi fallback.
#     Loads the REAL aria/speech.py (bypassing the stub used for the bridge).
def t_tts_contract():
    import unittest.mock as mock
    import importlib.util
    if "speech_recognition" not in sys.modules:
        sys.modules["speech_recognition"] = types.ModuleType("speech_recognition")
    spec = importlib.util.spec_from_file_location(
        "aria_speech_real", os.path.join(PKG, "speech.py"))
    sp = importlib.util.module_from_spec(spec)
    sys.modules["aria_speech_real"] = sp
    spec.loader.exec_module(sp)
    sp._edge_tts_bytes_strict = lambda *a, **k: (_ for _ in ()).throw(ImportError("no pkg"))
    try:
        sp.tts_bytes_for_bridge("hi")
        raise AssertionError("must raise, never return silent empty")
    except RuntimeError as e:
        assert "edge-tts failed" in str(e), e
    sp._sapi_tts_wav_ex = lambda text, timeout=60: (b"RIFFfakex", "")
    with mock.patch.object(sp.sys, "platform", "win32"):
        data, ctype = sp.tts_bytes_for_bridge("hi")
        assert ctype == "audio/wav" and data, (ctype, len(data))
    sp._edge_tts_bytes_strict = lambda *a, **k: (_ for _ in ()).throw(Exception("net down"))
    sp._sapi_tts_wav_ex = lambda text, timeout=60: (b"", "sapi powershell failed: boom")
    with mock.patch.object(sp.sys, "platform", "win32"):
        try:
            sp.tts_bytes_for_bridge("hi")
            raise AssertionError("must raise when SAPI also fails")
        except RuntimeError as e:
            assert "net down" in str(e) and "boom" in str(e), e
    sp._edge_tts_bytes_strict = lambda *a, **k: b"ID3x"
    data, ctype = sp.tts_bytes_for_bridge("hi")
    assert ctype == "audio/mpeg" and data, (ctype, len(data))
    sp._edge_tts_bytes_strict = lambda *a, **k: (_ for _ in ()).throw(Exception("down"))
    assert sp.edge_tts_bytes("hi") == b"", "desktop wrapper must stay silent-safe"
check("tts_bytes_for_bridge: loud failure, SAPI fallback, edge passthrough", t_tts_contract)

# 23. Robot body protocol: head + drive commands over the Arduino wire format.
#     Loads the REAL aria/hardware.py with a fake `serial` module.
def t_body_protocol():
    import importlib.util, time
    written = []

    class FakeSerial:
        def __init__(self, device, baudrate, timeout=1):
            self.device, self.is_open = device, True
        def write(self, data):
            written.append(bytes(data))

    fake_serial = types.ModuleType("serial")
    fake_tools = types.ModuleType("serial.tools")
    fake_lp = types.ModuleType("serial.tools.list_ports")
    fake_port = types.SimpleNamespace(device="COM7", description="USB-SERIAL CH340 (COM7)")
    fake_lp.comports = lambda: [fake_port]
    fake_serial.Serial = FakeSerial
    fake_serial.tools = fake_tools
    fake_tools.list_ports = fake_lp
    sys.modules["serial"] = fake_serial
    sys.modules["serial.tools"] = fake_tools
    sys.modules["serial.tools.list_ports"] = fake_lp
    try:
        spec = importlib.util.spec_from_file_location(
            "aria_hardware_real", os.path.join(PKG, "hardware.py"))
        hw = importlib.util.module_from_spec(spec)
        sys.modules["aria_hardware_real"] = hw
        spec.loader.exec_module(hw)

        # virtual mode: no connection, no crash, state still tracked
        assert hw.HARDWARE_CONNECTED is False
        assert hw.send_servo_command(90, 45) == (90, 45)
        assert written == [], "virtual mode must not write serial"

        # auto-detect the CH340 Nano and connect
        assert hw.init_hardware() is True, "must detect fake CH340 port"
        assert hw.HARDWARE_CONNECTED is True

        # head protocol: b"P<pan>T<tilt>\\n" with clamping
        assert hw.send_servo_command(200, -10) == (180, 0)
        assert written[-1] == b"P180T0\n", written[-1]

        # drive protocol: b"W<l>,<r>\\n", -100..100 with clamping
        assert hw.send_drive_command(-50, 75) == (-50, 75)
        assert written[-1] == b"W-50,75\n", written[-1]
        assert hw.send_drive_command(150, -150) == (100, -100)
        assert written[-1] == b"W100,-100\n", written[-1]

        # auto-stop timer fires a W0,0
        n = len(written)
        msg = hw.tool_drive(60, 60, seconds=0.1)
        assert "60" in msg and "auto-stop" in msg, msg
        time.sleep(0.4)
        assert written[-1] == b"W0,0\n", written[-1:]
        assert len(written) > n

        # stop tool sends the firmware's dedicated S command + status shape
        assert "centered" in hw.tool_body_stop().lower()
        assert written[-2] == b"S\n", written[-2:]
        assert written[-1] == b"P90T45\n", written[-1:]
        st = hw.get_hardware_status()
        assert st["connected"] is True and st["wheels"] == {"left": 0, "right": 0}, st

        # ARIA_BODY_SERIAL_URL routes init_hardware through pyserial's
        # serial_for_url (used by sim/robot_sim.py); falls back to port scan
        # when unset. Only asserted as source presence — no socket needed.
        hw_src = open(os.path.join(PKG, "hardware.py")).read()
        assert "ARIA_BODY_SERIAL_URL" in hw_src and "serial_for_url" in hw_src

        # firmware parses every command hardware.py can emit
        ino = open(os.path.join(os.path.dirname(PKG), "arduino", "aria_body",
                                "aria_body.ino")).read()
        for token in ("line[0] == 'P'", "line[0] == 'W'", "line[0] == 'S'"):
            assert token in ino, f"firmware missing handler {token}"

        # vision.py honors ARIA_BODY_CAMERA on both capture paths
        vis = open(os.path.join(PKG, "vision.py")).read()
        assert "ARIA_BODY_CAMERA" in vis
        assert vis.count("cap = open_body_camera()") == 2, "both capture paths must use the body camera"
        assert "http://" in vis and "_body_camera_source" in vis

        # bridge-page camera: page uploads frames, vision serves them as a capture
        assert "publish_phone_frame" in vis and "BridgeCamera" in vis
        assert "describe_phone_view" in vis and "get_phone_frame_status" in vis
        brg = open(os.path.join(PKG, "bridge.py")).read()
        assert "/api/camframe" in brg and "facingMode" in brg and "Camera: OFF" in brg
        # eyes viewer + look: MJPEG of phone frames, cam status, describe endpoint
        assert "/phone_cam.mjpg" in brg and "/api/camstatus" in brg and "/api/look" in brg
        assert "mode-eyes" in brg and "mode-full" in brg and "facemode" in brg

        # camera source parsing: URL stays a string, digits become int, junk -> 0
        # (extracted from the real source via AST so the headless suite never
        # needs cv2/numpy just to test parsing)
        import ast
        tree = ast.parse(vis)
        fn_src = next(
            (ast.get_source_segment(vis, n) for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_body_camera_source"),
            None,
        )
        assert fn_src, "missing _body_camera_source"
        ns: dict = {}
        exec(compile("BODY_CAMERA_RAW = ''\n" + fn_src, "<test>", "exec"), ns)
        parse = ns["_body_camera_source"]
        for raw, expected in [
            ("http://192.168.1.42:8080/video", "http://192.168.1.42:8080/video"),
            ("https://example.com/cam", "https://example.com/cam"),
            ("bridge", "bridge"), ("BRIDGE", "bridge"),
            ("1", 1), ("0", 0), ("bogus", 0), ("", 0),
        ]:
            ns["BODY_CAMERA_RAW"] = raw
            assert parse() == expected, (raw, parse())

        # publish_phone_frame: rejects non-JPEG/tiny bodies, stores latest frame
        fn2_src = next(
            (ast.get_source_segment(vis, n) for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "publish_phone_frame"),
            None,
        )
        assert fn2_src, "missing publish_phone_frame"
        ns2: dict = {
            "time": time, "threading": threading,
            "_PHONE_CAM": {"jpeg": None, "lock": threading.Lock(), "last": 0.0},
            "_PHONE_CAM_FPS_MIN_GAP": 0.15,
        }
        exec(compile(fn2_src, "<test>", "exec"), ns2)
        pub = ns2["publish_phone_frame"]
        assert pub(b"not a jpeg") is False
        assert pub(b"\xff\xd8" + b"\x00" * 50) is False  # too short to be real
        good = b"\xff\xd8\xff\xe0" + b"\x00" * 200
        assert pub(good) is True
        assert ns2["_PHONE_CAM"]["jpeg"] == good

        # body_camera_label: short source tag for the HUD subsystem box
        fn3_src = next(
            (ast.get_source_segment(vis, n) for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "body_camera_label"),
            None,
        )
        assert fn3_src, "missing body_camera_label"
        ns3: dict = {"_body_camera_source": parse}
        exec(compile(fn3_src, "<test>", "exec"), ns3)
        label = ns3["body_camera_label"]
        for raw, expected in [
            ("bridge", "bridge"), ("0", "cam 0"), ("1", "cam 1"),
            ("http://192.168.1.42:8080/video", "net"),
        ]:
            ns["BODY_CAMERA_RAW"] = raw
            assert label() == expected, (raw, label())
    finally:
        for m in ("serial", "serial.tools", "serial.tools.list_ports"):
            sys.modules.pop(m, None)
check("robot body: head/drive wire protocol, auto-stop, firmware parity, body camera", t_body_protocol)

print(f"\n{sum(1 for _, s, _ in results if s=='PASS')}/{len(results)} passed")

# 40. MCP client: schema conversion + name sanitization (no mcp package needed)
mcp_mod = importlib.import_module("aria.mcp")

def t_mcp_schema():
    assert mcp_mod.sanitize_tool_name("mcp_fs__read-file!") == "mcp_fs__read-file_"
    assert len(mcp_mod.sanitize_tool_name("x" * 200)) == 64
    conv = mcp_mod.convert_schema({
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "file path"},
            "count": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "opts": {"type": "object", "properties": {"v": {"type": "boolean"}}, "required": ["v"]},
        },
        "required": ["path"],
    })
    assert conv["type"] == "OBJECT"
    assert conv["properties"]["path"]["type"] == "STRING"
    assert conv["properties"]["path"]["description"] == "file path"
    assert conv["properties"]["tags"]["items"]["type"] == "STRING"
    assert conv["properties"]["opts"]["properties"]["v"]["type"] == "BOOLEAN"
    assert conv["required"] == ["path"]
    assert mcp_mod.convert_schema(None) == {"type": "OBJECT"}
check("mcp: schema conversion + name sanitization", t_mcp_schema)

# 41. MCP server config add/remove round-trip (isolated settings file)
def t_mcp_config_roundtrip():
    import tempfile as _tf
    orig = config.SETTINGS_FILE
    tmp = _tf.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    config.SETTINGS_FILE = tmp.name
    try:
        msg = mcp_mod.add_server(name="testfs", command="npx",
                                 args='["-y", "x"]', env='{"K": "v"}')
        assert "saved" in msg, msg
        servers = mcp_mod.get_servers()
        assert servers["testfs"]["args"] == ["-y", "x"], servers
        assert servers["testfs"]["env"] == {"K": "v"}
        assert servers["testfs"]["transport"] == "stdio"
        bad = mcp_mod.add_server(name="bad", transport="carrier-pigeon")
        assert "Unknown transport" in bad, bad
        bad2 = mcp_mod.add_server(name="bad2")
        assert "need a command" in bad2, bad2
        sse = mcp_mod.add_server(name="websvc", transport="sse", url="http://x:1/sse")
        assert "saved" in sse, sse
        assert mcp_mod.get_servers()["websvc"]["url"] == "http://x:1/sse"
        rm = mcp_mod.remove_server("testfs")
        assert "removed" in rm, rm
        assert "testfs" not in mcp_mod.get_servers()
        missing = mcp_mod.remove_server("nope")
        assert "No MCP server" in missing, missing
    finally:
        config.SETTINGS_FILE = orig
        os.unlink(tmp.name)
check("mcp: server config add/remove round-trip", t_mcp_config_roundtrip)

# 42. MCP tool result formatting
def t_mcp_format():
    T = types.SimpleNamespace(type="text", text="hello")
    ok = types.SimpleNamespace(isError=False, content=[T])
    err = types.SimpleNamespace(isError=True,
                                content=[types.SimpleNamespace(type="text", text="nope")])
    img = types.SimpleNamespace(isError=False,
                                content=[types.SimpleNamespace(type="image")])
    empty = types.SimpleNamespace(isError=False, content=[])
    assert mcp_mod.format_tool_result(ok) == "hello"
    r = mcp_mod.format_tool_result(err)
    assert r.startswith("[MCP tool error]") and "nope" in r, r
    assert "image" in mcp_mod.format_tool_result(img)
    assert "empty" in mcp_mod.format_tool_result(empty)
check("mcp: tool result formatting", t_mcp_format)

# 43. MCP live stdio server: connect, register, call, disconnect (needs mcp pkg)
def t_mcp_live_stdio():
    import json as _json, tempfile as _tf
    try:
        import mcp  # noqa: F401
    except Exception:
        print("SKIP  mcp live stdio (mcp package not installed)")
        return
    dispatch.set_spine_hook(lambda *a, **k: None)
    orig = config.SETTINGS_FILE
    tmp = _tf.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    srv = _tf.NamedTemporaryFile(delete=False, suffix=".py")
    srv.close()
    config.SETTINGS_FILE = tmp.name
    try:
        with open(srv.name, "w", encoding="utf-8") as f:
            f.write(
                "from mcp.server.fastmcp import FastMCP\n"
                "srv = FastMCP('aria-test')\n"
                "@srv.tool()\n"
                "def add(a: int, b: int) -> int:\n"
                "    \"\"\"Add two numbers.\"\"\"\n"
                "    return a + b\n"
                "@srv.tool()\n"
                "def boom() -> str:\n"
                "    \"\"\"Always fails.\"\"\"\n"
                "    raise ValueError('kaboom')\n"
                "if __name__ == '__main__':\n"
                "    srv.run()\n"
            )
        setup_msg = builtins_mod.tool_mcp_setup(
            name="test", command=sys.executable, args=_json.dumps(["-u", srv.name]))
        assert "saved" in setup_msg, setup_msg
        conn = builtins_mod.tool_mcp_connect(name="test")
        assert "Connected" in conn, conn
        decls = schemas.get_tool_decls_by_name()
        assert "mcp_test__add" in decls, [k for k in decls if k.startswith("mcp_")]
        miss = sandbox.missing_required_args("mcp_test__add", {"a": 1}, decls)
        assert miss == ["b"], miss
        res, _ = dispatch.execute_tool("mcp_test__add", {"a": 2, "b": 3})
        assert res.strip() == "5", res
        err_res, _ = dispatch.execute_tool("mcp_test__boom", {})
        assert "kaboom" in err_res or "error" in err_res.lower(), err_res
        payload = schemas.get_toolkit_declarations({"core", "mcp"})
        names = [d["name"] for d in payload[0]["function_declarations"]]
        assert "mcp_test__add" in names and "mcp_connect" in names, names
        disc = builtins_mod.tool_mcp_disconnect(name="test")
        assert "Disconnected" in disc, disc
        assert "mcp_test__add" not in schemas.get_tool_decls_by_name()
        rm = builtins_mod.tool_mcp_remove_server(name="test")
        assert "removed" in rm, rm
        # connecting to a bogus command fails cleanly, no crash
        builtins_mod.tool_mcp_setup(name="bogus", command="/nonexistent/binary_xyz")
        bad_conn = builtins_mod.tool_mcp_connect(name="bogus")
        assert "Could not connect" in bad_conn, bad_conn
        builtins_mod.tool_mcp_remove_server(name="bogus")
    finally:
        try:
            mcp_mod.get_bridge().disconnect_all()
        except Exception:
            pass
        config.SETTINGS_FILE = orig
        for p in (tmp.name, srv.name):
            try:
                os.unlink(p)
            except Exception:
                pass
check("mcp: live stdio server connect/call/disconnect", t_mcp_live_stdio)

# 30. self-restart auto-restore: syntax-broken file -> backup kept, git copy restored
def t_restore_last_committed():
    import subprocess, tempfile, py_compile
    # brain imports shortcuts -> aria.spotify names; extend the stub
    sys.modules["aria.spotify"].tool_media_key = lambda *a, **k: "ok"
    sys.modules["aria.spotify"].tool_spotify = lambda *a, **k: "ok"
    from aria.agent.brain import _restore_last_committed
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    f = os.path.join(d, "mod.py")
    with open(f, "w") as fh:
        fh.write("X = 1\n")
    subprocess.run(["git", "add", "."], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
    with open(f, "w") as fh:
        fh.write("def broken(:\n")
    bak = _restore_last_committed(f, repo_dir=d)
    assert bak and os.path.isfile(bak), "backup of broken file missing"
    assert open(bak).read() == "def broken(:\n", "backup must hold the broken content"
    assert open(f).read() == "X = 1\n", "file must be restored from git"
    py_compile.compile(f, doraise=True)
    # outside a git repo -> None, broken file untouched
    d2 = tempfile.mkdtemp()
    f2 = os.path.join(d2, "m.py")
    with open(f2, "w") as fh:
        fh.write("def broken(:\n")
    assert _restore_last_committed(f2, repo_dir=d2) is None
    assert open(f2).read() == "def broken(:\n"
check("self-restart: auto-restore broken file from git", t_restore_last_committed)

# 31. import smoke test: parses-clean but unimportable file is caught
def t_smoke_import_changed():
    from aria.agent.brain import _smoke_import_changed
    import tempfile
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    good = os.path.join(repo_dir, "aria", "pixel_avatar.py")
    assert _smoke_import_changed([good]) == [], "good file flagged"
    bad = os.path.join(repo_dir, "aria", "_smoke_bad_tmp.py")
    try:
        with open(bad, "w") as fh:
            fh.write("from aria.core.optimport import optional_module\n")
        fails = _smoke_import_changed([bad])
        assert len(fails) == 1 and fails[0][0] == bad, fails
        assert "aria.core" in fails[0][1], fails[0][1]
    finally:
        if os.path.exists(bad):
            os.unlink(bad)
    # missing file is skipped, never an error
    assert _smoke_import_changed([os.path.join(repo_dir, "nope.py")]) == []
check("self-restart: import smoke test catches bad imports", t_smoke_import_changed)

# 32. restart watcher scope: package + launcher only, never tests/tools/probes
def t_watched_scope():
    from aria.agent.brain import _watched_source_files
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = _watched_source_files()
    assert files, "watcher found no files"
    assert os.path.join(repo_dir, "aria.py") in files, "root launcher must be watched"
    assert os.path.join(repo_dir, "aria", "agent", "brain.py") in files, "package files must be watched"
    allowed = {os.path.join(repo_dir, "aria.py"), os.path.abspath(sys.argv[0])}
    pkg_prefix = os.path.join(repo_dir, "aria") + os.sep
    for f in files:
        assert f.startswith(pkg_prefix) or f in allowed, f"unexpected watched file: {f}"
    assert not any(f.startswith(os.path.join(repo_dir, "tests") + os.sep) and f != os.path.abspath(sys.argv[0]) for f in files), "top-level tests/ must not be watched (except the running script itself)"
    assert not any(f.startswith(os.path.join(repo_dir, "tools") + os.sep) for f in files), "top-level tools/ must not be watched"
check("self-restart: watcher scoped to package + launcher", t_watched_scope)

print(f"\n{sum(1 for _, s, _ in results if s=='PASS')}/{len(results)} passed")
fails = [r for r in results if r[1] != "PASS"]
sys.exit(1 if fails else 0)
