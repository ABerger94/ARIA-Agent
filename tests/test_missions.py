"""Durable Mission Layer v1 (Alek's approved design, 2026-10-31).

Pins the contract the design was approved on:

  (a) a restart mid-mission never replays a completed step —
      the executor resumes at the first unfinished step, and a
      parked step whose in-memory approval died with the old
      process is re-parked fresh (approval store reconciliation);
  (b) a step outside the authorization envelope parks in the
      EXISTING approval store even in auto mode, and resolving
      it through approve_token executes it exactly once and
      checkpoints its real output; denying it fails the mission
      without executing;
  (c) safe mode hard-blocks its usual tools inside a mission;
  (d) a mission created (and plan-approved) before a restart
      completes after it — all durable state is in the SQLite
      store, the executor is stateless;
  plus the plan-approval gate itself, pause/resume/cancel, the
  verify pass closing done_unverified after 2 repairs, and the
  bridge Missions section reading the same store.

Harness: real aria.missions, real aria.approval, real
dispatch.execute_tool; two fake tools registered into the real
registry (fake_note, and a recording send_email override —
send_email is the real destructive + safe-mode-blocked name
whose policy shape the tests need). Peripheral subsystems are
stubbed at the sys.modules seam, as in
tests/test_approve_replay_dupguard.py. HOME / install root /
DB are all temp.
"""
import http.client
import importlib
import json
import os
import sys
import tempfile
import threading
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
bridge = importlib.import_module("aria.bridge")

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
    """Drive executor ticks until the mission reaches one of
    the wanted statuses (or time runs out)."""
    end = time.time() + timeout
    while time.time() < end:
        missions.executor_tick()
        row = _row(mid)
        if row["status"] in want:
            return row
        time.sleep(0.05)
    return _row(mid)


def _calls(tool):
    return [c for c in CALLS if c[0] == tool]


# -- the plan gate ------------------------------------------------

def test_plan_gate_blocks_execution_until_approved(env):
    mid, out = _create("gate check",
                       [("fake_note", {"text": "hi"})])
    assert "AWAITING_APPROVAL" in out
    row = _pump(mid, {"planned"}, timeout=2)
    assert row["status"] == "planned"
    assert _calls("fake_note") == []
    # Deny at the gate: mission cancelled, never ran.
    approval.deny_token(row["plan_token"])
    row = _row(mid)
    assert row["status"] == "cancelled"
    assert _calls("fake_note") == []


# -- (d) created + approved pre-restart, completes post-restart --

def test_mission_completes_after_restart(env):
    mid, _out = _create("restart proof",
                        [("fake_note", {"text": "one"}),
                         ("fake_note", {"text": "two"})])
    _approve_plan(mid)
    # Simulate a process restart: every in-memory structure
    # dies (approval store, worker flag); only SQLite survives.
    approval._PENDING.clear()
    missions._worker_alive = False
    row = _pump(mid, {"done"})
    assert row["status"] == "done", row
    assert len(_calls("fake_note")) == 2
    steps = missions._step_rows(mid)
    assert [s["status"] for s in steps] == ["done", "done"]
    assert "restart proof" in (row["summary"] or "")
    assert "Done: 2/2" in row["summary"]


# -- (a)+(b) parked step, restart, re-park, exactly-once --------

def test_restart_resume_never_replays_completed_steps(env):
    mid, _out = _create(
        "resume proof",
        [("fake_note", {"text": "first"}),
         ("send_email", {"to": "a@b.c", "subject": "s",
                         "body": "b"}),
         ("fake_note", {"text": "third"})])
    _approve_plan(mid)
    # send_email is irreversible and NOT pre-approved: it parks
    # even in auto mode (the envelope fork).
    row = _pump(mid, {"waiting_approval"})
    assert row["status"] == "waiting_approval", row
    assert len(_calls("fake_note")) == 1
    assert _calls("send_email") == []
    steps = missions._step_rows(mid)
    assert steps[0]["status"] == "done"
    assert steps[1]["status"] == "waiting"
    dead_token = steps[1]["approval_token"]
    assert dead_token
    # Restart: the in-memory approval store dies with the
    # process; the parked token is unresolvable.
    approval._PENDING.clear()
    missions._worker_alive = False
    # The next tick reconciles: step re-parks on a FRESH token,
    # step 1 is NOT replayed.
    row = _pump(mid, {"waiting_approval"})
    assert row["status"] == "waiting_approval", row
    steps = missions._step_rows(mid)
    assert steps[1]["status"] == "waiting"
    assert steps[1]["approval_token"] not in (None, dead_token)
    assert len(_calls("fake_note")) == 1
    # Alek taps Confirm on the fresh entry: the step executes
    # exactly once and its output is checkpointed.
    result = approval.approve_token(steps[1]["approval_token"])
    assert "email sent to a@b.c" in result, result
    assert len(_calls("send_email")) == 1
    steps = missions._step_rows(mid)
    assert steps[1]["status"] == "done"
    assert "email sent to a@b.c" in (steps[1]["output"] or "")
    # Mission resumes and finishes; nothing replayed.
    row = _pump(mid, {"done"})
    assert row["status"] == "done", row
    assert len(_calls("fake_note")) == 2
    assert len(_calls("send_email")) == 1


def test_out_of_envelope_parks_in_auto_and_deny_fails(env):
    mid, _out = _create(
        "envelope proof",
        [("send_email", {"to": "x@y.z", "subject": "s",
                         "body": "b"})],
        allowed_tools="fake_note")
    _approve_plan(mid)
    row = _pump(mid, {"waiting_approval"})
    assert row["status"] == "waiting_approval", row
    assert _calls("send_email") == []
    steps = missions._step_rows(mid)
    approval.deny_token(steps[0]["approval_token"])
    row = _row(mid)
    assert row["status"] == "failed"
    assert "denied" in (row["summary"] or "")
    assert _calls("send_email") == []


