"""Pass 27 #25: starting a child must not certify the parent.

Before: delegate_task and manage_background_job(start) returned
plain text ("Delegated as task ...", "Background job #... started
..."), which normalize() read as DONE — so a mission marked the
step done and verified the mission before the child finished,
or failed. The submission was certified as the work.

Fix contract pinned here:
- both tools return outcome.started with the durable child
  handle (child id + project) — never DONE text;
- a mission step returning STARTED with a handle goes
  'waiting_child' (handle stored) instead of 'done', and the
  mission 'waiting_child' — it is not verified while a child
  is outstanding;
- later executor ticks settle the step from the child's
  terminal outcome: completed -> step done (mission proceeds),
  failed -> step failed (mission fails, no later step runs);
- a child with no record at all goes 'uncertain' /
  'blocked_input' — the loss is surfaced, never certified.

Harness: real missions + approval + dispatch (test_missions.py
shape), real taskqueue with an injected fake executor, real
background jobs (echo exits 0 at once). aria.agent is stubbed
as a package so importing it does not pull in brain; its
taskqueue/workers submodules are real.
"""
import importlib
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMPHOME = tempfile.mkdtemp(prefix='aria_mission_child25_')

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

# Stub the aria.agent package (importing the real __init__ pulls
# in brain) but keep its taskqueue/workers submodules real.
agent_pkg = types.ModuleType("aria.agent")
agent_pkg.__path__ = [str(Path(__file__).parents[1] / "aria" / "agent")]
sys.modules.setdefault("aria.agent", agent_pkg)
taskqueue = importlib.import_module("aria.agent.taskqueue")
workers_mod = importlib.import_module("aria.agent.workers")
agent_pkg.start_background_job = workers_mod.start_background_job
agent_pkg.list_background_jobs = workers_mod.list_background_jobs

import pytest

from aria import outcome as outcome_mod
config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
data_dir = importlib.import_module("aria.data_dir")
approval = importlib.import_module("aria.approval")
dispatch = importlib.import_module("aria.tools.dispatch")
missions = importlib.import_module("aria.missions")

CALLS = []


def _fake_note(a):
    CALLS.append(("fake_note", dict(a)))
    return f"noted {a.get('text', '')}"


dispatch.register_tool("fake_note", _fake_note)
dispatch.set_spine_hook(lambda *a, **k: None)
dispatch.set_log_hook(lambda *a, **k: None)


class _FakeExecutor:
    """A delegated-task executor: completes at once, or raises."""

    def __init__(self, behavior="ok"):
        self.behavior = behavior

    def execute(self, goal, speak=None, cancel_flag=None):
        if self.behavior == "fail":
            raise RuntimeError("child blew up")
        return "child did the thing"


def _make_queue(behavior="ok"):
    q = taskqueue.TaskQueue(
        executor_factory=lambda: _FakeExecutor(behavior))
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
    missions.set_verifier(None)
    missions._worker_alive = False
    approval.set_mode("auto")
    dispatch.reset_turn_state()
    queue = _make_queue("ok")
    monkeypatch.setattr(taskqueue, "get_queue", lambda: queue)
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


def _pump(mid, want, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        missions.executor_tick()
        row = missions._mission_row(mid)
        if row["status"] in want:
            return row
        time.sleep(0.05)
    return missions._mission_row(mid)


def test_delegate_task_returns_started_with_handle(env):
    out = dispatch.execute_tool(
        "delegate_task", {"goal": "child goal"})[0]
    assert outcome_mod.normalize(out).status == "started", out
    assert "task_id=" in out and "project=default" in out, out
    assert "Delegated as task" not in out
    # The submission alone is not the work: nothing certifies it.
    assert "child did the thing" not in out


def test_mission_waits_then_completes_on_child(env):
    mid = _create_mission(
        "child completes",
        [("delegate_task", {"goal": "child goal"}),
         ("fake_note", {"text": "after"})],
        allowed_tools="delegate_task,fake_note")
    _approve_plan(mid)
    # First the mission waits on the child (fake_note not run)...
    row = _pump(mid, {"waiting_child"})
    assert row["status"] == "waiting_child", row
    steps = missions._step_rows(mid)
    assert steps[0]["status"] == "waiting_child", steps
    assert steps[0]["child_handle"], steps
    assert CALLS == [], CALLS
    # ...then the child's completion settles it and the mission runs on.
    row = _pump(mid, {"done"})
    assert row["status"] == "done", row
    steps = missions._step_rows(mid)
    assert steps[0]["status"] == "done", steps
    assert "child did the thing" in (steps[0]["output"] or ""), steps
    assert len([c for c in CALLS if c[0] == "fake_note"]) == 1, CALLS


def test_mission_fails_when_child_fails(env, monkeypatch):
    failing = _make_queue("fail")
    monkeypatch.setattr(taskqueue, "get_queue", lambda: failing)
    try:
        mid = _create_mission(
            "child fails",
            [("delegate_task", {"goal": "child goal"}),
             ("fake_note", {"text": "after"})],
            allowed_tools="delegate_task,fake_note")
        _approve_plan(mid)
        row = _pump(mid, {"waiting_child"})
        assert row["status"] == "waiting_child", row
        row = _pump(mid, {"failed"})
        assert row["status"] == "failed", row
        steps = missions._step_rows(mid)
        assert steps[0]["status"] == "failed", steps
        assert "child blew up" in (steps[0]["output"] or ""), steps
        assert CALLS == [], CALLS  # the later step never ran
    finally:
        failing.stop()


def test_mission_blocked_when_child_record_gone(env, monkeypatch):
    mid = _create_mission(
        "child lost",
        [("delegate_task", {"goal": "child goal"}),
         ("fake_note", {"text": "after"})],
        allowed_tools="delegate_task,fake_note")
    _approve_plan(mid)
    # Park the mission on the child, then lose the queue — the
    # restart case (pass 27 #26's gap, before it is fixed).
    row = _pump(mid, {"waiting_child"})
    assert row["status"] == "waiting_child", row
    fresh = taskqueue.TaskQueue(
        executor_factory=lambda: _FakeExecutor("ok"))
    fresh.start()
    monkeypatch.setattr(taskqueue, "get_queue", lambda: fresh)
    try:
        row = _pump(mid, {"blocked_input"})
        assert row["status"] == "blocked_input", row
        steps = missions._step_rows(mid)
        assert steps[0]["status"] == "uncertain", steps
        assert CALLS == [], CALLS  # nothing certified, nothing ran on
    finally:
        fresh.stop()


def test_background_job_waits_then_completes(env):
    mid = _create_mission(
        "job completes",
        [("manage_background_job",
          {"action": "start", "command": "echo hi",
           "name": "job25"}),
         ("fake_note", {"text": "after"})],
        allowed_tools="manage_background_job,fake_note",
        pre_approved_irreversible="manage_background_job")
    _approve_plan(mid)
    row = _pump(mid, {"done"})
    assert row["status"] == "done", row
    steps = missions._step_rows(mid)
    assert steps[0]["status"] == "done", steps
    assert "completed (exit 0)" in (steps[0]["output"] or ""), steps
    assert len([c for c in CALLS if c[0] == "fake_note"]) == 1, CALLS
