"""Pass 28 #23: a child-job lookup must not mistake an old job
for a missing job.

Before: _settle_job_child searched list_background_jobs(200) —
the 200 MOST RECENT jobs. Once 200 newer jobs existed, the
mission's child fell out of the window, the lookup found no
row, and the step went 'uncertain' ("has no record") even
though the job's record was right there in the DB.

Fix contract pinned here:
* memory.memory.job_db_get(job_id) fetches one job row by id, no
  window;
* _settle_job_child uses it, so a child's age never makes it
  look missing.
"""
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

config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
data_dir = importlib.import_module("aria.data_dir")
approval = importlib.import_module("aria.approval")
dispatch = importlib.import_module("aria.tools.dispatch")
missions = importlib.import_module("aria.missions")


def _fake_note(a):
    return f"noted {a.get('text', '')}"


dispatch.register_tool("fake_note", _fake_note)
dispatch.set_spine_hook(lambda *a, **k: None)
dispatch.set_log_hook(lambda *a, **k: None)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "ARIA"))
    monkeypatch.setattr(memory, "DB_PATH",
                        str(tmp_path / "aria_memory.db"))
    memory.init_databases()
    monkeypatch.setattr(config, "is_safe_mode", lambda: False)
    monkeypatch.delenv("ARIA_SAFE_MODE", raising=False)
    approval._PENDING.clear()
    missions.set_verifier(None)
    missions._worker_alive = False
    approval.set_mode("auto")
    dispatch.reset_turn_state()
    yield types.SimpleNamespace(tmp=tmp_path)
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


def test_job_db_get_finds_row_by_id(env):
    jid = memory.job_db_create("old job", "sleep 1", 1234, "/tmp/x.log")
    assert memory.job_db_get(jid)["name"] == "old job"
    assert memory.job_db_get(jid + 9999) is None
    assert memory.job_db_get("not-an-id") is None


def test_settle_job_child_finds_old_job_beyond_list_window(env):
    # The child is the OLDEST job; 250 newer jobs push it far
    # outside any 200-row recent window.
    jid = memory.job_db_create("child job", "sleep 1", 1234, "/tmp/x.log")
    for i in range(250):
        memory.job_db_create(f"newer {i}", "sleep 1", 2000 + i, "/tmp/x.log")
    memory.job_db_update(jid, "completed", exit_code=0)

    mid = _create_mission(
        "old child", [("fake_note", {"text": "after"})])
    _approve_plan(mid)
    step = missions._step_rows(mid)[0]
    handle = {"kind": "background_job", "job_id": jid,
              "project": "default"}
    missions._set_step(
        step["id"], status="waiting_child",
        child_handle=json.dumps(handle))
    missions._set_mission(mid, status="waiting_child")

    missions._settle_job_child(mid, step, handle, "default")
    assert missions._step_rows(mid)[0]["status"] == "done", \
        missions._step_rows(mid)
