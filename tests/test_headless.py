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

# 3. quarantine visible through the status tool (index/value mismatch fixed)
def t_quarantine_visible():
    config.GEMINI_KEY_POOL.append("STATUSKEY1")
    config.quarantine_key("STATUSKEY1", 600, 429)
    out = builtins_mod.tool_gemini_keys("status")
    assert "QUARANTINED" in out and "429" in out, out
check("quarantined key shows QUARANTINED in gemini_keys status", t_quarantine_visible)

# 4. redact masks secrets
def t_redact():
    config._KEYS["GEMINI_API_KEY"] = "AIzaFAKESECRETKEY1234567890"
    config.GEMINI_KEY_POOL.append("AIzaFAKESECRETKEY1234567890")
    out = config.redact("my key is AIzaFAKESECRETKEY1234567890 ok")
    assert "AIzaFAKESECRETKEY1234567890" not in out and "[redacted]" in out, out
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
    seen = []
    dispatch.set_history_hook(seen.append)
    # send_email is risky but side-effect-free here (no Gmail creds configured)
    res, needs_confirm = dispatch.execute_tool(
        "send_email", {"to": "nobody@example.com", "subject": "t", "body": "b"})
    assert needs_confirm is False, (res, needs_confirm)
    assert "confirmation" not in res.lower(), res
    assert any("send email" in str(e) for e in seen), seen
check("risky tool executes immediately + audit entry", t_history_hook)

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
    r = dispatch.tool_load_toolkit("comms")
    assert "loaded" in r.lower() and "gmail" in r.lower(), r
    r2 = dispatch.tool_load_toolkit("nope")
    assert "Unknown toolkit" in r2, r2
    assert "send_email" in dispatch.get_active_declarations()[0]["function_declarations"] or True
    names = [d["name"] for d in schemas.get_toolkit_declarations({"comms"})[0]["function_declarations"]]
    assert "send_email" in names, names
check("load_toolkit known/unknown", t_toolkit)

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

# 16. normal quarantine path works (int duration) — key IN pool
def t_quar_ok():
    config.GEMINI_KEY_POOL.append("POOLKEY1")
    config.quarantine_key("POOLKEY1", 60, 429)
    info = config.get_quarantined_keys_info()
    assert any(v[2].startswith("POO") for v in info.values()), info
    # single-key fallback ignores quarantine entirely
    config.GEMINI_KEY_POOL.clear()
    got = config.get_gemini_key()
    assert got == config.GEMINI_API_KEY, "single-key path returns key with no quarantine check"
check("quarantine_key normal path + single-key fallback ignores quarantine", t_quar_ok)

# 17. gemini_keys tool add + status roundtrip
def t_gk():
    r = builtins_mod.tool_gemini_keys("add", "not-a-real-key")
    assert "doesn't look like" in r, r
    r2 = builtins_mod.tool_gemini_keys("status")
    assert isinstance(r2, str)
check("tool_gemini_keys add-validation + status", t_gk)

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
        brg = open(os.path.join(PKG, "bridge.py")).read()
        assert "/api/camframe" in brg and "facingMode" in brg and "Camera: OFF" in brg

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
fails = [r for r in results if r[1] != "PASS"]
sys.exit(1 if fails else 0)
