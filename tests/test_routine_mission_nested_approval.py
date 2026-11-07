"""Pass 27 #24: a routine's nested approval must not strand its
parent mission.

Before: a routine step that parked for approval made the routine
return outcome.ask(...) — a text starting with the ASK line, not
the [AWAITING_APPROVAL token=...] marker. A mission running that
routine therefore took the ASK branch (step 'uncertain', mission
'blocked_input', no approval_token stored), and when the inner
approval resolved the routine resumed but the mission never
settled — _on_approval_resolved found no step row for the token.

Fix contract pinned here:
- the routine's paused return carries the parked token as the
  FIRST line ([AWAITING_APPROVAL token=...]), so the mission
  parks the step on the inner token; the ASK line stays, so
  normalize() still reads ASK for every other consumer;
- approving the inner token executes the step, resumes the
  routine from the next step, and settles the parent mission
  (done) from the child's terminal receipt;
- denying the inner token fails the mission without executing
  anything further;
- with no token in the step's result (handler-side re-park) the
  return stays exactly as before.

Harness: real missions + approval + dispatch + routines, as in
tests/test_missions.py and tests/test_routine_resume.py.
"""
import http.client  # noqa: F401  (mirrors test_missions.py imports)
import importlib
import json
import os
import re
import sys
import tempfile
import threading  # noqa: F401
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_mission24_')

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
_vision.get_phone_frame_status = lambda: {"active": False, "age_s": -1.0}
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

from aria import outcome as outcome_mod
config = importlib.import_module("aria.config")
memory = importlib.import_module("aria.memory")
data_dir = importlib.import_module("aria.data_dir")
approval = importlib.import_module("aria.approval")
dispatch = importlib.import_module("aria.tools.dispatch")
missions = importlib.import_module("aria.missions")
routines = importlib.import_module("aria.routines")
bridge = importlib.import_module("aria.bridge")  # noqa: F401

CALLS = []


def _fake_note(a):
    CALLS.append(("fake_note", dict(a)))
    return f"noted {a.get('text', '')}"


def _fake_email(a):
    CALLS.append(("send_email", dict(a)))
    return f"email sent to {a.get('to', '')}"


dispatch.register_tool("fake_note", _fake_note)
dispatch.register_tool("send_email", _fake_email)
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
    CALLS.clear()
    missions.set_verifier(None)
    missions._worker_alive = False
    approval.set_mode("confirm-risky")
    dispatch.reset_turn_state()
    routines._PAUSED.clear()
    routines._paused_loaded = False
    yield types.SimpleNamespace(tmp=tmp_path)
    approval._PENDING.clear()
    missions.set_verifier(None)
    missions._worker_alive = False
    dispatch.reset_turn_state()
    routines._PAUSED.clear()
    routines._paused_loaded = False


def _make_routine(name):
    """Untrusted routine: note, destructive email, note. In
    confirm-risky the email step parks and the routine pauses."""
    approval.tool_set_approval_mode("auto")
    routines.tool_routine_record_start(name)
    dispatch.execute_tool("fake_note", {"text": "one"})
    dispatch.execute_tool(
        "send_email",
        {"to": "a@b.c", "subject": "s", "body": "b"})
    dispatch.execute_tool("fake_note", {"text": "three"})
    summary = routines.tool_routine_record_stop()
    assert "3 step" in summary, summary
    # Recording and replaying share the dispatch turn context:
    # without a reset the replay's first step is duplicate-blocked
    # (test_routine_resume.py's _fresh() does the same).
    dispatch.reset_turn_state()
    approval.tool_set_approval_mode("confirm-risky")


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
    assert missions._mission_row(mid)["plan_approved"] is True


def _pump(mid, want, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        missions.executor_tick()
        row = missions._mission_row(mid)
        if row["status"] in want:
            return row
        time.sleep(0.05)
    return missions._mission_row(mid)


def _token(text):
    m = re.search(r"token=([0-9a-f]{8})", text)
    assert m, f"no token in: {text[:200]}"
    return m.group(1)


def _run_mission_to_inner_park(env):
    _make_routine("r24")
    CALLS.clear()  # recording itself called every step once
    mid = _create_mission(
        "routine nested approval",
        [("run_routine", {"name": "r24"})],
        # The routine's inner tools are nested dispatch calls under
        # the mission envelope: they must be allowed too, or the
        # envelope gate parks them first (the designed boundary).
        # send_email stays irreversible without pre-approval, so
        # the routine's own gate still parks it in confirm-risky.
        allowed_tools="run_routine,fake_note,send_email")
    _approve_plan(mid)
    row = _pump(mid, {"waiting_approval"})
    assert row["status"] == "waiting_approval", row
    steps = missions._step_rows(mid)
    assert steps[0]["status"] == "waiting", steps
    assert steps[0]["approval_token"], steps
    assert len([c for c in CALLS if c[0] == "fake_note"]) == 1, CALLS
    assert [c for c in CALLS if c[0] == "send_email"] == [], CALLS
    return mid, steps[0]["approval_token"]


def test_nested_approval_settles_parent_mission(env):
    mid, tok = _run_mission_to_inner_park(env)
    result = approval.approve_token(tok)
    assert "email sent to a@b.c" in result, result
    assert "noted three" in result, result  # routine resumed
    row = _pump(mid, {"done"})
    assert row["status"] == "done", row
    steps = missions._step_rows(mid)
    assert steps[0]["status"] == "done", steps
    assert len([c for c in CALLS if c[0] == "fake_note"]) == 2, CALLS
    assert len([c for c in CALLS if c[0] == "send_email"]) == 1, CALLS
    assert approval.pending_entries() == []


def test_nested_denial_fails_parent_mission(env):
    mid, tok = _run_mission_to_inner_park(env)
    approval.deny_token(tok)
    row = missions._mission_row(mid)
    assert row["status"] == "failed", row
    assert "denied" in (row["summary"] or ""), row
    assert [c for c in CALLS if c[0] == "send_email"] == [], CALLS
    assert len([c for c in CALLS if c[0] == "fake_note"]) == 1, CALLS


def test_paused_routine_return_carries_marker_first(env):
    _make_routine("r24b")
    out = routines.tool_run_routine("r24b")
    assert out.startswith("[AWAITING_APPROVAL token="), out[:120]
    assert "PAUSED at step 2" in out, out
    # The typed status is unchanged for every other consumer.
    assert outcome_mod.normalize(out).status == "ask", out[:120]
    # The token in the marker is the token the step parked on.
    assert _token(out) in [e["token"]
                           for e in approval.pending_entries()]
