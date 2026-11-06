"""Pass 26 B2#3a: different desktop windows must not satisfy the
same approval binding.

Verified against the code at b1ba4ad:
- approval.py _gui_binding_matches() ignores HWND whenever a
  title or process is present — so two different windows of the
  same app (same title, same process) satisfy one binding, and
  the replay clicks / types into the wrong window.

Fix contract pinned here:
- when BOTH the bound and the current readings carry an HWND,
  HWND equality is required IN ADDITION to title/process; a
  differing HWND refuses;
- the title-less fallback path (HWND-only identity) is unchanged;
- when either side has no HWND, title/process alone still decide.

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


def test_same_title_process_different_hwnd_no_match():
    bound = {"title": "Notepad", "process": "notepad.exe", "hwnd": 111}
    other = {"title": "Notepad", "process": "notepad.exe", "hwnd": 222}
    assert approval._gui_binding_matches(bound, other) is False


def test_same_title_process_same_hwnd_matches():
    bound = {"title": "Notepad", "process": "notepad.exe", "hwnd": 111}
    same = {"title": "Notepad", "process": "notepad.exe", "hwnd": 111}
    assert approval._gui_binding_matches(bound, same) is True


def test_different_title_same_hwnd_no_match():
    bound = {"title": "Notepad", "process": "notepad.exe", "hwnd": 111}
    other = {"title": "Calc", "process": "notepad.exe", "hwnd": 111}
    assert approval._gui_binding_matches(bound, other) is False


def test_titleless_fallback_still_hwnd_only():
    assert approval._gui_binding_matches({"hwnd": 7}, {"hwnd": 7}) is True
    assert approval._gui_binding_matches({"hwnd": 7}, {"hwnd": 8}) is False
    assert approval._gui_binding_matches({"hwnd": 7}, {}) is False


def test_missing_hwnd_on_one_side_falls_back_to_title_process():
    bound = {"title": "Notepad", "process": "notepad.exe", "hwnd": 111}
    no_hwnd = {"title": "Notepad", "process": "notepad.exe"}
    assert approval._gui_binding_matches(bound, no_hwnd) is True
    assert approval._gui_binding_matches(no_hwnd, bound) is True


def test_approve_refuses_when_hwnd_changed(state, monkeypatch):
    calls = []
    monkeypatch.setitem(dispatch._REGISTRY, "gui_click",
                        lambda args: calls.append(dict(args)) or "clicked")
    proj = workspaces.create("Project A")
    parked = {"title": "Notepad", "process": "notepad.exe", "hwnd": 111}
    current = {"title": "Notepad", "process": "notepad.exe", "hwnd": 222}
    monkeypatch.setattr(approval, "_gui_binding", lambda: parked)
    with workspaces.use(proj):
        approval.request_approval("gui_click", {"x": 10, "y": 20},
                                  desc="click at (10, 20)")
    (token,) = list(approval._PENDING)
    monkeypatch.setattr(approval, "_gui_binding", lambda: current)
    out = approval.approve_token(token)
    assert calls == [], calls
    assert "refused" in out.lower() or "NOT executed" in out, out
    (rec,) = approval.recent_resolutions()
    assert rec["verdict"] == "refused"


def test_approve_executes_when_hwnd_unchanged(state, monkeypatch):
    calls = []
    monkeypatch.setitem(dispatch._REGISTRY, "gui_click",
                        lambda args: calls.append(dict(args)) or "clicked")
    proj = workspaces.create("Project A")
    window = {"title": "Notepad", "process": "notepad.exe", "hwnd": 111}
    monkeypatch.setattr(approval, "_gui_binding", lambda: window)
    with workspaces.use(proj):
        approval.request_approval("gui_click", {"x": 10, "y": 20},
                                  desc="click at (10, 20)")
    (token,) = list(approval._PENDING)
    out = approval.approve_token(token)
    assert "clicked" in out, out
    assert calls == [{"x": 10, "y": 20}], calls