def test_preapproved_irreversible_runs_unasked_in_auto(env):
    mid, _out = _create(
        "preapproved proof",
        [("send_email", {"to": "x@y.z", "subject": "s",
                         "body": "b"})],
        pre_approved_irreversible="send_email")
    _approve_plan(mid)
    row = _pump(mid, {"done"})
    assert row["status"] == "done", row
    assert len(_calls("send_email")) == 1
    assert approval.pending_entries() == []


# -- (c) safe mode -------------------------------------------------

def test_safe_mode_blocks_blocked_tool_inside_mission(env):
    _SAFE["on"] = True
    mid, _out = _create(
        "safe mode proof",
        [("send_email", {"to": "x@y.z", "subject": "s",
                         "body": "b"})],
        pre_approved_irreversible="send_email")
    _approve_plan(mid)
    row = _pump(mid, {"failed"})
    assert row["status"] == "failed", row
    assert "safe mode" in (row["summary"] or "").lower()
    assert _calls("send_email") == []


# -- pause / resume / cancel ---------------------------------------

def test_pause_resume_and_cancel(env):
    mid, _out = _create("pause proof",
                        [("fake_note", {"text": "later"})])
    _approve_plan(mid)
    assert "paused" in missions.tool_mission_pause(
        {"mission_id": mid})
    row = _pump(mid, {"paused"}, timeout=2)
    assert row["status"] == "paused"
    assert _calls("fake_note") == []
    assert "resumed" in missions.tool_mission_pause(
        {"mission_id": mid})
    row = _pump(mid, {"done"})
    assert row["status"] == "done", row
    assert len(_calls("fake_note")) == 1

    mid2, _out2 = _create(
        "cancel proof",
        [("send_email", {"to": "x@y.z", "subject": "s",
                         "body": "b"})])
    _approve_plan(mid2)
    row = _pump(mid2, {"waiting_approval"})
    assert row["status"] == "waiting_approval", row
    tok = missions._step_rows(mid2)[0]["approval_token"]
    assert "cancelled" in missions.tool_mission_cancel(
        {"mission_id": mid2})
    row = _row(mid2)
    assert row["status"] == "cancelled"
    assert all(e["token"] != tok
               for e in approval.pending_entries())
    assert _calls("send_email") == []


# -- verification ---------------------------------------------------

def test_verify_failure_closes_done_unverified(env):
    missions.set_verifier(lambda goal, steps: False)
    mid, _out = _create("verify proof",
                        [("fake_note", {"text": "only"})])
    _approve_plan(mid)
    row = _pump(mid, {"done_unverified"})
    assert row["status"] == "done_unverified", row
    assert "did not pass" in (row["summary"] or "")
    # 1 initial run + 2 repair re-runs of the last step.
    assert len(_calls("fake_note")) == 3


def test_mission_tools_reject_bad_input(env):
    out = missions.tool_mission_create(
        {"goal": "x", "steps": "not json"})
    assert out.startswith("[Mission error"), out
    out = missions.tool_mission_create(
        {"goal": "x", "steps": json.dumps(
            [{"tool": "mission_create", "args": {}}])})
    assert "cannot spawn" in out or "missions cannot" in out
    assert missions.tool_mission_list({}) == "No missions yet."
    assert "No mission" in missions.tool_mission_pause(
        {"mission_id": 999})
    assert "No mission" in missions.tool_mission_cancel(
        {"mission_id": 999})


# -- bridge Missions section ----------------------------------------

_TOKEN = "mission-test-token"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(data_dir, "_ROOT", str(tmp_path / "ARIA"))
    monkeypatch.setattr(memory, "DB_PATH",
                        str(tmp_path / "bridge.db"))
    memory.init_databases()
    monkeypatch.setattr(config, "is_safe_mode", lambda: False)
    bridge.BRIDGE_TOKEN = _TOKEN
    bridge.WORKSPACE_DIR = tempfile.mkdtemp(prefix="aria_mws_")
    bridge.lan_ip = lambda: "192.168.99.10"
    bridge.get_setting = lambda key, default=None: default
    bridge.BRIDGE_SCHEME = "http"
    bridge.BRIDGE_CERT = None
    bridge.BRIDGE_KEY = None
    approval._PENDING.clear()
    approval.set_mode("auto")
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge.BridgeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield types.SimpleNamespace(port=srv.server_address[1])
    srv.shutdown()
    srv.server_close()
    approval._PENDING.clear()


def _req(port, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, headers=dict(headers or {}))
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def test_bridge_missions_section_reads_store(server):
    status, body = _req(server.port, "GET", "/api/missions")
    assert status == 401, (status, body)
    mid, _out = _create("bridge proof",
                        [("fake_note", {"text": "hi"})])
    _approve_plan(mid)
    row = _pump(mid, {"done"})
    assert row["status"] == "done", row
    status, body = _req(server.port, "GET", "/api/missions",
                        {"X-Bridge-Token": _TOKEN})
    assert status == 200, (status, body)
    data = json.loads(body)["missions"]
    mine = [m for m in data if m["id"] == mid]
    assert mine and mine[0]["status"] == "done"
    assert mine[0]["done"] == 1 and mine[0]["total"] == 1
    assert "bridge proof" in mine[0]["summary"]
    # The phone page carries the section + its loader.
    page = (Path(__file__).parents[1] / "aria" / "assets" /
            "phone.html").read_text(encoding="utf-8")
    assert 'id="missions"' in page
    assert "/api/missions" in page
