"""Pass 36 #30 — cancel propagation (no late summary, no blocked-over-cancelled).

Pins:
  * #30 cancel propagation: a cancel arriving during grading is
    never answered with the completion summary; a Cancelled
    raised by the verifier propagates instead of parking; a
    TaskBlocked racing a cancel persists as cancelled, not
    blocked.
"""
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import types

import pytest

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

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_pass36_exec_"), "test.db")
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

# taskstore writes under config.WORKSPACE_DIR: scratch dir.
config.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-pass36-exec-ws-")

builtins = importlib.import_module("aria.tools.builtins")
dispatch = importlib.import_module("aria.tools.dispatch")
failures = importlib.import_module("aria.agent.failures")
planner = importlib.import_module("aria.agent.planner")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")
taskstore = importlib.import_module("aria.agent.taskstore")
runtime = importlib.import_module("aria.runtime")


class ScriptedTool:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, args):
        self.calls.append(dict(args or {}))
        if len(self.outcomes) > 1:
            return self.outcomes.pop(0)
        return self.outcomes[0]


def _register(name, tool):
    dispatch.register_tool(name, tool, replay_safe=True)
    return tool


def _plan_json(steps, goal="g"):
    return json.dumps({"goal": goal, "steps": steps})


def _step(tool, critical=False, params=None, desc="do it"):
    return {"step": 1, "tool": tool, "description": desc,
            "parameters": params or {}, "critical": critical}


def _executor(llm=None, **kw):
    kw.setdefault("summarizer", lambda goal, completed: "SUMMARY-OK")
    kw.setdefault("sleep_fn", lambda s: None)
    return taskexec.AgentExecutor(llm=llm, **kw)


def test_cancel_during_grading_speaks_nothing():
    _register("fake_ok30", ScriptedTool(["ok-out"]))
    flag = threading.Event()

    def _verifier(goal, completed, results):
        flag.set()  # the cancel lands while grading runs
        return None

    ex = _executor(
        llm=lambda p, s: _plan_json([_step("fake_ok30")]),
        verifier=_verifier)
    spoken = []
    out = ex.execute("g", speak=spoken.append, cancel_flag=flag,
                     verify=True)
    assert out == "Task cancelled.", out
    assert spoken == [], "a late grade must not speak completion"
    print("ok cancel_during_grading_speaks_nothing")


def test_cancelled_verifier_propagates():
    _register("fake_ok30b", ScriptedTool(["ok-out"]))

    def _verifier(goal, completed, results):
        raise runtime.Cancelled("cancelled mid-grade")

    ex = _executor(
        llm=lambda p, s: _plan_json([_step("fake_ok30b")]),
        verifier=_verifier)
    with pytest.raises(runtime.Cancelled):
        ex.execute("g", verify=True)
    print("ok cancelled_verifier_propagates")


class _ParkingExecutor:
    """Parks while its own cancel flag is already set — the
    queue must record 'cancelled', not 'blocked'."""

    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        cancel_flag.set()
        raise taskexec.TaskBlocked(
            "needs the user",
            {"plan": None, "completed": [], "results": {}})


def test_queue_blocked_racing_cancel_persists_cancelled():
    q = taskqueue.TaskQueue(executor_factory=lambda: _ParkingExecutor())
    q.start()
    try:
        tid = q.submit("park vs cancel")
        end = time.time() + 10
        st = None
        while time.time() < end:
            st = q.get_status(tid)
            if st and st["status"] in ("cancelled", "blocked"):
                break
            time.sleep(0.05)
        assert st["status"] == "cancelled", st
        assert taskstore.get(tid)["status"] == "cancelled", \
            "a blocked record would resurrect the task as resumable"
        assert taskstore.recover_interrupted() == 0
    finally:
        q.stop()
    print("ok queue_blocked_racing_cancel_persists_cancelled")


if __name__ == "__main__":
    test_cancel_during_grading_speaks_nothing()
    test_cancelled_verifier_propagates()
    test_queue_blocked_racing_cancel_persists_cancelled()
    print("ALL PASS36 #30 CHECKS PASSED")
