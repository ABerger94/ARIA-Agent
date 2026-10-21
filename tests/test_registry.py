"""Tool-registry self-test + audit fixes (2026-10-21 fix package).

Walks the ENTIRE dispatch registry and calls every tool with
minimal valid arguments derived from its schema declaration,
externals stubbed (no network, no COM, temp dirs/DBs), asserting
each call returns a real result string through execute_tool —
never raises out of dispatch, never returns None/empty, never
reports a schema argument error when the schema's own properties
were supplied, and (with one documented exception) never lands in
the [Tool Error] repair flow.

What the first walk caught, all fixed in this package:
  * inbox_list / inbox_describe were registered but had NO
    function declarations and no toolkit — the model could never
    call them, though the brain's system prompt directs her to
    (phone-bridge uploads). inbox_read and the routine-pack pair
    were declared but in no toolkit, so never offered either.
  * builtins.tool_system_health_audit / tool_self_heal_diagnose
    delegated via `aria.agent.<fn>` package attributes, which only
    exist after aria/agent/__init__ runs — AttributeError in any
    import order that reaches builtins first. They now import
    from their defining submodules.
  * pricecheck.check_price_watches failed with "no such table"
    on a fresh install (the table is created lazily by
    watch_price); it now ensures the table, pinned below.
  * dispatch._repair_or_exhaust discarded the Phase-1
    surface_failure record, so its user_message — the plain
    line composed FOR the user — never reached the brain loop;
    the exhausted-budget result now carries it verbatim, pinned
    below.

Documented exception: run_python_code. The sandbox returns the
executed code's own traceback as its payload (that IS the result
for arbitrary user code), and dispatch's generic output heuristic
wraps any traceback-bearing output in the repair flow. The call
still returns a proper string; unwrapping that heuristic is a
separate design question, not an audit fix.

delegate_task is driven against a fake task queue (the tool's own
wiring is what's under test; the real queue + executor are
covered by tests/test_tasks.py) — submitting a real background
task from a self-test would plan against the live pool.
"""
import importlib
import os
import sys
import tempfile
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")
TMP = tempfile.mkdtemp(prefix="aria_registry_")


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
_ps.virtual_memory = lambda: types.SimpleNamespace(
    percent=0.0, available=0, total=0, used=0, free=0)
_ps.disk_usage = lambda p: types.SimpleNamespace(
    percent=0.0, total=0, used=0, free=0)
_ps.disk_partitions = lambda: []
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)


class _FakeCap:
    """A camera that opens but never has a frame — the shape a
    real absent/busy camera presents to capture_webcam."""

    def read(self):
        return False, None

    def release(self):
        pass

    def isOpened(self):
        return False


_cv2 = types.ModuleType("cv2")
_cv2.VideoCapture = lambda *a, **k: _FakeCap()
_cv2.IMWRITE_JPEG_QUALITY = 1
_cv2.resize = lambda img, size: img
_cv2.imencode = lambda ext, img, params=None: (False, None)
_cv2.imwrite = lambda path, img: True
sys.modules["cv2"] = _cv2

_speech = types.ModuleType("aria.speech")
_speech.speak = lambda t: None
_speech.edge_tts_bytes = lambda text, whisper_mode=False: b""
_speech.tts_bytes_for_bridge = lambda text: (b"", "no speech in tests")
_speech.transcribe_audio = lambda audio, mime: ""
_speech._SPEECH_STOP = types.SimpleNamespace(is_set=lambda: False)
_speech._SPEECH_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
_speech._AUDIO_PLAY_QUEUE = types.SimpleNamespace(unfinished_tasks=0)
sys.modules["aria.speech"] = _speech


class _DDGS:
    def text(self, *a, **k):
        raise RuntimeError("search stubbed in registry test")


for _mod_name in ("ddgs", "duckduckgo_search"):
    _m = types.ModuleType(_mod_name)
    _m.DDGS = _DDGS
    sys.modules[_mod_name] = _m

config = importlib.import_module("aria.config")
# Hermetic paths BEFORE any module value-copies them from config.
os.makedirs(os.path.join(TMP, "workspace"), exist_ok=True)
config.WORKSPACE_DIR = os.path.join(TMP, "workspace")
config.PRICE_WATCH_DB = os.path.join(TMP, "workspace",
                                      "price_watches.db")
config.KEYS_FILE = os.path.join(TMP, "aria_keys.json")

memory = importlib.import_module("aria.memory")
memory.DB_PATH = os.path.join(TMP, "test.db")
memory.init_databases()
memory._embed = lambda text: None


def _no_net(*a, **k):
    raise urllib.error.URLError("network stubbed in registry test")


urllib.request.urlopen = _no_net
import imaplib
import smtplib
smtplib.SMTP = smtplib.SMTP_SSL = _no_net
imaplib.IMAP4_SSL = _no_net

import aria.comclient as _comclient
_comclient.get_client = lambda: (_ for _ in ()).throw(
    _comclient.ComHostDown("stubbed in registry test"))

