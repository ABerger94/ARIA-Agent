"""Pass 26 B2#4: an approved failure result must not resume the
routine that paused on it.

Verified against the code at b1ba4ad:
- approve_token() (approval.py:1026-1056) classifies replay
  results by string prefix only; typed `status=failed` results
  (outcome.py:217-228, dispatch.py:425,436) land in the
  "executed" else-branch and call maybe_resume_for_token — so a
  routine step that FAILED on replay still triggers the rest of
  the routine. The _ok flag from execute_tool is never consulted.

Fix contract pinned here:
- a replay result that is a typed failure (outcome.is_failure /
  kind_of == FAILED) is classified exactly like the
  "[Tool Error:" branch: journalled "errored", no resume;
- a typed success (status=done) still resumes, as before.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

for _name in ("aria.speech", "aria.hud",
              "aria.frame", "aria.vision", "aria.hardware",
              "aria.agent.taskqueue"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

_spotify_stub = types.ModuleType("aria.spotify")
_spotify_stub.tool_media_key = lambda args=None: ""
_spotify_stub.tool_spotify = lambda args=None: ""
sys.modules.setdefault("aria.spotify", _spotify_stub)

import pytest

approval = importlib.import_module("aria.approval")
config = importlib.import_module("aria.config")
data_dir = importlib.import_module("aria.data_dir")
dispatch = importlib.import_module("aria.tools.dispatch")
outcome = importlib.import_module("aria.outcome")
routines = importlib.import_module("aria.routines")
workspaces = importlib.import_module("aria.workspaces")


@pytest.fixture()
def state(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "ARIA").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(data_dir, "_ROOT", str(h / "ARIA"))
    logs = []
    monkeypatch.setattr(
        config, "add_log",
        lambda msg, *a, **k: logs.append(str(msg)))
    approval._PENDING.clear()
    approval._RESOLUTIONS.clear()
    approval._pending_loaded = False
    dispatch.reset_turn_state()
    approval.set_mode("confirm-risky")
    yield types.SimpleNamespace(dir=h, logs=logs)
    approval._PENDING.clear()
    approval._RESOLUTIONS.clear()
    approval._pending_loaded = False
    dispatch.reset_turn_state()
    routines._PAUSED.clear()


def _pause_routine_on(token, monkeypatch):
    """A routine paused on `token`, with the revision guard and
    the resume executor stubbed so the test observes exactly
    whether approve_token attempted a resume (the subject under
    test is approve_token's branch, not the revision guard)."""
    calls = []
    monkeypatch.setattr(routines, "_check_resume", lambda record: "")
    monkeypatch.setattr(routines, "run_routine_impl",
                        lambda name, params, start_at=1: (
                            calls.append((name, start_at)),
                            "resumed-ok")[1])
    routines._load_paused()
    routines._PAUSED[str(token)] = {
        "name": "probe_routine", "revision": "test",
        "next_step": 2, "project": "default", "params": {},
        "token": str(token)}
    routines._save_paused()
    return calls


def test_failed_replay_does_not_resume_routine(state, monkeypatch):
    monkeypatch.setitem(
        dispatch._REGISTRY, "probe_step",
        lambda args: outcome.failed("probe_step", "boom"))
    proj = workspaces.create("Project A")
    with workspaces.use(proj):
        approval.request_approval("probe_step", {"x": 1},
                                  desc="probe step")
    (token,) = list(approval._PENDING)
    calls = _pause_routine_on(token, monkeypatch)

    out = approval.approve_token(token)
    assert "status=failed" in out, out
    assert calls == [], calls
    # The routine is still paused on the token — nothing resumed
    # it, and the pause was not consumed.
    assert str(token) in routines._PAUSED
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "errored", rec


def test_done_replay_still_resumes_routine(state, monkeypatch):
    monkeypatch.setitem(
        dispatch._REGISTRY, "probe_step",
        lambda args: outcome.done("probe_step"))
    proj = workspaces.create("Project A")
    with workspaces.use(proj):
        approval.request_approval("probe_step", {"x": 1},
                                  desc="probe step")
    (token,) = list(approval._PENDING)
    calls = _pause_routine_on(token, monkeypatch)

    out = approval.approve_token(token)
    assert "status=done" in out, out
    assert calls == [("probe_routine", 2)], calls
    assert str(token) not in routines._PAUSED
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "executed", rec


def _pause_routine_returning(token, monkeypatch, resume_text):
    """Like _pause_routine_on, but the resumed routine returns
    `resume_text` — the continuation's own outcome, which the
    token's return must carry instead of hiding behind the first
    step's success (pass 28 #7)."""
    calls = []
    monkeypatch.setattr(routines, "_check_resume", lambda record: "")
    monkeypatch.setattr(routines, "run_routine_impl",
                        lambda name, params, start_at=1: (
                            calls.append((name, start_at)),
                            resume_text)[1])
    routines._load_paused()
    routines._PAUSED[str(token)] = {
        "name": "probe_routine", "revision": "test",
        "next_step": 2, "project": "default", "params": {},
        "token": str(token)}
    routines._save_paused()
    return calls


def test_unverified_replay_does_not_resume_routine(state, monkeypatch):
    # Pass 28 #6: an UNVERIFIED replay (e.g. a timeout — the call
    # may have acted) is not a completion. The routine must stay
    # paused, and the token's return must keep the uncertain
    # status instead of reading as done.
    monkeypatch.setitem(
        dispatch._REGISTRY, "probe_step",
        lambda args: outcome.unverified(
            "probe_step", "timed out — the server may have acted"))
    proj = workspaces.create("Project A")
    with workspaces.use(proj):
        approval.request_approval("probe_step", {"x": 1},
                                  desc="probe step")
    (token,) = list(approval._PENDING)
    calls = _pause_routine_on(token, monkeypatch)

    out = approval.approve_token(token)
    assert outcome.normalize(out).status == "unverified", out
    assert calls == [], calls
    assert str(token) in routines._PAUSED
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "uncertain", rec


def test_started_replay_does_not_resume_routine(state, monkeypatch):
    # Pass 28 #6: a STARTED replay (work under way, no result
    # yet) is not a completion either.
    monkeypatch.setitem(
        dispatch._REGISTRY, "probe_step",
        lambda args: outcome.started("probe_step"))
    proj = workspaces.create("Project A")
    with workspaces.use(proj):
        approval.request_approval("probe_step", {"x": 1},
                                  desc="probe step")
    (token,) = list(approval._PENDING)
    calls = _pause_routine_on(token, monkeypatch)

    out = approval.approve_token(token)
    assert outcome.normalize(out).status == "started", out
    assert calls == [], calls
    assert str(token) in routines._PAUSED
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "uncertain", rec


def test_refused_replay_normalizes_failed_and_does_not_resume(state, monkeypatch):
    # Pass 28 #8: an approval-refusal result ("[Approval
    # refused: ...]") used to normalize as DONE — the prefix
    # classifier had no refusal branch, so the replay journalled
    # "executed" and resumed the routine that paused on it.
    monkeypatch.setitem(
        dispatch._REGISTRY, "probe_step",
        lambda args: ("[Approval refused: the foreground window "
                      "changed since this was approved — it was "
                      "'Notepad' and is now 'Chrome', so the "
                      "probe_step was NOT executed. Ask again with "
                      "the current screen.]"))
    proj = workspaces.create("Project A")
    with workspaces.use(proj):
        approval.request_approval("probe_step", {"x": 1},
                                  desc="probe step")
    (token,) = list(approval._PENDING)
    calls = _pause_routine_on(token, monkeypatch)

    out = approval.approve_token(token)
    assert outcome.normalize(out).status == "failed", out
    assert calls == [], calls
    assert str(token) in routines._PAUSED
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "refused", rec


def test_resumed_routine_failure_surfaces_in_token_status(state, monkeypatch):
    # Pass 28 #7: the first step succeeded, but the resumed
    # routine failed — the token's return must carry the
    # continuation's FAILED status, not read as a success with
    # the failure buried in appended text.
    monkeypatch.setitem(
        dispatch._REGISTRY, "probe_step",
        lambda args: outcome.done("probe_step"))
    proj = workspaces.create("Project A")
    with workspaces.use(proj):
        approval.request_approval("probe_step", {"x": 1},
                                  desc="probe step")
    (token,) = list(approval._PENDING)
    calls = _pause_routine_returning(
        token, monkeypatch, "[Tool Error: resume step failed]")
    out = approval.approve_token(token)
    assert calls == [("probe_routine", 2)], calls
    assert "resume step failed" in out, out
    assert outcome.normalize(out).status == "failed", out
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "executed", rec


def test_resumed_routine_repark_surfaces_as_ask(state, monkeypatch):
    # Pass 28 #7, second face: the resumed routine re-parked
    # behind a fresh approval — the token's return must read as
    # ASK (a new token is waiting), not as the first step's
    # success.
    monkeypatch.setitem(
        dispatch._REGISTRY, "probe_step",
        lambda args: outcome.done("probe_step"))
    proj = workspaces.create("Project A")
    with workspaces.use(proj):
        approval.request_approval("probe_step", {"x": 1},
                                  desc="probe step")
    (token,) = list(approval._PENDING)
    calls = _pause_routine_returning(
        token, monkeypatch,
        "[AWAITING_APPROVAL token=12345678] next step needs approval")
    out = approval.approve_token(token)
    assert calls == [("probe_routine", 2)], calls
    assert outcome.normalize(out).status == "ask", out
