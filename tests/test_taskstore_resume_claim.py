"""Pass 28 #15: a resume must atomically claim the previous task.

Before: submit(resume_from=X) read the old record, checked it
was resumable, and created a new task — but never marked the old
record. A second resume of the same task (or a concurrent one)
built a second executor for the same remaining steps, so the
steps ran twice.

Fix contract pinned here: the old record is claimed (status
'resumed', resumed_by=new id) in the SAME locked journal write
as the new record's creation. A second resume finds the old
record already claimed and refuses; a failed create leaves the
old record untouched (no stranded claim, no ghost task).
"""
import importlib
import os
import sys
import tempfile
import threading
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

_cfg = importlib.import_module("aria.config")
_memory = importlib.import_module("aria.memory")
_memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_taskstore_resume_claim_"), "test.db")
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
_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskstore-resume-claim-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")


class _CompletingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        return "child did the thing"


def _blocked_with_checkpoint(task_id="old1"):
    taskstore.create(task_id, "do the thing")
    taskstore.checkpoint(task_id, {
        "plan": ["s1", "s2"], "completed": ["s1"],
        "results": {"s1": "did s1"}})
    taskstore.finish(task_id, "blocked", blocked_reason="paused")


def test_second_resume_of_same_task_refused():
    _blocked_with_checkpoint()
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    tid1 = q.submit("do the thing", resume_from="old1")
    old = taskstore.get("old1")
    assert old["status"] == "resumed", old
    assert old["resumed_by"] == tid1, old
    assert taskstore.get(tid1)["resumed_from"] == "old1"
    with pytest.raises(ValueError, match="not resumable"):
        q.submit("do the thing", resume_from="old1")
    assert len(q._tasks) == 1, "the refused resume queued a task"


def test_concurrent_resumes_claim_once():
    _blocked_with_checkpoint()
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    barrier = threading.Barrier(2)
    results = []

    def _try():
        barrier.wait(timeout=10)
        try:
            results.append(("ok", q.submit("do the thing",
                                           resume_from="old1")))
        except Exception as e:  # noqa: BLE001 — the race outcome
            results.append(("err", type(e).__name__))

    threads = [threading.Thread(target=_try) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert sorted(k for k, _ in results) == ["err", "ok"], results
    assert len(q._tasks) == 1
    assert taskstore.get("old1")["status"] == "resumed"


def test_failed_create_leaves_old_record_resumable(monkeypatch):
    _blocked_with_checkpoint()
    monkeypatch.setattr(taskstore, "_save", lambda data: False)
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    with pytest.raises(RuntimeError, match="journal"):
        q.submit("do the thing", resume_from="old1")
    # The single write failed, so neither the claim nor the new
    # record landed: the old task is still resumable.
    assert taskstore.get("old1")["status"] == "blocked"
    assert taskstore.get("old1").get("resumed_by") in (None, "")
