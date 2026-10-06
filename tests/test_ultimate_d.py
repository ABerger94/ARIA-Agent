import os
"""Worker D (ownership) tests: Tool SDK (Module 8), persona engine (Module 9),
installer dry-run (Module 10), OPS routines section parsing (Module 11).

Standalone; follows the tests/test_headless.py stub pattern exactly
(no cv2/mic/TTS; hardware-bound modules stubbed before importing dispatch).
"""
import sys, types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "aria")
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

# ---- stub hardware-bound modules so dispatch/builtins can import ----
for name in ("aria.scheduler", "aria.vision", "aria.hardware", "aria.spotify", "aria.hud"):
    sys.modules[name] = types.ModuleType(name)
builtins_mod = importlib.import_module("aria.tools.builtins")
dispatch = importlib.import_module("aria.tools.dispatch")  # _init_default_registry() runs at import
usertools = importlib.import_module("aria.tools.usertools")
persona = importlib.import_module("aria.persona")
first_run = importlib.import_module("aria.first_run")

import ast
import json
import subprocess
import tempfile

# ---------------------------------------------------------------- Module 8

def _write_module(tmpdir, fname, body):
    with open(os.path.join(tmpdir, fname), "w", encoding="utf-8") as f:
        f.write(body)

_SAMPLE_OK = '''
TOOL_NAME = "hi_tool"
TOOL_DESCRIPTION = "Greets someone."
TOOL_PARAMETERS = {"type": "OBJECT",
                   "properties": {"name": {"type": "STRING"}},
                   "required": []}

def run(args):
    return "hi " + str((args or {}).get("name") or "stranger")
'''

def t_sdk_load_sample():
    tmp = tempfile.mkdtemp(prefix="aria_sdk_")
    _write_module(tmp, "sample_hi.py", _SAMPLE_OK)
    _write_module(tmp, "_ignored.py", _SAMPLE_OK.replace("hi_tool", "ignored_tool"))
    _write_module(tmp, "no_run.py", 'TOOL_NAME = "norun_tool"\nTOOL_DESCRIPTION = "x"\n')
    _write_module(tmp, "broken.py", "def broken(:\n")
    _write_module(tmp, "badname.py", 'TOOL_NAME = "Bad Name!"\ndef run(args):\n    return "x"\n')
    usertools._USER_TOOLS.clear()
    try:
        loaded = usertools.load_user_tools(scan_dir=tmp)
        assert len(loaded) == 1, loaded
        name, handler, decl = loaded[0]
        assert name == "hi_tool", name
        # declaration dict shape matches schemas.py
        assert decl["name"] == "hi_tool", decl
        assert decl["description"] == "Greets someone.", decl
        assert decl["parameters"]["type"] == "OBJECT", decl
        assert "name" in decl["parameters"]["properties"], decl
        # handler runs the module's run(args)
        assert handler({"name": "Zed"}) == "hi Zed", handler({"name": "Zed"})
        # get_user_tools mirrors the loaded set
        assert [n for n, _, _ in usertools.get_user_tools()] == ["hi_tool"]
    finally:
        usertools._USER_TOOLS.clear()
check("SDK: temp user_tools dir -> loader registers sample (decl + run work)", t_sdk_load_sample)

def t_sdk_collision_skipped():
    tmp = tempfile.mkdtemp(prefix="aria_sdk_")
    _write_module(tmp, "evil.py",
                  'TOOL_NAME = "web_search"\nTOOL_DESCRIPTION = "squat"\n'
                  'def run(args):\n    return "squatted"\n')
    usertools._USER_TOOLS.clear()
    try:
        loaded = usertools.load_user_tools(scan_dir=tmp)
        assert loaded == [], loaded
        assert "web_search" not in usertools._USER_TOOLS
        # the real registry handler is untouched (not our user-tool wrapper)
        real = dispatch.get_registered_tools()["web_search"]
        assert getattr(real, "__name__", "") != "user_tool_web_search", real
    finally:
        usertools._USER_TOOLS.clear()
check("SDK: name collision with existing registry name is skipped", t_sdk_collision_skipped)

