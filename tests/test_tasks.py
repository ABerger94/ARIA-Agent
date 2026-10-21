"""Unit tests for Merger Phase 3 — aria/agent/taskqueue.py,
planner.py, taskexec.py + the delegate_task/task_status/task_cancel
tool wiring (blueprint SS2.1, SS3.3).

Everything is local and deterministic: the planner's LLM is a stub,
step tools are scripted fakes registered in the REAL dispatch
registry (so steps run through the real execute_tool), the queue's
executor is injected, and the memory DB is a temp file. Verifies
the caps on the record: plan clamped to 5, step attempts capped at
3, replans capped at 2, unknown tool = hard abort, 600 s budget,
queue concurrency 1, priority order, cancel.

Run:  python3 tests/_run_suite.py tests/test_tasks.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [PKG + "/agent"]
sys.modules["aria.agent"] = _agent_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

import importlib

config = importlib.import_module("aria.config")
schemas = importlib.import_module("aria.tools.schemas")
sandbox = importlib.import_module("aria.tools.sandbox")
memory = importlib.import_module("aria.memory")
memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_tasks_"), "test.db")
memory.init_databases()
memory._embed = lambda text: None

for _name in ("aria.scheduler", "aria.vision", "aria.hardware",
              "aria.spotify", "aria.hud"):
    sys.modules[_name] = types.ModuleType(_name)
sys.modules["aria.vision"].get_face_frame_jpeg = lambda: None
sys.modules["aria.vision"].publish_phone_frame = lambda jpeg: True
sys.modules["aria.vision"].get_phone_frame_jpeg = lambda: None
sys.modules["aria.vision"].get_phone_frame_status = lambda: {"active": False, "age_s": -1.0}
sys.modules["aria.vision"].describe_phone_view = lambda q="": "[stubbed]"
sys.modules["aria.hud"].tool_show_commands = lambda: "ok"
sys.modules["aria.hud"].tool_hide_commands = lambda: "ok"

builtins = importlib.import_module("aria.tools.builtins")
dispatch = importlib.import_module("aria.tools.dispatch")

_speech = types.ModuleType("aria.speech")
_SPOKEN = []
_speech.speak = lambda text: _SPOKEN.append(text)
sys.modules["aria.speech"] = _speech

failures = importlib.import_module("aria.agent.failures")
planner = importlib.import_module("aria.agent.planner")
taskqueue = importlib.import_module("aria.agent.taskqueue")
taskexec = importlib.import_module("aria.agent.taskexec")


# ---------------------------------------------------------------- helpers
class ScriptedTool:
    """A dispatch-registry tool with scripted outcomes (strings are
    returned; the last outcome repeats)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, args):
        self.calls.append(dict(args or {}))
        if len(self.outcomes) > 1:
            return self.outcomes.pop(0)
        return self.outcomes[0]


def _register(name, tool):
    dispatch.register_tool(name, tool)
    return tool


def _executor(llm=None, **kw):
    kw.setdefault("summarizer", lambda goal, completed: "SUMMARY-OK")
    kw.setdefault("sleep_fn", lambda s: None)
    return taskexec.AgentExecutor(llm=llm, **kw)


def _plan_json(steps, goal="g"):
    return json.dumps({"goal": goal, "steps": steps})


def _step(tool, critical=False, params=None, desc="do it"):
    return {"step": 1, "tool": tool, "description": desc,
            "parameters": params or {}, "critical": critical}


# ---------------------------------------------------------------- planner
def test_planner_clamps_to_five_steps():
    _register("fake_ok", ScriptedTool(["ok-output"]))
    steps = [_step("fake_ok") for _ in range(7)]
    plan = planner.create_plan("g", llm=lambda p, s: _plan_json(steps))
    assert len(plan["steps"]) == 5, plan
    assert any("clamped" in w for w in plan["warnings"]), plan["warnings"]
    assert [s["step"] for s in plan["steps"]] == [1, 2, 3, 4, 5]
    print("ok planner_clamps_to_five_steps")


def test_planner_drops_unknown_tools():
    _register("fake_ok", ScriptedTool(["ok-output"]))
    steps = [_step("fake_ok"), _step("nope_tool_xyz")]
    plan = planner.create_plan("g", llm=lambda p, s: _plan_json(steps))
    assert [s["tool"] for s in plan["steps"]] == ["fake_ok"], plan
    assert any("unknown tool" in w for w in plan["warnings"])
    print("ok planner_drops_unknown_tools")


def test_planner_fallbacks():
    bad = planner.create_plan("g", llm=lambda p, s: "not json at all")
    assert len(bad["steps"]) == 1
    assert bad["steps"][0]["tool"] == "web_search"
    assert bad["steps"][0]["critical"] is True

    def _boom(p, s):
        raise RuntimeError("pool down")
    down = planner.create_plan("g", llm=_boom)
    assert down["steps"][0]["tool"] == "web_search"
    print("ok planner_fallbacks")


