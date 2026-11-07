"""Pass 28 #13: a mission waiting on a delegated task must follow
the task's resume chain.

Before: a mission step that started a delegated task stored the
child handle (task id) and settled from that id's status. When
the task blocked and was resumed under a NEW id, the step kept
waiting on the old (blocked) id forever — the resumed task's
completion never settled the step.

Fix contract pinned here: when the journal says the handled task
was resumed (status 'resumed', resumed_by=new id), the settler
follows the chain to the live id, re-points the step's stored
handle, and settles from the live task. A blocked task with no
resume keeps waiting, as before; multi-hop chains are followed.
"""
import http.client  # noqa: F401  (mirrors the missions harness imports)
import importlib
import json
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

for _name in ("aria.scheduler", "aria.hud", "aria.frame",
              "aria.hardware", "aria.spotify"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

# The real aria.agent package (NOT stubbed): taskqueue/taskstore
# must be the real modules for this test.
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [str(Path(__file__).parents[1] / "aria" / "agent")]
sys.modules.setdefault("aria.agent", _agent_pkg)

_vision = types.ModuleType("aria.vision")
_vision.get_face_frame_jpeg = lambda: None
_vision.publish_phone_frame = lambda jpeg: True
_vision.get_phone_frame_jpeg = lambda: None
_vision.get_phone_frame_status = lambda: {"active": False,
                                          "age_s": -1.0}
_vision.describe_phone_view = lambda q="": "[stubbed]"
sys.modules.setdefault("aria.vision", _vision)
_speech = types.ModuleType("aria.speech")
_speech.speak = lambda t: None
_speech.tts_bytes_for_bridge = lambda text: (b"", "no speech")
_speech.transcribe_audio = lambda audio, mime: ""
_speech._SPEECH_QUEUE = types.SimpleNamespace(
    unfinished_tasks=0, put=lambda t: None)
sys.modules.setdefault("aria.speech", _speech)

import pytest

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
data_dir = importlib.import_module("aria.data_dir")
approval = importlib.import_module("aria.approval")
dispatch = importlib.import_module("aria.tools.dispatch")
missions = importlib.import_module("aria.missions")

# The real taskqueue/taskstore modules (NOT the stubbed
# aria.agent.taskqueue from the missions harness preamble):
# import them fresh under the real package path.
taskstore = importlib.import_module("aria.agent.taskstore")
taskqueue = importlib.import_module("aria.agent.taskqueue")

CALLS = []


def _fake_note(a):
    CALLS.append(("fake_note", dict(a)))
    return f"noted {a.get('text', '')}"


dispatch.register_tool("fake_note", _fake_note)
dispatch.set_spine_hook(lambda *a, **k: None)
dispatch.set_log_hook(lambda *a, **k: None)

_SAFE = {"on": False}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "ARIA"))
    monkeypatch.setattr(memory, "DB_PATH",
                        str(tmp_path / "aria_memory.db"))
    memory.init_databases()
    monkeypatch.setattr(config, "is_safe_mode",
                        lambda: _SAFE["on"])
    monkeypatch.delenv("ARIA_SAFE_MODE", raising=False)
    # taskstore writes under config.WORKSPACE_DIR: scratch dir.
    monkeypatch.setattr(config, "WORKSPACE_DIR",
                        str(tmp_path / "ws"))
    approval._PENDING.clear()
    CALLS.clear()
    _SAFE["on"] = False
    missions.set_verifier(None)
    missions._worker_alive = False
    approval.set_mode("auto")
    dispatch.reset_turn_state()
    yield types.SimpleNamespace(tmp=tmp_path)
    approval._PENDING.clear()
    missions.set_verifier(None)
    missions._worker_alive = False
    dispatch.reset_turn_state()


class _CompletingExecutor:
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        return "child did the thing"


def _create_mission_with_delegated_step():
    args = {"goal": "delegate proof",
            "steps": json.dumps(
                [{"tool": "delegate_task",
                  "args": {"goal": "do the thing"}}]),
            "allowed_tools": ["delegate_task"]}
    out = missions.tool_mission_create(args)
    assert "Mission #" in out, out
    mid = int(out.split("Mission #", 1)[1].split(":", 1)[0])
    return mid


def test_mission_follows_resumed_delegated_task(env, monkeypatch):
    # The queue the settler reads: a fresh, unstarted one (the
    # singleton would start a worker thread).
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    monkeypatch.setattr(taskqueue, "get_queue", lambda: q)
    mid = _create_mission_with_delegated_step()
    step = missions._step_rows(mid)[0]
    # The delegated task, as the mission saw it: queued, then
    # blocked with a checkpoint.
    old_tid = q.submit("do the thing")
    q._tasks[old_tid].status = taskqueue.TaskStatus.BLOCKED
    taskstore.finish(old_tid, "blocked", blocked_reason="paused")
    missions._set_step(step["id"], status="waiting_child",
                       child_handle=json.dumps(
                           {"kind": "delegated_task",
                            "task_id": old_tid, "project": None}))
    # The task is resumed: a new id runs the remaining work.
    new_tid = q.submit("do the thing", resume_from=old_tid)
    assert taskstore.get(old_tid)["status"] == "resumed"
    # Settle against the OLD handle: the step must re-point to
    # the live id and keep waiting (the resumed task is pending).
    missions._settle_delegated_child(
        mid, missions._step_rows(mid)[0],
        {"kind": "delegated_task", "task_id": old_tid, "project": None},
        None)
    step = missions._step_rows(mid)[0]
    assert json.loads(step["child_handle"])["task_id"] == new_tid, step
    assert step["status"] == "waiting_child", step
    # The resumed task completes: settling (even against the stale
    # old handle) must now complete the step from the live task.
    q._tasks[new_tid].status = taskqueue.TaskStatus.COMPLETED
    q._tasks[new_tid].result = "did the thing"
    missions._settle_delegated_child(
        mid, missions._step_rows(mid)[0],
        {"kind": "delegated_task", "task_id": old_tid, "project": None},
        None)
    step = missions._step_rows(mid)[0]
    assert step["status"] == "done", step
    assert "did the thing" in (step["output"] or ""), step


def test_blocked_without_resume_keeps_waiting(env, monkeypatch):
    q = taskqueue.TaskQueue(executor_factory=lambda: _CompletingExecutor())
    monkeypatch.setattr(taskqueue, "get_queue", lambda: q)
    mid = _create_mission_with_delegated_step()
    step = missions._step_rows(mid)[0]
    old_tid = q.submit("do the thing")
    q._tasks[old_tid].status = taskqueue.TaskStatus.BLOCKED
    taskstore.finish(old_tid, "blocked", blocked_reason="paused")
    missions._set_step(step["id"], status="waiting_child",
                       child_handle=json.dumps(
                           {"kind": "delegated_task",
                            "task_id": old_tid, "project": None}))
    missions._settle_delegated_child(
        mid, missions._step_rows(mid)[0],
        {"kind": "delegated_task", "task_id": old_tid, "project": None},
        None)
    step = missions._step_rows(mid)[0]
    assert json.loads(step["child_handle"])["task_id"] == old_tid, step
    assert step["status"] == "waiting_child", step
