"""Pass 28 #12: task lookup must consult the durable journal
after a restart.

Before: TaskQueue.get_status read only the in-memory queue. After
a restart the queue is empty, so every journaled task read as
"no record" — a mission child that had completed (or failed, with
its result/error on disk) settled as "uncertain / may have been
lost", even though the journal held the outcome.

Fix contract pinned here: when the task is not in memory,
get_status falls back to the journal record (status, goal,
result, error). The journal file itself is project-scoped
(workspaces.path), so the fallback cannot leak across projects;
a live in-memory task still wins over the journal.
"""
import importlib
import os
import sys
import tempfile
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

_cfg = importlib.import_module("aria.config")
_memory = importlib.import_module("aria.memory")
_memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_taskqueue_journal_fb_"), "test.db")
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
_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskqueue-journal-fb-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")


class _CompletingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        return "child did the thing"


def _wait_status(q, tid, want, timeout=10):
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = q.get_status(tid)
        if st and st["status"] in want:
            return st
        time.sleep(0.05)
    return st


def test_get_status_falls_back_to_journal_after_restart():
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    tid = q.submit("journal fallback proof")
    q.start()
    st = _wait_status(q, tid, {"completed"})
    assert st and st["status"] == "completed", st
    q.stop()
    # Simulate a restart: a fresh queue with empty in-memory
    # state, reading the same journal.
    q2 = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    assert q2._tasks == {}
    st = q2.get_status(tid)
    assert st is not None, "journaled task read as 'no record'"
    assert st["status"] == "completed", st
    assert st["result"] == "child did the thing", st
    assert st["goal"] == "journal fallback proof", st


def test_get_status_returns_none_for_unknown_task():
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    assert q.get_status("no-such-task") is None


def test_get_status_prefers_live_task_over_journal():
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    tid = q.submit("live wins")
    # The journal still says pending; the live task is running.
    q._tasks[tid].status = taskqueue.TaskStatus.RUNNING
    st = q.get_status(tid)
    assert st is not None and st["status"] == "running", st
