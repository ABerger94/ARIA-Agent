"""Pass 28 #14: a queued resume must persist its checkpoint.

Before: submit(resume_from=...) built the resume state from the
old record into the in-memory Task only — the NEW pending journal
record was created with plan=None, completed=[], results={}. A
restart before the worker started the resumed task recovered a
record with no checkpoint, so the next resume repeated the
completed steps (or re-ran side effects).

Fix contract pinned here: the resumed checkpoint (plan +
completed steps + their results) is written into the new record
in the same atomic create as the pending record itself, before
the enqueue and before submit returns the id.
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

_cfg = importlib.import_module("aria.config")
_memory = importlib.import_module("aria.memory")
_memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_taskstore_resume_ckpt_"), "test.db")
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
_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskstore-resume-ckpt-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")


class _CompletingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        return "child did the thing"


def test_resume_submit_persists_checkpoint_before_worker_starts():
    # A blocked task with a checkpoint: step s1 done, s2 pending.
    taskstore.create("old1", "do the thing")
    taskstore.checkpoint("old1", {
        "plan": ["s1", "s2"], "completed": ["s1"],
        "results": {"s1": "did s1"}})
    taskstore.finish("old1", "blocked", blocked_reason="paused")
    # Queue NOT started: no worker can pick the resumed task up,
    # so any checkpoint present comes from submit() itself.
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    tid = q.submit("do the thing", resume_from="old1")
    rec = taskstore.get(tid)
    assert rec is not None and rec["status"] == "pending", rec
    assert rec["plan"] == ["s1", "s2"], rec
    assert rec["completed"] == ["s1"], rec
    assert rec["results"] == {"s1": "did s1"}, rec
    assert rec["resumed_from"] == "old1", rec


def test_fresh_submit_still_creates_empty_checkpoint():
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    tid = q.submit("fresh work")
    rec = taskstore.get(tid)
    assert rec is not None and rec["status"] == "pending", rec
    assert rec["plan"] is None, rec
    assert rec["completed"] == [], rec
    assert rec["results"] == {}, rec
