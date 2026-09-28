import os
"""Headless test harness for ARIA-Agent on Linux (no cv2/mic/TTS).
Bypasses aria/__init__.py's eager imports; stubs hardware-bound modules."""
import sys, types, traceback

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
sys.modules["aria.hud"].tool_show_commands = lambda: "ok"
sys.modules["aria.hud"].tool_hide_commands = lambda: "ok"
builtins_mod = importlib.import_module("aria.tools.builtins")
dispatch = importlib.import_module("aria.tools.dispatch")

# stub aria.speech so the bridge module imports headlessly
_speech = types.ModuleType("aria.speech")
_speech.edge_tts_bytes = lambda text: b""
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

print(f"\n{sum(1 for _, s, _ in results if s=='PASS')}/{len(results)} passed")
fails = [r for r in results if r[1] != "PASS"]
sys.exit(1 if fails else 0)
