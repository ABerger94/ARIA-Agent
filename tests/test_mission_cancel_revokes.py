"""Pass 28 #24: cancelling a mission must revoke the background
and deferred work it owns.

Before: cancel_mission stopped the mission and its delegated
tasks, but a background job a step had started kept running
(the job's process was never signalled, and the waiting_child
step was not even marked cancelled), and schedules, triggers
and autonomous goals the mission had created under its origin
policy kept firing under the dead mission's envelope and cap.

Fix contract pinned here:
* every background job named by a step's child_handle is
  cancelled in the handle's project, and waiting_child steps
  are marked cancelled with the rest;
* schedules, triggers and autonomous goals whose stored policy
  names the mission are revoked; items owned by anyone else
  (or by no mission) are untouched;
* the cancel message names what was stopped and revoked.
"""
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

for _name in ("aria.hud", "aria.frame",
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

import psutil
import pytest

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
data_dir = importlib.import_module("aria.data_dir")
approval = importlib.import_module("aria.approval")
dispatch = importlib.import_module("aria.tools.dispatch")
missions = importlib.import_module("aria.missions")
runtime = importlib.import_module("aria.runtime")
scheduler = importlib.import_module("aria.scheduler")
triggers = importlib.import_module("aria.triggers")
proactive = importlib.import_module("aria.agent.proactive")
workers = importlib.import_module("aria.agent.workers")
from aria.memory import job_db_list

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
    monkeypatch.setattr(scheduler, "DB_PATH",
                        str(tmp_path / "aria_memory.db"))
    # triggers.json lives under triggers.STORE_DIR (bound at
    # import from config.WORKSPACE_DIR) — point it at tmp so
    # test triggers never touch the real store.
    monkeypatch.setattr(triggers, "STORE_DIR", str(tmp_path / "ws"))
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


def _wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def _proc_gone(pid):
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            return True
    except ChildProcessError:
        return True
    except OSError:
        pass
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _mission_policy(mid):
    return {"mission_id": mid, "envelope": {"mission_id": mid},
            "budget_scope": f"mission:{mid}", "spend_cap": 0}


def test_cancel_revokes_owned_background_job(env):
    mid, _ = _create("job owner",
                     [("fake_note", {"text": "hi"})])
    _approve_plan(mid)
    job = workers.start_background_job("sleep 25", "owned sleeper")
    assert "job_id" in job, job
    jid, pid = job["job_id"], job["pid"]
    assert _wait_for(lambda: psutil.pid_exists(pid))
    step = missions._step_rows(mid)[0]
    missions._set_step(
        step["id"], status="waiting_child",
        child_handle=json.dumps({
            "kind": "background_job", "job_id": jid,
            "project": "default"}))
    missions._set_mission(mid, status="waiting_child")

    result = missions.cancel_mission(mid)
    assert "cancelled" in result.lower(), result
    assert "1 background job" in result, result
    assert _wait_for(lambda: _proc_gone(pid)), "job still alive"
    assert missions._step_rows(mid)[0]["status"] == "cancelled"
    assert _row(mid)["status"] == "cancelled"
    row = next((r for r in job_db_list(50) if r["id"] == jid), None)
    assert row["status"] == "killed", row


def test_cancel_revokes_owned_schedule_only(env):
    mid, _ = _create("schedule owner",
                     [("fake_note", {"text": "hi"})])
    _approve_plan(mid)
    owned = scheduler.sched_add(
        "once", "owned reminder", delay_s=3600,
        policy=_mission_policy(mid))
    other = scheduler.sched_add(
        "once", "someone else", delay_s=3600, policy={})
    result = missions.cancel_mission(mid)
    assert "1 schedule" in result, result
    ids = [r[0] for r in scheduler.sched_list()]
    assert owned not in ids
    assert other in ids


def test_cancel_revokes_owned_trigger_only(env):
    mid, _ = _create("trigger owner",
                     [("fake_note", {"text": "hi"})])
    _approve_plan(mid)
    owned = triggers.create_trigger(
        "owned trig", "presence_return", "say hi",
        policy=_mission_policy(mid))
    assert owned["ok"], owned
    other = triggers.create_trigger(
        "other trig", "presence_return", "say hi", policy={})
    assert other["ok"], other
    result = missions.cancel_mission(mid)
    assert "1 trigger" in result, result
    names = [t["name"] for t in triggers.load_triggers()]
    assert "owned trig" not in names
    assert "other trig" in names


def test_cancel_revokes_owned_autonomous_goal_only(env):
    mid, _ = _create("goal owner",
                     [("fake_note", {"text": "hi"})])
    _approve_plan(mid)
    with runtime.execution(
            "mission", execution_id=f"mission:default:{mid}",
            budget_scope=f"mission:{mid}", spend_cap=0,
            envelope={"mission_id": mid}):
        owned = proactive.goal_create("owned goal", "do the thing")
    other = proactive.goal_create("other goal", "do the other")
    result = missions.cancel_mission(mid)
    assert "1 autonomous goal" in result, result
    titles = [g["title"] for g in proactive.goal_list()]
    assert "owned goal" not in titles
    assert "other goal" in titles
