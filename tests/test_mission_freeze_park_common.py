"""Pass 28 #4: mission parking must use the COMMON revision freezer.

The mission park path froze only trust_routine and skill_activate
with its own local helpers, bypassing dispatch._freeze_park_args —
so a mission step parking export_routine_pack, a user tool,
mcp_connect, or a discovered MCP tool parked UNFROZEN: the
replacement (a re-derived export path, a swapped tool file, a
changed server config) rode the original token. Pins that a
mission-parked export resolves its destination once, exactly as
the dispatch gate does.
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


def test_mission_parked_export_resolves_destination_once(env):
    _save_routine(
        "myroutine",
        [{"tool": "fake_note", "args": {"text": "hi"}}])
    mid, _out = _create(
        "export proof",
        [("export_routine_pack",
          {"names_csv": "myroutine", "dest_path": ""})],
        allowed_tools=["fake_note"])
    _approve_plan(mid)
    row = _pump(mid, {"waiting_approval"})
    assert row["status"] == "waiting_approval", row
    parked = [e for e in approval._PENDING.values()
              if e["tool"] == "export_routine_pack"]
    assert len(parked) == 1, approval._PENDING
    dest = parked[0]["args"].get("dest_path", "")
    # The freeze resolves the empty destination ONCE, at park
    # time — an unfrozen park stashes "" and the replay
    # re-derives a different timestamped name.
    assert dest and os.path.isabs(dest), parked[0]
    assert parked[0]["args"].get("overwrite") is False, parked[0]
    print("ok mission_parked_export_resolves_destination_once")


if __name__ == "__main__":
    test_mission_parked_export_resolves_destination_once()
    print("ALL MISSION COMMON-FREEZE PARK CHECKS PASSED")
