"""Shell Home V12 command palette (2026-10-31, Alek's order):
the command bar offers apps, files/folders, dashboard tabs and
ARIA actions as typed candidates, ranked with usage history,
keyboard-driven — while its no-match path stays the old
route_command/chat behaviour.

Pure candidate building (aria/shell_home.py) plus panel wiring
with fakes: no real enumeration roots, no real launches, no
real prefs file.
"""
import os
import sys
import types
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import importlib
shell_home = importlib.import_module("aria.shell_home")
shell_panel = importlib.import_module("aria.shell_panel")

NOW = datetime(2026, 10, 31, 1, 30)
APPS = [{"name": "Google Chrome", "path": "/x/chrome.lnk"},
        {"name": "Steam", "path": "/x/steam.lnk"},
        {"name": "Calculator", "path": "/x/calc.lnk"}]
FILES = [{"name": "budget.xlsx", "path": "/d/budget.xlsx",
          "is_dir": False},
         {"name": "SteamLibrary", "path": "/d/SteamLibrary",
          "is_dir": True},
         {"name": "notes.txt", "path": "/d/notes.txt",
          "is_dir": False}]
ACTIONS = shell_home.palette_actions(muted=False)


def _cands(q, **kw):
    return shell_home.palette_candidates(
        q, APPS, FILES, shell_home.DASHBOARD_TABS, ACTIONS, **kw)


# -- pure candidate building ------------------------------------

def test_candidates_find_app_file_tab_action():
    kinds = {c["label"]: c["kind"] for c in _cands("steam")}
    assert kinds["Steam"] == "app"
    assert kinds["SteamLibrary"] == "file"
    cal = _cands("cal")
    assert cal[0]["label"] == "Calculator"
    tabs = _cands("calendar")
    assert tabs and tabs[0]["kind"] == "tab" \
        and tabs[0]["ref"] == "Calendar"
    lock = _cands("lock")
    assert any(c["kind"] == "action" and c["ref"] == "lock"
               for c in lock)


def test_candidates_mute_label_follows_state():
    un = shell_home.palette_actions(muted=False)
    mu = shell_home.palette_actions(muted=True)
    assert any(a["label"] == "Mute voice" for a in un)
    assert any(a["label"] == "Unmute voice" for a in mu)


def test_candidates_history_boosts():
    boosted = _cands("s", history={"Steam": 9})
    plain = _cands("s")
    steam_b = next(c for c in boosted if c["label"] == "Steam")
    steam_p = next(c for c in plain if c["label"] == "Steam")
    assert steam_b["score"] > steam_p["score"]


def test_candidates_blank_query_offers_actions_and_frequent():
    out = _cands("", history={"Steam": 3})
    assert any(c["kind"] == "action" for c in out)
    assert any(c["label"] == "Steam" for c in out)
    assert _cands("zzz-no-match") == []


def test_enumerate_files_bounded(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "Bdir").mkdir()
    (tmp_path / ".hidden").write_text("x")
    sub = tmp_path / "Bdir"
    (sub / "deep.txt").write_text("x")
    out = shell_home.enumerate_files([str(tmp_path)], cap=10)
    names = [e["name"] for e in out]
    assert names == ["Bdir", "a.txt"]  # sorted, shallow, no dotfiles
    assert out[0]["is_dir"] is True
    capped = shell_home.enumerate_files([str(tmp_path)], cap=1)
    assert len(capped) == 1
    assert shell_home.enumerate_files(["/nonexistent-xyz"]) == []


# -- panel wiring -------------------------------------------------

def _panel(**overrides):
    calls = {"launch": [], "command": [], "tab": [],
             "action": [], "opened": [], "history": []}
    state = {"history": {}}
    kwargs = dict(
        apps=APPS,
        on_launch=calls["launch"].append,
        on_command=calls["command"].append,
        now_provider=lambda: NOW,
        briefing_provider=lambda: None,
        research_provider=lambda: "",
        weather_provider=lambda: None,
        pinned_provider=lambda: [],
        pinned_writer=lambda paths: None,
        approval_provider=lambda: [],
        reminder_provider=lambda: [],
        files_provider=lambda: list(FILES),
        history_provider=lambda: dict(state["history"]),
        history_writer=lambda label: (
            state["history"].__setitem__(
                label, state["history"].get(label, 0) + 1),
            calls["history"].append(label)),
        file_opener=calls["opened"].append,
        muted_provider=lambda: False,
        on_open_tab=calls["tab"].append,
        on_shell_action=calls["action"].append)
    kwargs.update(overrides)
    panel = shell_panel.ShellPanel(**kwargs)
    return panel, calls, state


def _key(panel, key):
    ev = QKeyEvent(QEvent.Type.KeyPress, key,
                   Qt.KeyboardModifier.NoModifier)
    return panel.eventFilter(panel.command, ev)


def test_palette_opens_on_typing_and_enter_launches():
    panel, calls, state = _panel()
    panel.command.setText("steam")
    assert not panel.palette.isHidden()
    assert panel.palette.count() >= 2  # Steam + SteamLibrary
    assert panel.palette.currentRow() == 0
    assert _key(panel, Qt.Key.Key_Return) is True
    assert calls["launch"] and \
        calls["launch"][0]["name"] == "Steam"
    assert state["history"].get("Steam") == 1


def test_palette_down_key_moves_selection():
    panel, _calls, _state = _panel()
    panel.command.setText("steam")
    first = panel.palette.currentRow()
    assert _key(panel, Qt.Key.Key_Down) is True
    assert panel.palette.currentRow() == first + 1


def test_palette_esc_closes_and_enter_falls_through():
    panel, calls, _state = _panel()
    panel.command.setText("steam")
    assert _key(panel, Qt.Key.Key_Escape) is True
    assert panel.palette.isHidden()
    # Palette closed: Enter is NOT consumed by the filter —
    # _submit_command (returnPressed) owns it, unchanged.
    assert _key(panel, Qt.Key.Key_Return) is False


def test_palette_file_and_tab_and_action_dispatch():
    panel, calls, _state = _panel()
    file_cand = next(c for c in _cands("budget")
                     if c["kind"] == "file")
    panel._activate_candidate(file_cand)
    assert calls["opened"] == ["/d/budget.xlsx"]
    tab_cand = next(c for c in _cands("inbox")
                    if c["kind"] == "tab")
    panel._activate_candidate(tab_cand)
    assert calls["tab"] == ["Inbox"]
    lock_cand = next(c for c in _cands("lock")
                     if c["kind"] == "action")
    panel._activate_candidate(lock_cand)
    assert calls["action"] == ["lock"]


def test_palette_reminder_prefills_command_bar():
    panel, calls, _state = _panel()
    rem = next(c for c in _cands("remind")
               if c.get("ref") == "reminder")
    panel._activate_candidate(rem)
    assert panel.command.text() == "Remind me to "
    assert calls["action"] == []  # panel-local, no dashboard call


def test_submit_command_chat_path_unchanged():
    panel, calls, _state = _panel()
    panel.command.setText("what is the weather tomorrow")
    panel._submit_command()
    assert calls["command"] == ["what is the weather tomorrow"]
    assert calls["launch"] == []
