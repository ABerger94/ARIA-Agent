"""Pass 27 #13: GUI approval binding must be re-validated under the
desktop lock, at input delivery — not only before the lock wait.

Verified against the code at 4daf6d5:
- approve_token() checked the foreground window, then dispatch
  waited for DESKTOP_LOCK and the handler sent input without
  revalidation — focus could change while queued, and the click /
  typed text landed in a window the approval never covered.
- A coordinate click could also land on a different visible
  window (an always-on-top overlay) while the foreground window
  itself was unchanged.

Fix contract pinned here:
- approve_token carries the parked binding into the replay
  (approval._replay_local.gui_binding); dispatch re-validates it
  under DESKTOP_LOCK immediately before the handler runs, and
  refuses instead of sending input on mismatch;
- a gui_click additionally verifies the window under the click
  point (winctl.window_at_point) against the bound window;
- calls with no carried binding (ordinary / auto-mode) proceed
  exactly as before.

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
workspaces = importlib.import_module("aria.workspaces")

W1 = {"title": "Notepad", "process": "notepad.exe", "hwnd": 111}
W2 = {"title": "Calc", "process": "calc.exe", "hwnd": 222}


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
    approval._replay_local.gui_binding = None
    dispatch.reset_turn_state()


def _park_gui_click(monkeypatch, proj):
    monkeypatch.setattr(approval, "_gui_binding", lambda: dict(W1))
    with workspaces.use(proj):
        approval.request_approval("gui_click", {"x": 10, "y": 20},
                                  desc="click at (10, 20)")
    (token,) = list(approval._PENDING)
    return token


def test_replay_refuses_when_foreground_changes_after_approve_check(
        state, monkeypatch):
    """The gap itself: approve-time check sees W1, but by the
    time the handler would run under DESKTOP_LOCK the foreground
    is W2 — the click must not be sent."""
    calls = []
    monkeypatch.setitem(dispatch._REGISTRY, "gui_click",
                        lambda args: calls.append(dict(args)) or "clicked")
    proj = workspaces.create("Project A")
    token = _park_gui_click(monkeypatch, proj)
    seen = {"n": 0}

    def _binding():
        seen["n"] += 1
        # The park call ran under the earlier lambda; this fake
        # sees call 1 = approve_token's check, call 2 = the
        # dispatch re-check under the lock.
        return dict(W1) if seen["n"] == 1 else dict(W2)

    monkeypatch.setattr(approval, "_gui_binding", _binding)
    out = approval.approve_token(token)
    assert seen["n"] == 2, seen
    assert calls == [], calls
    assert "NOT executed" in out, out


def test_replay_clicks_when_foreground_unchanged(state, monkeypatch):
    calls = []
    monkeypatch.setitem(dispatch._REGISTRY, "gui_click",
                        lambda args: calls.append(dict(args)) or "clicked")
    proj = workspaces.create("Project A")
    token = _park_gui_click(monkeypatch, proj)
    monkeypatch.setattr(approval, "_gui_binding", lambda: dict(W1))
    out = approval.approve_token(token)
    assert calls == [{"x": 10, "y": 20}], calls
    assert "clicked" in out, out


def test_recheck_returns_none_without_carried_binding(monkeypatch):
    approval._replay_local.gui_binding = None
    assert approval.recheck_replay_gui_binding(
        "gui_click", {"x": 10, "y": 20}) is None


def test_recheck_refuses_when_foreground_unreadable_at_input(monkeypatch):
    approval._replay_local.gui_binding = dict(W1)
    monkeypatch.setattr(approval, "_gui_binding", lambda: None)
    out = approval.recheck_replay_gui_binding(
        "gui_click", {"x": 10, "y": 20})
    assert out is not None and "NOT executed" in out, out


def _fake_winctl(monkeypatch, at_point):
    fake = types.ModuleType("aria.tools.winctl")
    fake.window_at_point = lambda x, y: dict(at_point) if at_point else None
    monkeypatch.setitem(sys.modules, "aria.tools.winctl", fake)
    # `from aria.tools import winctl` resolves via the package
    # attribute when the real module was already imported, so
    # the sys.modules entry alone is bypassed — patch the
    # attribute too.
    import aria.tools as _tools_pkg
    monkeypatch.setattr(_tools_pkg, "winctl", fake)


def test_click_point_over_other_window_refuses(state, monkeypatch):
    approval._replay_local.gui_binding = dict(W1)
    monkeypatch.setattr(approval, "_gui_binding", lambda: dict(W1))
    _fake_winctl(monkeypatch, W2)
    out = approval.recheck_replay_gui_binding(
        "gui_click", {"x": 10, "y": 20})
    assert out is not None and "click point" in out, out
    assert "NOT executed" in out, out


def test_click_point_over_same_window_proceeds(state, monkeypatch):
    approval._replay_local.gui_binding = dict(W1)
    monkeypatch.setattr(approval, "_gui_binding", lambda: dict(W1))
    _fake_winctl(monkeypatch, W1)
    assert approval.recheck_replay_gui_binding(
        "gui_click", {"x": 10, "y": 20}) is None


def test_type_has_no_point_check(state, monkeypatch):
    approval._replay_local.gui_binding = dict(W1)
    monkeypatch.setattr(approval, "_gui_binding", lambda: dict(W1))
    _fake_winctl(monkeypatch, W2)
    assert approval.recheck_replay_gui_binding(
        "gui_type", {"text": "hi"}) is None
