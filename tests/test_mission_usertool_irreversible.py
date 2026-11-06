"""Pass 26 B3#2 — mission irreversible-tool classification must cover
user-defined tools.

Pins:
  * a mission step naming a registered user-defined tool is marked
    [irreversible] in the plan text;
  * in auto mode with an empty pre_approved_irreversible list, the
    step parks for approval instead of executing preauthorized.
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

CALLS = []


def _fake_note(a):
    CALLS.append(("fake_note", dict(a)))
    return f"noted {a.get('text', '')}"


def _fake_user_tool(a):
    CALLS.append(("my_user_tool", dict(a)))
    return "user tool ran"


dispatch.register_tool("fake_note", _fake_note)
dispatch.register_tool("my_user_tool", _fake_user_tool)
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


def test_user_tool_step_parks_and_marks_irreversible(env):
    config.register_user_tool("my_user_tool")
    try:
        mid, out = _create("user tool proof",
                           [("my_user_tool", {"x": "1"})])
        assert "my_user_tool [irreversible]" in out, out
        _approve_plan(mid)
        row = _pump(mid, {"waiting_approval"})
        assert row["status"] == "waiting_approval", row
        assert [c for c in CALLS if c[0] == "my_user_tool"] == []
        steps = missions._step_rows(mid)
        assert steps[0]["status"] == "waiting", steps
    finally:
        config.unregister_user_tool("my_user_tool")
    print("ok user_tool_step_parks_and_marks_irreversible")


if __name__ == "__main__":
    test_user_tool_step_parks_and_marks_irreversible()
    print("ALL MISSION USERTOOL CHECKS PASSED")
