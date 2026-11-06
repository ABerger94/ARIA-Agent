"""Pass 26 B1#2: delayed approvals must keep the mission envelope
(approval side).

Verified against the code at b1ba4ad:
- approval.py request_approval() captures project / cancel /
  operation_id / budget_scope / spend_cap into the pending
  entry's execution_context, but NOT ctx.envelope; _save_pending()
  persists only project / operation_id / budget_scope / spend_cap.
- approve_token() replays under runtime.execution without an
  envelope, and runtime.execution only inherits an envelope from
  a parent that has one (runtime.py:91-92) — so a mission step
  parked for approval and confirmed after the mission's own
  execution ended replays with NO envelope, and its nested calls
  escape the mission's allowed-tools / pre-approval checks.

Fix contract pinned here:
- request_approval() captures a deep copy of ctx.envelope into
  the entry's execution_context; _save_pending() persists it
  (when JSON-serializable) and load_pending() restores it;
  approve_token() passes it to runtime.execution;
- an old-format entry without a persisted envelope restores
  WITHOUT one — never filled in from a latest / default;
- the parked envelope is a snapshot: later mutation of the
  caller's envelope dict must not change the parked copy.

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

ENVELOPE = {"allowed_tools": ["send_email"],
            "pre_approved_irreversible": [],
            "mission_id": 7, "goal": "probe mission",
            "spend_cap": 5.0}


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


def test_parked_envelope_survives_restart_and_replay(state, monkeypatch):
    seen = {}

    def _handler(args):
        seen["envelope"] = runtime.current().envelope
        return "stub: ok"

    monkeypatch.setitem(dispatch._REGISTRY, "send_email", _handler)
    proj = workspaces.create("Project A")
    env = dict(ENVELOPE)
    env["allowed_tools"] = list(ENVELOPE["allowed_tools"])
    with workspaces.use(proj):
        with runtime.execution("mission", project=proj, envelope=env):
            approval.request_approval(
                "send_email",
                {"to": "a@example.com", "subject": "hi", "body": "hello"},
                desc="send email to 'a@example.com'")
    (token,) = list(approval._PENDING)
    # The parked envelope is a snapshot, not an alias.
    env["allowed_tools"].append("write_file")
    env["spend_cap"] = 999.0
    assert approval._PENDING[token]["execution_context"]["envelope"] == ENVELOPE
    assert _saved_entries()[0]["envelope"] == ENVELOPE

    _simulate_restart()
    entries = approval.pending_entries()
    assert [e["token"] for e in entries] == [token]
    assert approval._PENDING[token]["execution_context"]["envelope"] == ENVELOPE

    out = approval.approve_token(token)
    assert "stub: ok" in out
    assert seen["envelope"] == ENVELOPE, seen
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "executed"


def test_legacy_entry_without_envelope_restores_without_one(state, monkeypatch):
    seen = {}

    def _handler(args):
        seen["envelope"] = runtime.current().envelope
        return "stub: ok"

    monkeypatch.setitem(dispatch._REGISTRY, "send_email", _handler)
    proj = workspaces.create("Project A")
    # An old-format entry: project recorded, no envelope key.
    data_dir.write_json_atomic(
        approval._pending_file(),
        {"version": 2, "entries": [{
            "token": "legacy-env", "tool": "send_email",
            "args": {"to": "a@example.com", "subject": "hi", "body": "hello"},
            "desc": "legacy", "created_ts": __import__("time").time(),
            "project": proj, "operation_id": "op-legacy",
            "budget_scope": "", "spend_cap": None}]})
    _simulate_restart()
    entries = approval.pending_entries()
    assert [e["token"] for e in entries] == ["legacy-env"]
    ctx = approval._PENDING["legacy-env"]["execution_context"]
    assert ctx.get("envelope") is None
    assert "envelope" not in ctx

    out = approval.approve_token("legacy-env")
    assert "stub: ok" in out
    assert seen["envelope"] is None, seen
