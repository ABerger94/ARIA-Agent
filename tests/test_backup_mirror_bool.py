"""Pass 26 B3#1: a string "false" must not clear backup mirroring
without approval.

Verified against the code at b1ba4ad:
- approval.py _as_bool("false") is False, so needs_approval()
  returns False for set_backup_mirror {"clear": "false"} and the
  call never parks; but backup_mirror.py tool_set_backup_mirror
  does `if params.get("clear"):` — the non-empty string is truthy
  — and clears the mirror, reporting status=done. The schema
  declares clear BOOLEAN but dispatch passes args uncoerced, so
  the two seams disagree.

Fix contract pinned here (the file_organize dry_run precedent
in the same function: reject non-bool outright):
- the handler clears ONLY on a literal bool True; a literal
  bool False (or absent clear) is the status report; any other
  present value is a failed outcome and clears nothing — even
  on an approved replay;
- needs_approval() treats a present non-False clear value as
  gating, so a malformed clear parks like file_organize's
  malformed dry_run instead of running ungated.

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
bm = importlib.import_module("aria.backup_mirror")
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


def _stub_settings(monkeypatch, store):
    monkeypatch.setattr(
        bm, "_get_setting",
        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(
        bm, "_set_setting",
        lambda key, value: store.__setitem__(key, value))
    return store


def test_malformed_clear_values_do_not_clear(state, monkeypatch):
    store = _stub_settings(monkeypatch, {"backup_mirror_dir": "/mirror"})
    for bad in ("false", "0", 0, 1, "yes", None):
        out = bm.tool_set_backup_mirror({"clear": bad})
        assert out.startswith("status=failed"), (bad, out)
        assert store["backup_mirror_dir"] == "/mirror", bad


def test_bool_clear_still_clears_and_bool_false_reports(state, monkeypatch):
    store = _stub_settings(monkeypatch, {"backup_mirror_dir": "/mirror"})
    out = bm.tool_set_backup_mirror({"clear": False})
    assert out.startswith("status=done"), out
    assert store["backup_mirror_dir"] == "/mirror"
    out = bm.tool_set_backup_mirror({"clear": True})
    assert out.startswith("status=done"), out
    assert store["backup_mirror_dir"] == ""


def test_malformed_clear_rejected_before_approval_and_never_clears(
        state, monkeypatch):
    store = _stub_settings(monkeypatch, {"backup_mirror_dir": "/mirror"})
    # needs_approval still treats a present non-False clear value
    # as gating (a malformed clear looks like a clear attempt) …
    assert approval.needs_approval("set_backup_mirror", {"clear": "false"})
    assert approval.needs_approval("set_backup_mirror", {"clear": "0"})
    assert approval.needs_approval("set_backup_mirror", {"clear": 0})
    assert approval.needs_approval("set_backup_mirror", {"clear": True})
    # … while a literal False / no-arg call stays a status report.
    assert not approval.needs_approval("set_backup_mirror",
                                        {"clear": False})
    assert not approval.needs_approval("set_backup_mirror", {})

    # … but dispatch's argument validation now rejects a
    # wrong-typed clear BEFORE any approval gate runs (the
    # file_organize dry_run precedent the fix contract cites):
    # nothing parks, nothing clears, and the store is untouched.
    proj = workspaces.create("Project A")
    with workspaces.use(proj):
        res, _ok = dispatch.execute_tool(
            "set_backup_mirror", {"clear": "false"})
    assert res.startswith("[Argument error"), res
    assert "wrong-typed value for clear" in res, res
    assert store["backup_mirror_dir"] == "/mirror"
    assert not approval._PENDING
    # Even an approved replay of the malformed call still fails
    # without clearing — the handler contract holds under dispatch
    # too, and no token is ever minted for it.
    with workspaces.use(proj):
        res, _ok = dispatch.execute_tool(
            "set_backup_mirror", {"clear": "false"}, preauthorized=True)
    assert res.startswith("[Argument error"), res
    assert store["backup_mirror_dir"] == "/mirror"

    # Control: a real bool clear still clears after approval.
    with workspaces.use(proj):
        res, _ok = dispatch.execute_tool(
            "set_backup_mirror", {"clear": True})
    assert res.startswith("[AWAITING_APPROVAL token="), res
    (token,) = list(approval._PENDING)
    out = approval.approve_token(token)
    assert "status=done" in out, out
    assert store["backup_mirror_dir"] == ""