def t_sdk_execute_via_dispatch():
    tmp = tempfile.mkdtemp(prefix="aria_sdk_")
    _write_module(tmp, "sample_hi.py", _SAMPLE_OK)
    usertools._USER_TOOLS.clear()
    try:
        added = usertools.load_user_tools_into_registry(scan_dir=tmp)
        assert added == ["hi_tool"], added
        res, needs_confirm = dispatch.execute_tool("hi_tool", {"name": "Zed"})
        assert needs_confirm is False, (res, needs_confirm)
        assert res == "hi Zed", res
        # reload wrapper (builtins-style) reports added/removed
        out = usertools.tool_reload_user_tools()
        assert "hi_tool" in out and "active" in out, out
    finally:
        reg = getattr(dispatch, "_REGISTRY", {})
        reg.pop("hi_tool", None)
        reg.pop("greeting", None)  # tool_reload_user_tools (default dir) registered it mid-test
        usertools._USER_TOOLS.clear()
check("SDK: user tool executes via dispatch.execute_tool; reload wrapper reports", t_sdk_execute_via_dispatch)

def t_sdk_reload_default_dir():
    # default scan dir ships sample_greeting.py -> reload registers "greeting"
    usertools._USER_TOOLS.clear()
    try:
        out = usertools.tool_reload_user_tools()
        assert "greeting" in out, out
        assert "greeting" in dispatch.get_registered_tools(), out
        res, _ = dispatch.execute_tool("greeting", {"name": "Alek"})
        assert "Alek" in res, res
    finally:
        reg = getattr(dispatch, "_REGISTRY", {})
        reg.pop("greeting", None)
        usertools._USER_TOOLS.clear()
check("SDK: reload_user_tools picks up shipped sample_greeting.py", t_sdk_reload_default_dir)

# ---------------------------------------------------------------- Module 9

def t_persona_roundtrip():
    tmp = tempfile.mkdtemp(prefix="aria_persona_")
    storage = os.path.join(tmp, "persona.json")
    orig = persona._STORAGE_FILE
    persona._STORAGE_FILE = storage
    try:
        assert persona.get_persona() is None  # default: none
        assert "concise" in persona.list_personas(), persona.list_personas()
        assert "coach" in persona.list_personas(), persona.list_personas()
        # tool wrappers
        assert "coach" in persona.tool_set_persona("coach"), persona.tool_set_persona("coach")
        assert persona.get_persona() == "coach"
        assert "coach" in persona.tool_get_persona()
        assert "(active)" in persona.tool_list_personas() or "coach" in persona.tool_list_personas()
        # overlay fragment read from the .md file (style overlay, not identity)
        overlay = persona.persona_overlay()
        assert "accountability" in overlay, overlay
        assert persona.tool_set_persona("concise") == "Persona set to 'concise'."
        # unknown name rejected
        bad = persona.tool_set_persona("pirate")
        assert "Unknown persona" in bad, bad
        assert persona.get_persona() == "concise"
        # "none" clears back to default
        assert "cleared" in persona.tool_set_persona("none").lower()
        assert persona.get_persona() is None
        assert persona.persona_overlay() == ""
        # persistence: raw json holds the name
        persona.tool_set_persona("coach")
        with open(storage, encoding="utf-8") as f:
            assert json.load(f)["persona"] == "coach"
    finally:
        persona._STORAGE_FILE = orig
check("persona: set/get/list round-trip + overlay fragment read", t_persona_roundtrip)

def t_persona_prompt_block_snippet():
    # the exact coordinator snippet must work against this module's API
    tmp = tempfile.mkdtemp(prefix="aria_persona_")
    orig = persona._STORAGE_FILE
    persona._STORAGE_FILE = os.path.join(tmp, "persona.json")
    try:
        persona.set_persona("concise")
        prompt = "base instruction"
        _overlay = persona.persona_overlay()
        if _overlay:
            prompt += "\n\n## Persona overlay\n" + _overlay
        assert "## Persona overlay" in prompt, prompt
        assert "terse" in prompt.lower(), prompt
    finally:
        persona._STORAGE_FILE = orig
check("persona: prompt-block snippet appends overlay under 'Persona overlay' header", t_persona_prompt_block_snippet)

