import os
"""Standalone headless tests for ARIA ULTIMATE Worker C modules (5+6+7).

Follows the tests/test_headless.py stub pattern exactly: bypasses
aria/__init__.py's eager imports, stubs the hardware-bound modules and
psutil BEFORE importing dispatch, and never calls real providers.

Covers spec test plan items 6 (events) + 7 (screenwatch comparator).
"""
import sys
import types
import traceback
import importlib

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
        traceback.print_exc()

# stub psutil
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules["psutil"] = _ps

# ---- stub hardware-bound modules (BEFORE importing dispatch) ----
for name in ("aria.scheduler", "aria.vision", "aria.hardware", "aria.spotify",
             "aria.hud", "aria.speech"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["aria.vision"].get_face_frame_jpeg = lambda: None
sys.modules["aria.vision"].publish_phone_frame = lambda jpeg: True
sys.modules["aria.vision"].get_phone_frame_jpeg = lambda: None
sys.modules["aria.vision"].get_phone_frame_status = lambda: {"active": False, "age_s": -1.0}
sys.modules["aria.vision"].describe_phone_view = lambda q="": "[stubbed]"
sys.modules["aria.vision"].tool_read_screen = lambda q="": "[vision stubbed]"
sys.modules["aria.hud"].tool_show_commands = lambda: "ok"
sys.modules["aria.hud"].tool_hide_commands = lambda: "ok"
# proactive.py imports BREAK_REMINDERS from aria.scheduler
sys.modules["aria.scheduler"].BREAK_REMINDERS = True

builtins_mod = importlib.import_module("aria.tools.builtins")
dispatch = importlib.import_module("aria.tools.dispatch")

events = importlib.import_module("aria.events")
screenwatch = importlib.import_module("aria.screenwatch")
proactive = importlib.import_module("aria.agent.proactive")
providers = importlib.import_module("aria.agent.providers")
triage = importlib.import_module("aria.tools.triage")

# fake scheduler backend for screenwatch tests (headless, no DB writes)
_FAKE_TASKS = {}
def _fake_sched_add(kind, prompt, delay_s=0, interval_s=0):
    tid = len(_FAKE_TASKS) + 1
    _FAKE_TASKS[tid] = {"kind": kind, "prompt": prompt, "interval_s": interval_s}
    return tid
sys.modules["aria.scheduler"].sched_add = _fake_sched_add
sys.modules["aria.scheduler"].sched_cancel = lambda tid: _FAKE_TASKS.pop(tid, None) is not None

# route screenwatch persistence at a temp file (not ~/ARIA)
import tempfile
_TMP = tempfile.mkdtemp(prefix="aria_sw_test_")
screenwatch.WATCHES_PATH = os.path.join(_TMP, "screen_watches.json")

# ============ Module 5: event bus ============
def t_emit_drain_roundtrip():
    events.drain()  # start clean
    events.emit("reminder_fired", {"task_id": 1, "prompt": "hello"})
    items = events.drain()
    assert len(items) == 1, f"expected 1 event, got {len(items)}"
    assert items[0]["type"] == "reminder_fired", items[0]
    assert items[0]["payload"]["prompt"] == "hello", items[0]
    assert events.drain() == [], "drain should leave the bus empty"
check("events: emit→drain round-trip", t_emit_drain_roundtrip)

def t_subscribe_handler_fires():
    events.drain()
    seen = []
    events.subscribe("task_fired", lambda payload: seen.append(payload))
    events.emit("task_fired", {"task_id": 42})
    items = events.drain()
    assert len(items) == 1, f"expected 1 event, got {len(items)}"
    assert seen and seen[0]["task_id"] == 42, f"handler did not fire: {seen}"
    assert len(items[0]["payload"]) == 1
check("events: subscribe handler fires on emit", t_subscribe_handler_fires)

def t_emit_never_raises():
    events.drain()
    events.subscribe("price_drop", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    events.emit("price_drop", {"label": "x"})  # failing subscriber must not raise
    assert events.drain(), "event must still be queued despite subscriber failure"
check("events: failing subscriber does not break emit/drain", t_emit_never_raises)

def t_process_event_queue_routes_through_proactive_say():
    events.drain()
    events.emit("reminder_fired", {"prompt": "take your meds"})
    spoken = []
    n = proactive.process_event_queue(spoken.append, lambda: False)
    assert n == 1, f"expected 1 spoken, got {n}"
    assert spoken == ["take your meds"], spoken
    assert events.drain() == [], "queue must be empty after processing"
check("proactive: process_event_queue speaks via proactive_say", t_process_event_queue_routes_through_proactive_say)

def t_process_event_queue_respects_busy():
    events.drain()
    events.emit("inbox_new_file", {"filename": "doc.pdf"})
    spoken = []
    n = proactive.process_event_queue(spoken.append, lambda: True)
    assert n == 0 and spoken == [], f"busy user must not be spoken to: {spoken}"
check("proactive: process_event_queue holds when busy", t_process_event_queue_respects_busy)

def t_poll_inbox_emits_on_new_file():
    events.drain()
    events._inbox_seen.clear()
    from aria.inbox import INBOX_DIR
    path = os.path.join(INBOX_DIR, "worker_c_test_probe.txt")
    try:
        with open(path, "w") as fh:
            fh.write("probe")
        new = events.poll_inbox()
        assert "worker_c_test_probe.txt" in new, new
        items = events.drain()
        assert any(i["type"] == "inbox_new_file" for i in items), items
        assert events.poll_inbox() == [], "second poll must be quiet"
    finally:
        if os.path.exists(path):
            os.remove(path)
    events.drain()
check("events: poll_inbox emits inbox_new_file for new files", t_poll_inbox_emits_on_new_file)

# ============ Module 7: screenwatch comparator ============
def t_answers_differ_identical():
    s = "The build finished successfully at 14:02."
    assert screenwatch.answers_differ(s, s) is False
check("screenwatch: identical answers -> False", t_answers_differ_identical)

def t_answers_differ_totally_different():
    assert screenwatch.answers_differ("Order status: shipped", "The weather is sunny and 72 degrees") is True
check("screenwatch: totally different answers -> True", t_answers_differ_totally_different)

def t_answers_differ_small_edit():
    old = ("Here is the current status of all monitored systems across the "
           "dashboard: every service is operational and the queue is empty.")
    new = ("Here is the current status of all monitored systems across the "
           "dashboard: every service is operational and the queue is nearly empty.")
    assert screenwatch.answers_differ(old, new) is False, "small edit must not count as a change"
check("screenwatch: small edit -> False", t_answers_differ_small_edit)

def t_answers_differ_empty():
    assert screenwatch.answers_differ("", "something") is True
    assert screenwatch.answers_differ("something", "") is True
    assert screenwatch.answers_differ("", "") is False
check("screenwatch: empty/blank edge cases", t_answers_differ_empty)

def t_watch_lifecycle():
    # sanitize + persist + fake scheduler task
    r = screenwatch.tool_watch_screen("My Watch!", "is the build done?", 300)
    assert "Watching" in r, r
    assert _FAKE_TASKS, "recurring scheduler task must be created"
    tid, task = next(iter(_FAKE_TASKS.items()))
    assert task["kind"] == "screenwatch" and task["prompt"] == "mywatch", task
    assert task["interval_s"] == 300, task
    lst = screenwatch.tool_list_screen_watches()
    assert "mywatch" in lst, lst
    # first check sets the baseline without emitting
    events.drain()
    sys.modules["aria.vision"].tool_read_screen = lambda q="": "Build is in progress."
    assert "no change" in screenwatch.check_watch("mywatch"), "first check sets baseline"
    assert events.drain() == [], "baseline check must not emit"
    # changed answer emits screen_watch_triggered
    sys.modules["aria.vision"].tool_read_screen = lambda q="": "DEPLOYMENT COMPLETE AND TOTALLY NEW"
    res = screenwatch.check_watch("mywatch")
    assert "change detected" in res, res
    items = events.drain()
    assert len(items) == 1 and items[0]["type"] == "screen_watch_triggered", items
    assert items[0]["payload"]["name"] == "mywatch", items[0]
    # unwatch removes watch + cancels task
    assert "Stopped" in screenwatch.tool_unwatch_screen("mywatch")
    assert "No active" in screenwatch.tool_list_screen_watches()
    assert tid not in _FAKE_TASKS, "scheduler task must be cancelled"
check("screenwatch: watch -> check(baseline/change) -> unwatch", t_watch_lifecycle)

def t_watch_validation():
    assert "Minimum" in screenwatch.tool_watch_screen("x", "q?", 30), "min interval 60s"
    assert "Invalid" in screenwatch.tool_watch_screen("!!!", "q?", 300)
    assert "question" in screenwatch.tool_watch_screen("ok", "   ", 300).lower()
check("screenwatch: name/interval/question validation", t_watch_validation)

# ============ Module 6: triage ============
def t_triage_gmail_unconfigured():
    orig = builtins_mod._gmail_creds
    builtins_mod._gmail_creds = lambda: ("", "")
    try:
        out = triage.tool_triage_email(limit=20)
    finally:
        builtins_mod._gmail_creds = orig
    assert out == triage.NOT_CONFIGURED_MSG, out
check("triage: Gmail unconfigured -> setup message", t_triage_gmail_unconfigured)

def t_triage_classifies_without_real_provider():
    orig_read = builtins_mod.tool_read_email
    orig_text = providers.provider_text
    builtins_mod.tool_read_email = lambda query="", limit=10, **kw: (
        "2 email(s), newest first:\n"
        "[uid=9] Mon, 5 Oct 2026 | bills@powerco.com | \"Your bill is due Oct 10\"\n"
        "[uid=8] Mon, 5 Oct 2026 | deals@shop.com | \"SALE: 50% off everything today!\""
    )
    providers.provider_text = lambda system, user_text: (
        "IMPORTANT (act now): powerco.com — electric bill due Oct 10\n"
        "NOISE (ignored): 1"
    )
    try:
        out = triage.tool_triage_email(limit=20)
    finally:
        builtins_mod.tool_read_email = orig_read
        providers.provider_text = orig_text
    assert "IMPORTANT (act now):" in out and "NOISE (ignored): 1" in out, out
check("triage: classification via monkeypatched provider_text", t_triage_classifies_without_real_provider)

def t_provider_text_helper_exists():
    assert callable(providers.provider_text)
    import inspect
    sig = inspect.signature(providers.provider_text)
    assert list(sig.parameters) == ["system_instruction", "user_text"], sig
check("providers: provider_text(system_instruction, user_text) exists", t_provider_text_helper_exists)

# ============ summary ============
passed = sum(1 for _, s, _ in results if s == "PASS")
failed = sum(1 for _, s, _ in results if s != "PASS")
print(f"\n{passed} passed, {failed} failed out of {len(results)}")
sys.exit(1 if failed else 0)
