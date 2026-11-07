"""Pass 27 #26 — queued delegated tasks survive a restart.

Pins:
  * submit() persists a 'pending' journal record — goal, policy
    snapshot (spend cap, budget scope, envelope), parent identity
    — BEFORE the in-memory enqueue and before the id is returned;
    the record exists while no worker has touched the task;
  * a mission-context submit persists the submitting execution's
    policy in that pending record;
  * when the worker starts the task the pending record flips to
    'running' (not overwritten): submit-time created_at survives;
  * recover_interrupted() recovers a 'pending' record as blocked
    (visible + resumable), with a reason that says it never
    started — and still recovers 'running' records as before.
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
    tempfile.mkdtemp(prefix="aria_taskstore_pending_"), "test.db")
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
_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskstore-pending-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")
runtime = importlib.import_module("aria.runtime")


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


def test_submit_persists_pending_record_before_worker_starts():
    # Queue deliberately NOT started: no worker can pick the task
    # up, so any record present comes from submit() itself.
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    tid = q.submit("queued work, never started")
    rec = taskstore.get(tid)
    assert rec is not None, "submit() must persist a record before returning the id"
    assert rec["status"] == "pending", rec
    assert rec["goal"] == "queued work, never started"
    assert rec["execution_scope"] == f"task:{tid}"
    assert rec["spend_cap"] is None
    assert rec["budget_scope"] == ""
    assert rec["envelope"] is None
    assert rec["mission_id"] is None
    # And the in-memory task is still queued, not lost.
    assert q.get_status(tid)["status"] == "pending"


def test_submit_under_mission_persists_policy_snapshot():
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    with runtime.execution(
            "mission", execution_id="mission:default:7",
            budget_scope="mission:7", spend_cap=25.0,
            envelope={"mission_id": 7}):
        tid = q.submit("mission child")
    rec = taskstore.get(tid)
    assert rec is not None and rec["status"] == "pending", rec
    assert rec["spend_cap"] == 25.0, rec
    assert rec["budget_scope"] == "mission:7", rec
    assert rec["envelope"] == {"mission_id": 7}, rec
    assert rec["mission_id"] == 7, rec
    assert rec["parent_execution_id"] == "mission:default:7", rec


def test_worker_start_flips_pending_to_running_preserves_created_at():
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    tid = q.submit("run me")
    created_at_submit = taskstore.get(tid)["created_at"]
    q.start()
    st = _wait_status(q, tid, {"completed"})
    assert st and st["status"] == "completed", st
    rec = taskstore.get(tid)
    assert rec["status"] == "completed", rec
    assert rec["result"] == "child did the thing"
    assert rec["created_at"] == created_at_submit, (
        "worker start must flip the pending record, not overwrite it")
    q.stop()


def test_pending_record_recovered_as_blocked():
    taskstore.create("p1", "queued work", status="pending")
    assert taskstore.get("p1")["status"] == "pending"
    n = taskstore.recover_interrupted()
    assert n >= 1, n
    rec = taskstore.get("p1")
    assert rec["status"] == "blocked", rec
    assert "never started" in rec["blocked_reason"], rec
    assert any(r["task_id"] == "p1" for r in taskstore.resumable()), (
        "a recovered pending task must be resumable")


def test_running_recovery_unchanged():
    taskstore.create("r1", "mid-flight work")  # default: running
    n = taskstore.recover_interrupted()
    assert n >= 1, n
    rec = taskstore.get("r1")
    assert rec["status"] == "blocked", rec
    assert "restarted mid-task" in rec["blocked_reason"], rec
