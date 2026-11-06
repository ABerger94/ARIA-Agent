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