# ---------------------------------------------------------------- executor
def test_executor_happy_path_and_substitution():
    tool = _register("fake_ok", ScriptedTool(["ok-output"]))
    steps = [_step("fake_ok", params={"x": "first"}),
             _step("fake_ok", params={"x": "{prev}"})]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    spoken = []
    out = ex.execute("goal", speak=spoken.append)
    assert out == "SUMMARY-OK", out
    assert spoken == ["SUMMARY-OK"], spoken
    assert tool.calls[0]["x"] == "first"
    assert tool.calls[1]["x"] == "ok-output", tool.calls
    print("ok executor_happy_path_and_substitution")


def test_executor_retries_transient_then_succeeds():
    tool = _register("fake_flaky", ScriptedTool([
        "[Tool Error: HTTP 503 service unavailable]",
        "[Tool Error: HTTP 503 service unavailable]",
        "recovered",
    ]))
    steps = [_step("fake_flaky", critical=True)]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    out = ex.execute("goal")
    assert out == "SUMMARY-OK", out
    assert len(tool.calls) == 3, tool.calls
    print("ok executor_retries_transient_then_succeeds")


def test_executor_attempt_cap_stops_at_three():
    tool = _register("fake_always503", ScriptedTool(
        ["[Tool Error: HTTP 503 service unavailable]"]))
    steps = [_step("fake_always503", critical=True)]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    out = ex.execute("goal")
    # Attempts are capped at STEP_MAX_ATTEMPTS per plan incarnation;
    # a critical transient failure escalates retry -> replan, and
    # replans are capped at MAX_REPLAN_ATTEMPTS, so the tool sees
    # exactly 3 attempts x (1 + 2 replans) calls before the abort.
    cap = config.STEP_MAX_ATTEMPTS * (1 + config.MAX_REPLAN_ATTEMPTS)
    assert len(tool.calls) == cap, (len(tool.calls), cap)
    assert "failed despite new approaches" in out, out
    print("ok executor_attempt_cap_stops_at_three")


def test_executor_noncritical_skip_continues():
    _register("fake_broken_nc", ScriptedTool(
        ["[Tool Error: FileNotFoundError: No such file or directory: 'x']"]))
    _register("fake_ok2", ScriptedTool(["done"]))
    steps = [_step("fake_broken_nc", critical=False),
             _step("fake_ok2")]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    out = ex.execute("goal")
    assert out == "SUMMARY-OK", out
    print("ok executor_noncritical_skip_continues")


def test_executor_critical_replan_cap_then_abort():
    _register("fake_broken_c", ScriptedTool(
        ["[Tool Error: FileNotFoundError: No such file or directory: 'x']"]))
    plan = {"goal": "g", "steps": [_step("fake_broken_c", critical=True)],
            "warnings": []}
    replan_calls = [0]
    orig_create, orig_replan = planner.create_plan, planner.replan
    try:
        planner.create_plan = lambda goal, context="", llm=None: plan
        def _replan(goal, completed, failed, error, llm=None):
            replan_calls[0] += 1
            return plan
        planner.replan = _replan
        ex = _executor()
        spoken = []
        out = ex.execute("goal", speak=spoken.append)
    finally:
        planner.create_plan, planner.replan = orig_create, orig_replan
    assert replan_calls[0] == config.MAX_REPLAN_ATTEMPTS, replan_calls
    assert "failed despite new approaches" in out, out
    assert spoken and spoken[-1] == out
    # The abort is on the record in the incident store.
    rows = [r for r in memory.incident_db_list(limit=100)
            if r["source"].startswith("taskexec")]
    assert rows, "expected taskexec incident rows"
    print("ok executor_critical_replan_cap_then_abort")


def test_executor_unknown_tool_hard_abort():
    plan = {"goal": "g", "steps": [_step("ghost_tool_xyz")], "warnings": []}
    orig_create = planner.create_plan
    try:
        planner.create_plan = lambda goal, context="", llm=None: plan
        ex = _executor()
        out = ex.execute("goal")
    finally:
        planner.create_plan = orig_create
    assert "don't have" in out, out
    rows = [r for r in memory.incident_db_list(limit=100)
            if r["source"] == "taskexec.step:ghost_tool_xyz"]
    assert rows and rows[0]["action_taken"] == "abort", rows
    print("ok executor_unknown_tool_hard_abort")


def test_executor_budget_abort():
    tool = _register("fake_ok3", ScriptedTool(["ok"]))
    steps = [_step("fake_ok3")]
    saved = config.DELEGATED_TASK_BUDGET_S
    try:
        config.DELEGATED_TASK_BUDGET_S = -1  # already expired
        ex = _executor(llm=lambda p, s: _plan_json(steps))
        out = ex.execute("goal")
    finally:
        config.DELEGATED_TASK_BUDGET_S = saved
    assert "ran too long" in out, out
    assert tool.calls == [], "budget abort must precede any step"
    print("ok executor_budget_abort")


