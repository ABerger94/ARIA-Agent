"""Pass 28 #16: cancelling a task must persist the cancellation.

Before: TaskQueue.cancel() (and cancel_children_of_mission)
set only the in-memory flag + status. The journal record stayed
'pending', so after a restart recover_interrupted() flipped it to
'blocked' — a cancelled task resurrected as resumable.

Fix contract pinned here: cancelling a task also finishes its
journal record as 'cancelled', so a restart finds it terminal
and recovery leaves it alone.
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
    tempfile.mkdtemp(prefix="aria_taskqueue_cancel_"), "test.db")
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
_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskqueue-cancel-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")
runtime = importlib.import_module("aria.runtime")


class _CompletingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        return "child did the thing"


def test_cancel_queued_task_persists_cancelled():
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    tid = q.submit("cancel me")
    assert taskstore.get(tid)["status"] == "pending"
    assert q.cancel(tid) is True
    assert taskstore.get(tid)["status"] == "cancelled", \
        "journal still says pending — the task will resurrect"
    # A restart's recovery must leave it alone: it is terminal.
    assert taskstore.recover_interrupted() == 0
    assert taskstore.get(tid)["status"] == "cancelled"
    # And the journal fallback (pass 28 #12) reports it cancelled.
    q2 = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    assert q2.get_status(tid)["status"] == "cancelled"


def test_cancel_children_of_mission_persists():
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    with runtime.execution(
            "mission", execution_id="mission:default:7",
            budget_scope="mission:7", spend_cap=25.0,
            envelope={"mission_id": 7}):
        tid1 = q.submit("child one")
        tid2 = q.submit("child two")
    assert q.cancel_children_of_mission(7) == 2
    assert taskstore.get(tid1)["status"] == "cancelled"
    assert taskstore.get(tid2)["status"] == "cancelled"
    assert taskstore.recover_interrupted() == 0


def test_cancel_unknown_task_returns_false():
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    assert q.cancel("no-such-task") is False
