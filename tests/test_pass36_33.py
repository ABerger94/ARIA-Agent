"""Pass 36 #33 — typed planning failure (no search fallback for non-research goals).

Pins:
  * #33 typed planning failure: a non-research goal whose
    planning fails aborts with a clear reason and never sends
    the raw goal to a search backend; an explicitly
    research-shaped goal keeps a scoped search fallback.
"""
import importlib
import json
import os
import sys
import tempfile
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


def test_executor_aborts_on_planning_failure_without_searching():
    calls = []

    def runner(name, args):
        calls.append(name)
        return "tool-result"

    ex = taskexec.AgentExecutor(
        llm=lambda p, s: "not json at all",
        tool_runner=runner,
        summarizer=lambda goal, completed: "SUMMARY-OK",
        sleep_fn=lambda s: None)
    spoken = []
    out = ex.execute("do a private thing", speak=spoken.append)
    assert "couldn't make a plan" in out, out
    assert calls == [], "no tool — least of all a search — ran"
    assert spoken and "couldn't make a plan" in spoken[-1]
    print("ok executor_aborts_on_planning_failure")


def test_research_goal_keeps_scoped_search_fallback():
    calls = []

    def runner(name, args):
        calls.append((name, dict(args or {})))
        return "search-result"

    ex = taskexec.AgentExecutor(
        llm=lambda p, s: "not json at all",
        tool_runner=runner,
        summarizer=lambda goal, completed: "SUMMARY-OK",
        sleep_fn=lambda s: None)
    out = ex.execute("research the best laptop under $1000")
    assert out == "SUMMARY-OK", out
    assert calls == [("web_search",
                      {"query": "the best laptop under $1000"})], calls
    print("ok research_goal_keeps_scoped_search_fallback")


def test_replan_failure_aborts_without_searching():
    _register("fake_flaky33", ScriptedTool(
        ["[Tool Error: HTTP 503 service unavailable]"]))
    calls = []

    def _llm(prompt, system):
        calls.append("llm")
        if len(calls) == 1:
            return _plan_json([_step("fake_flaky33", critical=True)])
        raise RuntimeError("pool down")

    ex = _executor(llm=_llm)
    spoken = []
    out = ex.execute("do a private thing", speak=spoken.append)
    assert "couldn't find another approach" in out, out
    assert spoken and "couldn't find another approach" in spoken[-1]
    print("ok replan_failure_aborts_without_searching")


if __name__ == "__main__":
    test_executor_aborts_on_planning_failure_without_searching()
    test_research_goal_keeps_scoped_search_fallback()
    test_replan_failure_aborts_without_searching()
    print("ALL PASS36 #33 CHECKS PASSED")