def test_executor_approval_gate_aborts():
    _register("fake_gated", ScriptedTool(
        ["[AWAITING_APPROVAL token=abc123] run_python_code"]))
    steps = [_step("fake_gated", critical=True)]
    ex = _executor(llm=lambda p, s: _plan_json(steps))
    out = ex.execute("goal")
    assert "needs your approval" in out, out
    print("ok executor_approval_gate_aborts")


# ---------------------------------------------------------------- queue
class _FakeExecutor:
    def __init__(self):
        self.executed = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()
        self.blocker = None  # threading.Event to hold a task open

    def execute(self, goal, speak=None, cancel_flag=None):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.blocker is not None:
                self.blocker.wait(timeout=10)
            self.executed.append(goal)
            return f"done: {goal}"
        finally:
            with self._lock:
                self.active -= 1


def _wait_for(pred, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_queue_priority_order_and_concurrency():
    fake = _FakeExecutor()
    fake.blocker = threading.Event()
    q = taskqueue.TaskQueue(executor_factory=lambda: fake)
    q.start()
    try:
        q.submit("blocker", priority=taskqueue.TaskPriority.NORMAL)
        assert _wait_for(lambda: fake.active == 1)
        q.submit("low-job", priority=taskqueue.TaskPriority.LOW)
        q.submit("normal-job", priority=taskqueue.TaskPriority.NORMAL)
        q.submit("high-job", priority=taskqueue.TaskPriority.HIGH)
        fake.blocker.set()
        assert _wait_for(lambda: len(fake.executed) == 4)
        order = fake.executed[1:]
        assert order == ["high-job", "normal-job", "low-job"], order
        assert fake.max_active == 1, fake.max_active
    finally:
        q.stop()
    print("ok queue_priority_order_and_concurrency")


def test_queue_cancel_and_status():
    fake = _FakeExecutor()
    fake.blocker = threading.Event()
    q = taskqueue.TaskQueue(executor_factory=lambda: fake)
    q.start()
    try:
        q.submit("blocker")
        assert _wait_for(lambda: fake.active == 1)
        tid = q.submit("victim")
        assert q.cancel(tid) is True
        fake.blocker.set()
        assert _wait_for(
            lambda: q.get_status(tid)["status"] == "cancelled")
        assert "victim" not in fake.executed
        assert q.cancel("no-such-id") is False
        assert q.get_status("no-such-id") is None
        st = q.get_status(tid)
        assert st["budget_s"] == config.DELEGATED_TASK_BUDGET_S
    finally:
        q.stop()
    print("ok queue_cancel_and_status")


def test_queue_executor_end_to_end():
    """The full composition: real queue + real executor + stub LLM +
    a real dispatch-registered tool, multi-step, outcome recorded."""
    tool = _register("fake_e2e", ScriptedTool(["e2e-result"]))
    steps = [_step("fake_e2e", params={"x": "a"}),
             _step("fake_e2e", params={"x": "{prev}"})]
    ex = taskexec.AgentExecutor(
        llm=lambda p, s: _plan_json(steps),
        summarizer=lambda goal, completed: "E2E SUMMARY",
        sleep_fn=lambda s: None)
    q = taskqueue.TaskQueue(executor_factory=lambda: ex)
    q.start()
    try:
        tid = q.submit("end to end goal")
        assert _wait_for(
            lambda: (q.get_status(tid) or {}).get("status") == "completed")
        st = q.get_status(tid)
        assert st["result"] == "E2E SUMMARY", st
        assert tool.calls[1]["x"] == "e2e-result", tool.calls
    finally:
        q.stop()
    print("ok queue_executor_end_to_end")


# ---------------------------------------------------------------- tools
def test_delegate_tools_end_to_end():
    fake = _FakeExecutor()
    q = taskqueue.TaskQueue(executor_factory=lambda: fake)
    q.start()
    saved = taskqueue._queue
    taskqueue._queue = q
    try:
        out, _ = dispatch.execute_tool(
            "delegate_task", {"goal": "research the thing"})
        assert out.startswith("Delegated as task "), out
        tid = out.split(":")[0].replace("Delegated as task ", "").strip()
        assert _wait_for(
            lambda: (taskqueue.get_queue().get_status(tid) or {})
            .get("status") == "completed")
        st_out, _ = dispatch.execute_tool("task_status", {"task_id": tid})
        assert "completed" in st_out, st_out
        assert "done: research the thing" in st_out, st_out
        bad, _ = dispatch.execute_tool("task_status", {"task_id": "nope"})
        assert "No delegated task" in bad
        no_goal, _ = dispatch.execute_tool("delegate_task", {"goal": ""})
        # The dispatcher's own schema validation rejects it first.
        assert "requires goal" in no_goal, no_goal
    finally:
        taskqueue._queue = saved
        q.stop()
    print("ok delegate_tools_end_to_end")
