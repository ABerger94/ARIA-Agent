"""Pass 28 #20: a delegated step that starts a child (delegate_task
/ task_resume) must have a settlement path — durable child waiting.

Before: the step returned STARTED, the executor raised TaskBlocked,
and the parent parked with the step uncheckpointed. On resume the
step re-ran and submitted ANOTHER child, then parked again. Every
resume spawned a new child; no child's outcome was ever consulted.

Fix contract pinned here:
* a child-spawning step that returns STARTED parks with a
  `pending_child` record {step, task_id, project} in the checkpoint
  state — the child id is durable, not lost in a block reason;
* on resume, the executor settles the pending child from the
  durable journal BEFORE re-running the step: completed -> the
  child's result becomes the step result; failed/cancelled/missing
  -> step failure (replan path); blocked/still running -> park
  again with the same pending_child, WITHOUT re-submitting.
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
config.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskexec-nested-ws-")
memory = importlib.import_module("aria.memory")
memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_taskexec_nested_"), "test.db")
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

planner = importlib.import_module("aria.agent.planner")
taskexec = importlib.import_module("aria.agent.taskexec")
taskstore = importlib.import_module("aria.agent.taskstore")
outcome = importlib.import_module("aria.outcome")
import pytest


def _plan_json(steps, goal="parent goal"):
    return json.dumps({"goal": goal, "steps": steps})


def _delegate_step():
    return {"step": 1, "tool": "delegate_task",
            "description": "hand the child goal off",
            "parameters": {"goal": "child goal"}, "critical": True}


class _Runner:
    """Counts delegate_task submissions; answers STARTED like the
    real tool (via the real outcome.started renderer). Other tools
    answer a plain DONE string, as a completed tool would."""

    def __init__(self):
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, dict(args or {})))
        if name not in ("delegate_task", "task_resume"):
            return "tool-result-ok"
        return outcome.started(
            name, task_id="child1", project="default",
            goal=(args or {}).get("goal", ""))


def _executor(runner, llm=None):
    return taskexec.AgentExecutor(
        llm=llm or (lambda p, s: _plan_json([_delegate_step()])),
        tool_runner=runner,
        summarizer=lambda goal, completed: "PARENT-SUMMARY",
        sleep_fn=lambda s: None)


def _seed_child(status, result="", error=""):
    taskstore.create("child1", "child goal", status="running")
    taskstore.finish("child1", status, result=result, error=error)


def _pending_state():
    return {"goal": "parent goal",
            "plan": {"goal": "parent goal", "steps": [_delegate_step()]},
            "completed": [], "results": {},
            "pending_child": {"step": 1, "task_id": "child1",
                              "project": "default"}}


def test_nested_delegation_parks_with_pending_child():
    runner = _Runner()
    ex = _executor(runner)
    with pytest.raises(taskexec.TaskBlocked) as exc:
        ex.execute("parent goal")
    assert len(runner.calls) == 1, runner.calls
    state = exc.value.state
    assert state["pending_child"] == {
        "step": 1, "task_id": "child1", "project": "default"}, state
    assert state["completed"] == [], state


def test_resume_settles_completed_child_without_respawning():
    _seed_child("completed", result="CHILD-DONE")
    runner = _Runner()
    ex = _executor(runner)
    out = ex.execute("parent goal", resume=_pending_state())
    assert len(runner.calls) == 0, runner.calls
    assert outcome.normalize(out).status == "done", out
    assert "PARENT-SUMMARY" in out


def test_resume_waits_while_child_running():
    _seed_child("running")
    runner = _Runner()
    ex = _executor(runner)
    with pytest.raises(taskexec.TaskBlocked) as exc:
        ex.execute("parent goal", resume=_pending_state())
    assert len(runner.calls) == 0, runner.calls
    assert exc.value.state["pending_child"]["task_id"] == "child1"


def test_resume_replans_when_child_failed():
    _seed_child("failed", error="child blew up")
    runner = _Runner()
    # Replan answers with an empty plan, so the planner falls back
    # to a single web_search step: the point is the failed child
    # feeds the replan path (the fallback step runs, the task
    # completes), not a re-submission of the child.
    ex = _executor(runner, llm=lambda p, s: _plan_json([]))
    out = ex.execute("parent goal", resume=_pending_state())
    assert [c for c in runner.calls if c[0] == "delegate_task"] == [], \
        runner.calls
    assert outcome.normalize(out).status == "done", out


def test_resume_replans_when_child_record_missing():
    runner = _Runner()
    ex = _executor(runner, llm=lambda p, s: _plan_json([]))
    out = ex.execute("parent goal", resume=_pending_state())
    assert [c for c in runner.calls if c[0] == "delegate_task"] == [], \
        runner.calls
    assert outcome.normalize(out).status == "done", out
