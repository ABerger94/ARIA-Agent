"""Pass 28 #21: the oldest waiting child must not starve every
later mission.

Before: _executor_tick picked ORDER BY id LIMIT 1 among runnable
missions, so a mission parked in 'waiting_child' (its delegated
child still running) was picked on every tick; its worker thread
reconciled nothing, saw the waiting step, and returned at once —
and the pick never reached a later mission with real work.

Fix contract pinned here:
* the pick prefers the oldest mission with work to do (no step
  in 'waiting_child'); a mission parked on a running child is
  skipped while a later mission has work;
* only when NO mission has work does the pick fall back to the
  oldest runnable mission even if it is waiting — its worker
  pass is what settles a finished child, so a waiting mission
  is never stranded by the skip;
* when the only runnable mission is purely waiting, the fallback
  still starts its worker (its pass reconciles the child), so
  the tick returns True there — the "no worker" case is only
  when nothing is runnable at all.
"""
import importlib
import json
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMPHOME = tempfile.mkdtemp(prefix='aria_mission_tick21_')

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

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

agent_pkg = types.ModuleType("aria.agent")
agent_pkg.__path__ = [str(Path(__file__).parents[1] / "aria" / "agent")]
sys.modules.setdefault("aria.agent", agent_pkg)
taskqueue = importlib.import_module("aria.agent.taskqueue")
workers_mod = importlib.import_module("aria.agent.workers")
agent_pkg.start_background_job = workers_mod.start_background_job
agent_pkg.list_background_jobs = workers_mod.list_background_jobs

import pytest
import time

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
data_dir = importlib.import_module("aria.data_dir")
approval = importlib.import_module("aria.approval")
dispatch = importlib.import_module("aria.tools.dispatch")
missions = importlib.import_module("aria.missions")
taskstore = importlib.import_module("aria.agent.taskstore")

CALLS = []
PICKED = []


def _fake_note(a):
    CALLS.append(("fake_note", dict(a)))
    return f"noted {a.get('text', '')}"


dispatch.register_tool("fake_note", _fake_note)
dispatch.set_spine_hook(lambda *a, **k: None)
dispatch.set_log_hook(lambda *a, **k: None)


class _FakeExecutor:
    def execute(self, goal, speak=None, cancel_flag=None):
        return "child did the thing"


def _make_queue():
    q = taskqueue.TaskQueue(
        executor_factory=lambda: _FakeExecutor())
    q.start()
    return q


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "ARIA"))
    monkeypatch.setattr(memory, "DB_PATH",
                        str(tmp_path / "aria_memory.db"))
    memory.init_databases()
    monkeypatch.setattr(config, "is_safe_mode", lambda: False)
    monkeypatch.delenv("ARIA_SAFE_MODE", raising=False)
    approval._PENDING.clear()
    CALLS.clear()
    PICKED.clear()
    missions.set_verifier(None)
    missions._worker_alive = False
    approval.set_mode("auto")
    dispatch.reset_turn_state()
    queue = _make_queue()
    monkeypatch.setattr(taskqueue, "get_queue", lambda: queue)
    # The tick starts a worker thread for the picked mission —
    # record the pick instead of running a mission.
    monkeypatch.setattr(
        missions, "_mission_entry",
        lambda mid: PICKED.append(mid))
    yield types.SimpleNamespace(tmp=tmp_path, queue=queue)
    queue.stop()
    approval._PENDING.clear()
    missions.set_verifier(None)
    missions._worker_alive = False
    dispatch.reset_turn_state()


def _create_mission(goal, steps, **kw):
    args = {"goal": goal,
            "steps": json.dumps(
                [{"tool": t, "args": a} for t, a in steps])}
    args.update(kw)
    out = missions.tool_mission_create(args)
    assert "Mission #" in out, out
    return int(out.split("Mission #", 1)[1].split(":", 1)[0])


def _approve_plan(mid):
    tok = missions._mission_row(mid)["plan_token"]
    assert tok, "plan was not parked"
    result = approval.approve_token(tok)
    assert "plan approved" in result, result


def _setup(env):
    # A delegated child that is still running (journal only).
    taskstore.create("child1", "child goal")
    # Older mission A: parked waiting on that child.
    mid_a = _create_mission(
        "waiting parent",
        [("delegate_task", {"goal": "child goal"})])
    _approve_plan(mid_a)
    step_a = missions._step_rows(mid_a)[0]
    missions._set_step(
        step_a["id"], status="waiting_child",
        child_handle=json.dumps({
            "kind": "delegated_task", "task_id": "child1",
            "project": "default"}))
    missions._set_mission(mid_a, status="waiting_child")
    # Newer mission B: real work to do.
    mid_b = _create_mission(
        "ready worker", [("fake_note", {"text": "hi"})])
    _approve_plan(mid_b)
    return mid_a, mid_b


def _wait_picked(timeout=5):
    # _executor_tick starts the worker on a thread and returns at
    # once; the pick lands a moment later.
    end = time.time() + timeout
    while time.time() < end:
        if PICKED:
            return True
        time.sleep(0.02)
    return False


def test_tick_skips_waiting_mission_for_ready_one(env):
    mid_a, mid_b = _setup(env)
    assert missions._executor_tick() is True
    assert _wait_picked()
    assert PICKED == [mid_b], PICKED


def test_tick_falls_back_to_waiting_mission_when_nothing_else(env):
    mid_a, mid_b = _setup(env)
    # Finish B so only the waiting mission is runnable: the pick
    # falls back to it — its worker pass is what settles the
    # child — instead of leaving it stranded.
    missions._set_mission(mid_b, status="completed")
    assert missions._executor_tick() is True
    assert _wait_picked()
    assert PICKED == [mid_a], PICKED


def test_tick_picks_waiting_mission_once_child_settles(env):
    mid_a, mid_b = _setup(env)
    missions._set_mission(mid_b, status="completed")
    taskstore.finish("child1", "completed", result="child did it")
    # The fallback pick takes A; the worker pass (replaced here
    # by the recorder) is what settles the step, so the step is
    # still waiting_child at pick time — the pick, not the
    # settlement, is what this pins.
    assert missions._executor_tick() is True
    assert _wait_picked()
    assert PICKED == [mid_a], PICKED
