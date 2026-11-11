"""Pass 36 #26 — stable plan/step identity (resume never skips a replacement step).

Pins:
  * #26 stable step identity: every plan revision gets a plan_rev
    and every step a stable id; resume skips, results, receipts,
    and substitution key by the id, never the display number — so
    a replacement step from a replan (renumbered from 1) is never
    skipped as if the step it replaced had run. Legacy
    number-keyed checkpoints with recorded results park for
    review instead of skipping.
"""
import importlib
import json
import os
import sys
import tempfile
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


def test_plan_rev_and_step_ids_stable_and_unique():
    _register("fake_ok", ScriptedTool(["ok-output"]))
    steps = [_step("fake_ok"), _step("fake_ok")]
    plan1 = planner.create_plan(
        "g", llm=lambda p, s: _plan_json(steps))
    plan2 = planner.create_plan(
        "g", llm=lambda p, s: _plan_json(steps))
    assert plan1["plan_rev"] != plan2["plan_rev"], (plan1, plan2)
    ids = [s["id"] for s in plan1["steps"]]
    assert len(set(ids)) == 2 and all(ids), plan1
    # Display numbers are still rendering-only 1..n.
    assert [s["step"] for s in plan1["steps"]] == [1, 2]
    print("ok plan_rev_and_step_ids")


def test_resume_after_replan_runs_replacement_step():
    tool = _register("fake_ok26", ScriptedTool(["new-out"]))
    # Checkpoint after a replan: plan rev B renumbered from 1,
    # while results still hold rev A's step 1 ("old-out").
    plan_b = {"goal": "g", "plan_rev": "revB", "steps": [
        {"id": "B1", "step": 1, "tool": "fake_ok26",
         "description": "replacement one",
         "parameters": {"x": "{prev}"}, "critical": True},
        {"id": "B2", "step": 2, "tool": "fake_ok26",
         "description": "replacement two",
         "parameters": {}, "critical": True},
    ]}
    completed_a = [{"id": "A1", "step": 1, "tool": "fake_ok26",
                    "description": "old one", "parameters": {},
                    "critical": True}]
    resume = {"plan": plan_b, "completed": completed_a,
              "results": {"A1": "old-out"}}
    ex = _executor()
    out = ex.execute("g", resume=resume)
    assert out == "SUMMARY-OK", out
    # The replacement step 1 RAN — the old code's
    # skip_steps=set(results) would have skipped it as if the
    # old step 1 had done its work.
    assert len(tool.calls) == 2, tool.calls
    # ...and the old step's result still fed {prev}.
    assert tool.calls[0]["x"] == "old-out", tool.calls
    print("ok resume_after_replan_runs_replacement_step")


def test_legacy_checkpoint_parks_for_review():
    tool = _register("fake_ok26b", ScriptedTool(["ok-out"]))
    legacy_plan = {"goal": "g", "steps": [
        {"step": 1, "tool": "fake_ok26b", "description": "first",
         "parameters": {}, "critical": False},
    ]}
    ex = _executor()
    with pytest.raises(taskexec.TaskBlocked) as ei:
        ex.execute("g", resume={
            "plan": legacy_plan,
            "completed": [legacy_plan["steps"][0]],
            "results": {"1": "old-out"}})
    assert "stable step ids" in ei.value.reason, ei.value.reason
    assert tool.calls == [], "parked legacy work must not run"
    # The same legacy shape with NOTHING recorded is safe to
    # adopt: ids are stamped and the plan runs.
    out = ex.execute("g", resume={
        "plan": legacy_plan, "completed": [], "results": {}})
    assert out == "SUMMARY-OK", out
    assert len(tool.calls) == 1, tool.calls
    print("ok legacy_checkpoint_parks_for_review")


if __name__ == "__main__":
    test_plan_rev_and_step_ids_stable_and_unique()
    test_resume_after_replan_runs_replacement_step()
    test_legacy_checkpoint_parks_for_review()
    print("ALL PASS36 #26 CHECKS PASSED")
