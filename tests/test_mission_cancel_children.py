"""Pass 26 B2#5 — mission-owned delegated work must not outlive cancellation.

Pins:
  * a task submitted under a mission execution context carries the
    mission's identity;
  * cancel_mission cancels that queued/running child (cancel flag
    set, status cancelled);
  * an unrelated task submitted outside the mission is untouched.
"""
import http.client  # noqa: F401  (mirrors the missions harness imports)
import importlib
import json
import os
import sys
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)
_agent_pkg = types.ModuleType("aria.agent")
_agent_pkg.__path__ = [str(Path(__file__).parents[1] / "aria" / "agent")]
sys.modules.setdefault("aria.agent", _agent_pkg)

for _name in ("aria.scheduler", "aria.hud", "aria.frame",
              "aria.hardware", "aria.spotify"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

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
runtime = importlib.import_module("aria.runtime")
taskqueue = importlib.import_module("aria.agent.taskqueue")
taskstore = importlib.import_module("aria.agent.taskstore")
taskexec = importlib.import_module("aria.agent.taskexec")

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
    monkeypatch.setattr(config, "WORKSPACE_DIR",
                        str(tmp_path / "ws"))
    monkeypatch.delenv("ARIA_SAFE_MODE", raising=False)
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


def _create(goal, steps, **kw):
    args = {"goal": goal,
            "steps": json.dumps(
                [{"tool": t, "args": a} for t, a in steps])}
    args.update(kw)
    out = missions.tool_mission_create(args)
    assert "Mission #" in out, out
    mid = int(out.split("Mission #", 1)[1].split(":", 1)[0])
    return mid, out


def _row(mid):
    return missions._mission_row(mid)


def _approve_plan(mid):
    tok = _row(mid)["plan_token"]
    assert tok, "plan was not parked"
    result = approval.approve_token(tok)
    assert "plan approved" in result, result
    assert _row(mid)["plan_approved"] is True


class _BlockingChild:
    """Runs until its cancel flag is set (then raises Cancelled,
    which the queue records as status cancelled)."""
    def execute(self, goal, speak=None, cancel_flag=None, **kw):
        end = time.time() + 15
        while time.time() < end:
            if cancel_flag is not None and cancel_flag.is_set():
                raise runtime.Cancelled("cancelled by test")
            time.sleep(0.02)
        return "child done"


def _wait_status(q, tid, want, timeout=10):
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = q.get_status(tid)
        if st and st["status"] == want:
            return st
        time.sleep(0.05)
    return st


def test_cancel_mission_stops_delegated_children(env):
    q = taskqueue.TaskQueue(max_concurrent=2,
                            executor_factory=lambda: _BlockingChild())
    q.start()
    taskqueue._queue = q
    try:
        mid, _out = _create("cancel children proof",
                            [("fake_note", {"text": "hi"})])
        _approve_plan(mid)
        # What a mission step's delegate_task call sees: the
        # mission execution context (envelope carries mission_id).
        env_dict = {"allowed_tools": ["fake_note"], "spend_cap": 0,
                    "pre_approved_irreversible": [],
                    "mission_id": mid, "goal": "cancel children proof"}
        with runtime.execution(
                "mission", execution_id=f"mission:default:{mid}",
                budget_scope=f"mission:{mid}", spend_cap=0,
                envelope=env_dict):
            child = taskqueue.get_queue().submit("child work")
        assert q._tasks[child].mission_id == mid, q._tasks[child]
        with runtime.execution("tool", execution_id="conv-9"):
            other = taskqueue.get_queue().submit("unrelated work")
        st = _wait_status(q, child, "running")
        assert st and st["status"] == "running", st
        result = missions.cancel_mission(mid)
        assert "cancelled" in result.lower(), result
        st = _wait_status(q, child, "cancelled")
        assert st and st["status"] == "cancelled", st
        assert q._tasks[child].cancel_flag.is_set()
        assert q._tasks[child].status == taskqueue.TaskStatus.CANCELLED
        # The unrelated task is untouched.
        assert not q._tasks[other].cancel_flag.is_set()
        assert q._tasks[other].status != taskqueue.TaskStatus.CANCELLED
        # The mission itself is cancelled.
        assert _row(mid)["status"] == "cancelled"
    finally:
        q.stop()
        taskqueue._queue = None
    print("ok cancel_mission_stops_delegated_children")


if __name__ == "__main__":
    test_cancel_mission_stops_delegated_children()
    print("ALL MISSION CANCEL CHILDREN CHECKS PASSED")
