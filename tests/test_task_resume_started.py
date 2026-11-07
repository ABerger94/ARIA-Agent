"""Pass 28 #19: task_resume must report a submission as STARTED,
not DONE.

Before: _tool_task_resume returned a plain string ("Resumed task
X as new task Y — N completed step(s) will be skipped."), which
normalize() reads as DONE — so the model (and any status reader,
including a mission step) certified the resumed task complete
when it had only been QUEUED. The error paths had the same shape
(a missing record / an unresumable task reported as DONE).

Fix contract pinned here:
- a successful resume returns a typed STARTED outcome carrying
  the child handle (new task id + project), mirroring
  delegate_task (pass 27 #25), with the skipped-step count as a
  fact — the resumed task is under way, not finished;
- a missing record / a refused resume returns a typed FAILED
  outcome instead of a plain string.
"""
import importlib
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

import pytest

_cfg = importlib.import_module("aria.config")
_memory = importlib.import_module("aria.memory")
_memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_task_resume_"), "test.db")
_memory.init_databases()
_memory._embed = lambda text: None

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
_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-task-resume-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")
dispatch = importlib.import_module("aria.tools.dispatch")
outcome = importlib.import_module("aria.outcome")


class _CompletingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        return "child did the thing"


def _blocked_with_checkpoint(task_id="old1"):
    taskstore.create(task_id, "do the thing")
    taskstore.checkpoint(task_id, {
        "plan": ["s1", "s2"], "completed": ["s1"],
        "results": {"s1": "did s1"}})
    taskstore.finish(task_id, "blocked", blocked_reason="paused")


@pytest.fixture()
def _queue(monkeypatch):
    # _tool_task_resume submits through the queue singleton —
    # point it at a fresh, unstarted queue so no worker thread
    # runs the resumed task with the real executor.
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    monkeypatch.setattr(taskqueue, "get_queue", lambda: q)
    return q


def test_task_resume_reports_started_not_done(_queue):
    _blocked_with_checkpoint()
    out = dispatch._tool_task_resume({"task_id": "old1"})
    assert outcome.normalize(out).status == "started", out
    assert "UNDER WAY, not finished" in out, out
    # The child handle rides along: the new task id + project.
    new_id = out.split("task_id=")[1].split()[0]
    assert "resumed_from=old1" in out, out
    assert "skipped=1" in out, out
    rec = taskstore.get(new_id)
    assert rec is not None and rec["status"] == "pending", rec
    assert rec["completed"] == ["s1"], rec
    assert rec["resumed_from"] == "old1", rec


def test_task_resume_unknown_task_is_failed_not_done():
    out = dispatch._tool_task_resume({"task_id": "ghost"})
    assert outcome.normalize(out).status == "failed", out


def test_task_resume_unresumable_task_is_failed_not_done():
    taskstore.create("done1", "finished business")
    taskstore.finish("done1", "completed", result="ok")
    out = dispatch._tool_task_resume({"task_id": "done1"})
    assert outcome.normalize(out).status == "failed", out
