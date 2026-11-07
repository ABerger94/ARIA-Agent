"""Pass 28 #17: a task submission must not be acknowledged as
durable when the journal write fails.

Before: taskstore._save swallowed every failure to a log line and
create() returned the record anyway, so submit() queued the task
in memory and returned its id — an acknowledgement of durability
for a record that was never written. A restart then lost the task
with no trace, and the submitter had been told it was safe.

Fix contract pinned here:
- _save reports success/failure (bool) instead of swallowing
  silently;
- create() raises RuntimeError when the write fails, instead of
  returning an unwritten record;
- submit() therefore never queues or acknowledges a task whose
  journal write failed: the caller gets the error, not an id.
"""
import importlib
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

_cfg = importlib.import_module("aria.config")
_memory = importlib.import_module("aria.memory")
_memory.DB_PATH = os.path.join(
    tempfile.mkdtemp(prefix="aria_taskstore_durable_"), "test.db")
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
_cfg.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria-taskstore-durable-ws-")

taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")
taskqueue = importlib.import_module("aria.agent.taskqueue")


class _CompletingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        return "child did the thing"


def test_create_raises_when_journal_write_fails(monkeypatch):
    monkeypatch.setattr(taskstore, "_save", lambda data: False)
    with pytest.raises(RuntimeError, match="journal"):
        taskstore.create("t1", "do a thing")
    assert taskstore.get("t1") is None
    assert taskstore._load() == {}


def test_submit_raises_and_queues_nothing_when_journal_write_fails(monkeypatch):
    monkeypatch.setattr(taskstore, "_save", lambda data: False)
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    with pytest.raises(RuntimeError, match="journal"):
        q.submit("queued work, never durable")
    # Nothing was acknowledged: no id returned, nothing queued in
    # memory, nothing in the journal.
    assert q._tasks == {} and q._queue == []
    assert taskstore._load() == {}