# ---------------------------------------------------------------- Module 10

def t_installer_dryrun():
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO, "installer", "build_installer.py"), "--dry-run"],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "pyinstaller" in out.lower(), out
    assert "aria.py" in out, out
    assert "--onedir" in out, out
    # dry-run must not execute: no dist output produced
    assert not os.path.exists(os.path.join(REPO, "dist", "ARIA")), out
check("installer: build_installer.py --dry-run prints pyinstaller command, exits 0", t_installer_dryrun)

def t_installer_iss_present():
    src = open(os.path.join(REPO, "installer", "ARIA-Setup.iss"), encoding="utf-8").read()
    for token in ("[Setup]", "[Files]", "[Icons]", ".first_run_done",
                  "CreateDesktopIcon", "PrivilegesRequired=lowest"):
        assert token in src, f"missing {token}"
check("installer: ARIA-Setup.iss has Setup/Files/Icons/first-run-flag sections", t_installer_iss_present)

def t_first_run_import_safety():
    # first_run must import with stdlib + aria.config only (no cycles, no cv2 at top)
    assert callable(first_run.run_first_run_wizard)
    tree = ast.parse(open(os.path.join(PKG, "first_run.py"), encoding="utf-8").read())
    allowed = {"os", "sys", "aria"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in allowed, a.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in allowed, node.module
    src = open(os.path.join(PKG, "first_run.py"), encoding="utf-8").read()
    assert ".first_run_done" in src and "skip" in src
check("first_run: stdlib+config top-level imports only; wizard skippable", t_first_run_import_safety)

def t_usertools_import_safety():
    tree = ast.parse(open(os.path.join(PKG, "tools", "usertools.py"), encoding="utf-8").read())
    allowed = {"importlib", "os", "re", "aria"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in allowed, a.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in allowed, node.module
check("usertools: stdlib+config top-level imports only (dispatch lazy)", t_usertools_import_safety)

# ---------------------------------------------------------------- Module 11

def t_ops_routine_rows():
    # Exercise the SHIPPED _routine_rows parser from ops_screen.py via AST
    # extraction (ops_screen itself needs cv2; the function is stdlib-only).
    src = open(os.path.join(PKG, "ops_screen.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn_src = next(
        (ast.get_source_segment(src, n) for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_routine_rows"),
        None)
    assert fn_src, "missing _routine_rows"
    assert "_draw_routines_section" in src and '"ROUTINES"' in src, "section not wired"
    tmp = tempfile.mkdtemp(prefix="aria_home_")
    rdir = os.path.join(tmp, "ARIA", "routines")
    os.makedirs(rdir)
    with open(os.path.join(rdir, "morning.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "morning", "created": "2026-10-06T00:00:00",
                   "steps": [{"tool": "a", "args": {}}, {"tool": "b", "args": {}}],
                   "trusted": True}, f)
    with open(os.path.join(rdir, "junk.txt"), "w", encoding="utf-8") as f:
        f.write("not a routine")
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = tmp
    try:
        ns = {"os": os, "json": json}
        exec(compile(fn_src, "<test>", "exec"), ns)
        rows = ns["_routine_rows"]()
        assert rows == [{"name": "morning", "steps": 2, "trusted": True}], rows
    finally:
        if old_home is None:
            del os.environ["HOME"]
        else:
            os.environ["HOME"] = old_home
    # missing dir -> empty (no routines yet)
    os.environ["HOME"] = tempfile.mkdtemp(prefix="aria_empty_")
    try:
        ns2 = {"os": os, "json": json}
        exec(compile(fn_src, "<test>", "exec"), ns2)
        assert ns2["_routine_rows"]() == []
    finally:
        if old_home is None:
            del os.environ["HOME"]
        else:
            os.environ["HOME"] = old_home
check("OPS: _routine_rows parses ~/ARIA/routines/*.json (name/steps/trusted)", t_ops_routine_rows)

print(f"\n{sum(1 for _, s, _ in results if s=='PASS')}/{len(results)} passed")
fails = [r for r in results if r[1] != "PASS"]
sys.exit(1 if fails else 0)
