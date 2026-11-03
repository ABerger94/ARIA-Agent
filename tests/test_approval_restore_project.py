"""Pass 19 (ChatGPT x ARIA loop, 2026-11-03): restored approvals
must execute in the project they were parked in.

Verified against the code before fixing:
- request_approval() stores an execution_context whose project
  is the parked call's origin project, and approve_token()
  replays under exactly that project (aria/approval.py).
- But _save_pending() persisted only token/tool/args/desc/
  created_ts, and load_pending() rebuilt the context from
  runtime.current() — whichever project was active when the
  store was first loaded after a restart. The pass-18 docstring
  even documented this as intended ("replays under today's
  project/cancel/budget, exactly like a new park").
- So parking write_file("report.md") in project A, switching
  the active project to B, restarting, and tapping Confirm
  wrote B's report.md — and, because the mission DB path is
  project-bound (aria/memory.py db_path via workspaces.path),
  a parked mission-plan approval for A's mission #N could
  approve B's mission #N instead. The banner description does
  not name a project, so the tapper could not tell.

Fix contract pinned here:
- _save_pending() persists the parked execution context
  (project, budget scope/cap, operation id) alongside each
  entry; load_pending() restores it verbatim and approve_token()
  replays under it, matching in-memory entries.
- Only the cancel event is fresh (it cannot cross a restart).
- Entries with no recorded project (pre-fix format) and
  entries whose project no longer exists are NOT restored:
  each is journaled as 'expired' with the reason, and never
  executed — no guessing a project for an old entry.

Run per-file (own process), like every suite in this repo.
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
runtime = importlib.import_module("aria.runtime")
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


def _simulate_restart():
    """Drop the in-memory store the way a process restart does;
    the persisted file (under the fixture's data root) survives."""
    approval._PENDING.clear()
    approval._RESOLUTIONS.clear()
    approval._pending_loaded = False


def _saved_entries():
    with open(approval._pending_file(), encoding="utf-8") as f:
        return json.load(f)["entries"]


def test_restored_approval_replays_in_origin_project(state, monkeypatch):
    seen = {}

    def _handler(args):
        seen["project"] = workspaces.current()
        seen["runtime_project"] = runtime.current().project
        return "stub: ok"

    monkeypatch.setitem(dispatch._REGISTRY, "send_email", _handler)
    proj_a = workspaces.create("Project A")
    proj_b = workspaces.create("Project B")
    with workspaces.use(proj_a):
        approval.request_approval("send_email", {"to": "a@example.com", "subject": "hi", "body": "hello"},
                                  desc="send email to 'a@example.com'")
    (token,) = list(approval._PENDING)
    assert approval._PENDING[token]["execution_context"]["project"] == proj_a
    assert _saved_entries()[0]["project"] == proj_a

    # Restart, with project B now the active project.
    _simulate_restart()
    workspaces.select(proj_b)
    entries = approval.pending_entries()
    assert [e["token"] for e in entries] == [token]
    assert approval._PENDING[token]["execution_context"]["project"] == proj_a

    out = approval.approve_token(token)
    assert "stub: ok" in out
    assert seen["project"] == proj_a
    assert seen["runtime_project"] == proj_a
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "executed"


def test_legacy_entry_without_project_is_expired_not_restored(state):
    data_dir.write_json_atomic(
        approval._pending_file(),
        {"version": 1, "entries": [{
            "token": "legacy-token", "tool": "send_email",
            "args": {"to": "a@example.com", "subject": "hi", "body": "hello"}, "desc": "legacy",
            "created_ts": __import__("time").time()}]})
    _simulate_restart()
    assert approval.pending_entries() == []
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "expired"
    assert "not executed" in rec["detail"]
    assert "project" in rec["detail"]


def test_entry_for_deleted_project_is_expired_not_restored(state, monkeypatch):
    monkeypatch.setitem(dispatch._REGISTRY, "send_email",
                        lambda args: "stub: ok")
    proj_a = workspaces.create("Project A")
    with workspaces.use(proj_a):
        approval.request_approval("send_email", {"to": "a@example.com", "subject": "hi", "body": "hello"},
                                  desc="send email to 'a@example.com'")
    (token,) = list(approval._PENDING)
    # Delete the project from the registry behind the entry's back.
    manifest = Path(data_dir.path("workspaces.json"))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["projects"][proj_a]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    _simulate_restart()
    assert approval.pending_entries() == []
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "expired"
    assert "no longer exists" in rec["detail"]
