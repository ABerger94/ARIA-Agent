"""Pass 26 B1#6: concurrent pending-store writes must not resurrect
spent approvals.

Verified against the code at b1ba4ad:
- approval.py has NO lock: _save_pending() snapshots _PENDING and
  then write_json_atomic()s it (atomic-replace only, data_dir.py),
  while request_approval / approve_token / deny_token /
  purge_expired / load_pending run on different threads — so a
  snapshot taken before a resolve can be written after it,
  re-persisting a spent token that load_pending() restores after
  a restart (within the 10-minute expiry).
- Directly parked requests have operation_id '' (runtime has no
  active operation), so dispatch mints a fresh uuid receipt id
  per replay (dispatch.py:630) and the receipt journal cannot
  dedup replays of the same parked call.

Fix contract pinned here:
- all _PENDING mutations and _save_pending run under one
  module-level threading.RLock (snapshot + write inside the lock;
  never held while executing handlers or notifying listeners);
- a stable operation id (uuid) is assigned at park time for
  directly parked requests, so replays share one receipt id;
- an explicit active operation id is kept as-is.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import json
import os
import sys
import threading
import time
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


def test_operation_id_stable_across_save_load(state):
    proj = workspaces.create("Project A")
    with workspaces.use(proj):
        approval.request_approval("send_email",
                                  {"to": "a@example.com"}, desc="mail A")
        approval.request_approval("send_email",
                                  {"to": "b@example.com"}, desc="mail B")
    tokens = list(approval._PENDING)
    op_ids = [approval._PENDING[t]["execution_context"]["operation_id"]
              for t in tokens]
    # Directly parked: a stable, non-empty id each — not ''.
    assert all(op_ids), op_ids
    assert len(set(op_ids)) == 2, op_ids
    saved = {e["token"]: e["operation_id"] for e in _saved_entries()}
    assert saved == dict(zip(tokens, op_ids))

    _simulate_restart()
    approval.pending_entries()
    assert [approval._PENDING[t]["execution_context"]["operation_id"]
            for t in tokens] == op_ids

    # An explicit active operation id is kept as-is, not replaced.
    with workspaces.use(proj):
        with runtime.operation("fixed-op-123"):
            approval.request_approval("send_email",
                                      {"to": "c@example.com"}, desc="mail C")
    (third,) = [t for t in approval._PENDING if t not in tokens]
    assert approval._PENDING[third]["execution_context"]["operation_id"] \
        == "fixed-op-123"


def test_stale_snapshot_cannot_resurrect_denied_token(state, monkeypatch):
    """Deterministic interleaving of the snapshot race: thread 1
    parks (its _save_pending snapshot is taken, then its write is
    held back); the deny thread resolves the token while the write
    is held; the held write must NOT re-persist the spent token.
    Without the store lock the held write lands after the deny's
    write and the token comes back after a restart."""
    real_write = data_dir.write_json_atomic
    calls = {"n": 0}
    entered = threading.Event()
    release = threading.Event()
    pending_file = approval._pending_file()

    def _slow_write(path, payload):
        # Only the pending store's own save is held back — other
        # writers (workspaces manifest, mode file) pass through.
        if str(path) != str(pending_file):
            return real_write(path, payload)
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            # Held back until the main thread lets go (or 10s —
            # the post-fix run blocks the deny on the lock, so the
            # wait must time out rather than deadlock).
            release.wait(timeout=10)
        return real_write(path, payload)

    monkeypatch.setattr(data_dir, "write_json_atomic", _slow_write)
    # Create the project BEFORE the patch: workspaces.create
    # writes its manifest through the same function.
    proj = workspaces.create("Project A")
    parked = {}

    def _park():
        with workspaces.use(proj):
            approval.request_approval(
                "send_email", {"to": "a@example.com"}, desc="mail A")
        parked["token"] = next(iter(approval._PENDING))

    t = threading.Thread(target=_park)
    t.start()
    assert entered.wait(timeout=10), "park never reached its save"
    denied = {}

    def _deny():
        # The token is in _PENDING before request_approval's save
        # runs (the save is what the park thread is blocked in),
        # so read it from the live store, not from the parked
        # thread's return value.
        token = next(iter(approval._PENDING))
        denied["out"] = approval.deny_token(token)

    d = threading.Thread(target=_deny)
    d.start()
    # Give the deny a chance to run while the park's write is held.
    time.sleep(0.5)
    release.set()
    t.join(timeout=10)
    d.join(timeout=10)
    assert not t.is_alive() and not d.is_alive()
    assert "Denied and dropped" in denied["out"], denied
    assert approval._PENDING == {}

    _simulate_restart()
    assert approval.pending_entries() == [], \
        "a denied token was resurrected by a stale snapshot write"
    assert _saved_entries() == []
