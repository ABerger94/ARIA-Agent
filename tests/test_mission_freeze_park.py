"""Pass 26 B1#3 (missions park-path half) — a mission-parked trust_routine
carries a frozen revision; trust for a routine that does not exist is
refused, not parked revisionless.

Pins:
  * a trust_routine step parked outside the mission envelope has
    the routine's current revision frozen into the stashed
    approval args;
  * a trust_routine step naming a routine that does not exist
    fails the mission with a clear error and parks nothing.
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

for _name in ("aria.scheduler", "aria.hud", "aria.frame",
              "aria.hardware", "aria.spotify",
              "aria.agent.taskqueue"):
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
routines = importlib.import_module("aria.routines")

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


def _pump(mid, want, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        missions.executor_tick()
        row = _row(mid)
        if row["status"] in want:
            return row
        time.sleep(0.05)
    return _row(mid)


def _save_routine(name, steps):
    data = {"name": name, "steps": steps, "trusted": False}
    os.makedirs(routines._routines_dir(), exist_ok=True)
    with open(routines._routine_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def test_mission_parked_trust_routine_carries_revision(env):
    data = _save_routine(
        "myroutine",
        [{"tool": "fake_note", "args": {"text": "hi"}}])
    expected = routines._routine_revision(data)
    mid, _out = _create(
        "trust proof",
        [("trust_routine", {"name": "myroutine"})],
        allowed_tools=["fake_note"])
    _approve_plan(mid)
    row = _pump(mid, {"waiting_approval"})
    assert row["status"] == "waiting_approval", row
    parked = [e for e in approval._PENDING.values()
              if e["tool"] == "trust_routine"]
    assert len(parked) == 1, approval._PENDING
    assert parked[0]["args"].get("revision") == expected, parked[0]
    assert parked[0]["args"].get("name") == "myroutine"
    print("ok mission_parked_trust_routine_carries_revision")


def test_mission_parked_trust_for_missing_routine_refused(env):
    mid, _out = _create(
        "ghost trust",
        [("trust_routine", {"name": "ghostroutine"})],
        allowed_tools=["fake_note"])
    _approve_plan(mid)
    row = _pump(mid, {"failed"})
    assert row["status"] == "failed", row
    assert "ghostroutine" in (row["summary"] or ""), row
    assert "does not exist" in (row["summary"] or ""), row
    parked = [e for e in approval._PENDING.values()
              if e["tool"] == "trust_routine"]
    assert parked == [], approval._PENDING
    steps = missions._step_rows(mid)
    assert steps[0]["status"] == "failed", steps
    print("ok mission_parked_trust_for_missing_routine_refused")


if __name__ == "__main__":
    test_mission_parked_trust_routine_carries_revision()
    test_mission_parked_trust_for_missing_routine_refused()
    print("ALL MISSION FREEZE PARK CHECKS PASSED")
