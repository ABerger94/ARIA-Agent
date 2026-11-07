"""Pass 27 #21: clicking desktop Confirm must not invalidate the
GUI approval it grants.

Before: a gui_click approval was bound to the foreground window
at park time, and approve_token re-checked the foreground at
replay. Clicking Confirm on ARIA's dashboard activates ARIA's
own window — so the very act of confirming moved the foreground
away from the bound window and the approval was refused.

Fix contract pinned here:
- approve_token restores the bound window to the foreground
  (winctl.bring_to_foreground) BEFORE re-checking, when the
  foreground no longer matches; the existing check still
  refuses when the restore fails or the window is gone;
- no restore is attempted when the foreground already matches
  (the unchanged path is byte-identical);
- bring_to_foreground is Windows-only, never raises, and
  returns True only when the window IS foreground afterwards.

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
winctl = importlib.import_module("aria.tools.winctl")
workspaces = importlib.import_module("aria.workspaces")

W1 = {"title": "Notepad", "process": "notepad.exe", "hwnd": 111}
ARIA_WIN = {"title": "ARIA Command Center", "process": "python.exe",
            "hwnd": 999}


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


class _FakeDesktop:
    """Foreground window + bring_to_foreground, simulating
    Windows: a successful restore makes the bound window
    foreground again; a failed one leaves ARIA's window up."""

    def __init__(self, restore_works=True):
        self.current = dict(ARIA_WIN)
        self.restore_works = restore_works
        self.restore_calls = []

    def foreground_window(self):
        return dict(self.current)

    def bring_to_foreground(self, hwnd):
        self.restore_calls.append(hwnd)
        if self.restore_works and hwnd == W1["hwnd"]:
            self.current = dict(W1)
            return True
        return False

    def window_at_point(self, x, y):
        return dict(self.current)


def _install_fake(monkeypatch, desk):
    fake = types.ModuleType("aria.tools.winctl")
    fake.foreground_window = desk.foreground_window
    fake.bring_to_foreground = desk.bring_to_foreground
    fake.window_at_point = desk.window_at_point
    monkeypatch.setitem(sys.modules, "aria.tools.winctl", fake)
    import aria.tools as _tools_pkg
    monkeypatch.setattr(_tools_pkg, "winctl", fake)
    monkeypatch.setattr(approval, "_gui_binding",
                        desk.foreground_window)


def test_confirm_click_restores_bound_window_and_executes(
        state, monkeypatch):
    """The bug itself: the foreground is ARIA's window at replay
    (the Confirm click activated it), yet the approval executes
    because the bound window is restored first."""
    calls = []
    monkeypatch.setitem(dispatch._REGISTRY, "gui_click",
                        lambda args: calls.append(dict(args)) or "clicked")
    proj = workspaces.create("Project A")
    token = _park_gui_click(monkeypatch, proj)
    desk = _FakeDesktop(restore_works=True)
    _install_fake(monkeypatch, desk)
    out = approval.approve_token(token)
    assert desk.restore_calls == [W1["hwnd"]], desk.restore_calls
    assert calls == [{"x": 10, "y": 20}], calls
    assert "clicked" in out, out


def test_failed_restore_still_refuses(state, monkeypatch):
    calls = []
    monkeypatch.setitem(dispatch._REGISTRY, "gui_click",
                        lambda args: calls.append(dict(args)) or "clicked")
    proj = workspaces.create("Project A")
    token = _park_gui_click(monkeypatch, proj)
    desk = _FakeDesktop(restore_works=False)
    _install_fake(monkeypatch, desk)
    out = approval.approve_token(token)
    assert desk.restore_calls == [W1["hwnd"]], desk.restore_calls
    assert calls == [], calls
    assert "NOT executed" in out, out


def test_unchanged_foreground_attempts_no_restore(state, monkeypatch):
    calls = []
    monkeypatch.setitem(dispatch._REGISTRY, "gui_click",
                        lambda args: calls.append(dict(args)) or "clicked")
    proj = workspaces.create("Project A")
    token = _park_gui_click(monkeypatch, proj)
    desk = _FakeDesktop(restore_works=True)
    desk.current = dict(W1)
    _install_fake(monkeypatch, desk)
    out = approval.approve_token(token)
    assert desk.restore_calls == [], desk.restore_calls
    assert calls == [{"x": 10, "y": 20}], calls
    assert "clicked" in out, out


def test_bring_to_foreground_degrades_off_windows():
    # This box is Linux: no window concept, never raises, False.
    assert winctl.bring_to_foreground(111) is False
    assert winctl.bring_to_foreground(None) is False
    assert winctl.bring_to_foreground("not-a-hwnd") is False