taskqueue = importlib.import_module("aria.agent.taskqueue")


class _FakeQueue:
    def submit(self, goal, priority=None, speak=None):
        return "fake-task-id"

    def get_status(self, tid):
        return None

    def cancel(self, tid):
        return False


taskqueue.get_queue = lambda: _FakeQueue()

dispatch = importlib.import_module("aria.tools.dispatch")
schemas = importlib.import_module("aria.tools.schemas")
pricecheck = importlib.import_module("aria.pricecheck")
dispatch.set_spine_hook(lambda *a, **k: None)
dispatch.set_log_hook(lambda *a, **k: None)

_SAMPLE = {"STRING": "test", "INTEGER": 1, "NUMBER": 1.0,
           "BOOLEAN": True}
# Tools whose designed result may legitimately take the [Tool
# Error] shape with schema-valid minimal args — see the module
# docstring. Everything else must come back clean.
_TOOL_ERROR_ALLOWED = {"run_python_code"}
# Registered tools with no static declaration BY DESIGN:
# load_toolkit is appended to every declaration payload by
# get_toolkit_declarations (asserted separately below). Dynamic
# user tools register their declarations at boot, so they appear
# in get_tool_decls_by_name and need no entry here.
_UNDECLARED_BY_DESIGN = {"load_toolkit"}


def _args_for(name, decls):
    if name == "load_toolkit":
        return {"toolkit": "core"}
    decl = decls.get(name) or {}
    props = (decl.get("parameters") or {}).get("properties", {})
    return {p: _SAMPLE.get((spec or {}).get("type", "STRING"), "test")
            for p, spec in props.items()}


def test_a_registry_declaration_parity():
    tools = dispatch.get_registered_tools()
    decls = schemas.get_tool_decls_by_name()
    undeclared = set(tools) - set(decls) - _UNDECLARED_BY_DESIGN
    assert not undeclared, f"registered but undeclared: {undeclared}"
    unregistered = set(decls) - set(tools)
    assert not unregistered, \
        f"declared but unregistered: {unregistered}"
    # Every declared tool must sit in a toolkit, or providers never
    # offer it (get_toolkit_declarations emits toolkit members only).
    in_tk = set()
    for tk in schemas.TOOLKITS.values():
        in_tk.update(tk["tools"])
    homeless = set(decls) - in_tk
    assert not homeless, f"declared but in no toolkit: {homeless}"
    # load_toolkit really is in the emitted payload.
    payload = schemas.get_toolkit_declarations({"core"})
    names = [d["name"] for chunk in payload
             for d in chunk["function_declarations"]]
    assert "load_toolkit" in names
    print("ok registry_declaration_parity")


def test_b_every_tool_answers():
    tools = dispatch.get_registered_tools()
    decls = schemas.get_tool_decls_by_name()
    failures = []
    for name in sorted(tools):
        dispatch.reset_turn_state("registry-test")
        try:
            res, _flag = dispatch.execute_tool(
                name, _args_for(name, decls), preauthorized=True)
        except Exception as e:  # execute_tool must never raise
            failures.append(f"{name}: RAISED {type(e).__name__}: {e}")
            continue
        if not isinstance(res, str) or res in ("", "None"):
            failures.append(f"{name}: bad result {res!r}")
        elif res.startswith("[Argument error"):
            failures.append(
                f"{name}: schema args rejected: {res[:120]}")
        elif res.startswith("[Tool Error") \
                and name not in _TOOL_ERROR_ALLOWED:
            failures.append(f"{name}: tool error: {res[:160]}")
    assert not failures, "\n".join(failures)
    print(f"ok every_tool_answers ({len(tools)} tools)")


def test_c_exhausted_repair_carries_user_message():
    # The Phase-1 record's user_message must survive into the text
    # the brain loop gets when the repair budget runs out — it is
    # the only route that message has to the user's ears.
    dispatch.reset_turn_state("registry-test")
    out = ""
    for _ in range(dispatch._REPAIR_BUDGET + 1):
        out = dispatch._repair_or_exhaust(
            "some_tool", {}, "RuntimeError: boom")
    assert "report the failure to the user:" in out.lower(), out
    assert "That failed and cannot continue, so I stopped it." in out, out
    print("ok exhausted_repair_carries_user_message")


def test_d_pricecheck_fresh_install_reports_no_watches():
    # Point pricecheck at a brand-new DB path (no file, no table —
    # the table is created lazily by watch_price). Done locally
    # because the registry walk above legitimately creates the
    # shared temp DB by calling the price tools.
    fresh = os.path.join(TMP, "fresh_price_watches.db")
    assert not os.path.exists(fresh)
    real = pricecheck.PRICE_WATCH_DB
    pricecheck.PRICE_WATCH_DB = fresh
    try:
        out = pricecheck.tool_check_price_watches()
    finally:
        pricecheck.PRICE_WATCH_DB = real
    assert out == "[No price watches to check]", out
    print("ok pricecheck_fresh_install_reports_no_watches")
