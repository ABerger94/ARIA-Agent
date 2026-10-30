"""Shell Home panel (ARIA OS, 2026-10-30): the real Qt widget,
offscreen — header paints the model, search filters the list,
the command bar routes launch vs chat to the injected callbacks,
Exit to Windows is present and fires its callback, and the
Shell Mode boot checkbox reports toggles.

The module-level _APP reference matters: without it the
QApplication is garbage-collected and the next QWidget aborts.
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
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import importlib
shell_panel = importlib.import_module("aria.shell_panel")

APPS = [{"name": "Google Chrome", "path": "/x/chrome.lnk"},
        {"name": "Steam", "path": "/x/steam.lnk"},
        {"name": "Calculator", "path": "/x/calc.lnk"}]
NOW = datetime(2026, 10, 30, 15, 47)


def _panel(**overrides):
    calls = {"launch": [], "command": [], "exit": 0, "mode": []}
    kwargs = dict(
        apps=APPS,
        on_launch=calls["launch"].append,
        on_command=calls["command"].append,
        on_exit=lambda: calls.__setitem__("exit", calls["exit"] + 1),
        on_shell_mode=calls["mode"].append,
        now_provider=lambda: NOW,
        shell_mode=False,
        briefing_provider=lambda: None,
        now_playing_provider=lambda: None,
        status_provider=lambda: None)
    kwargs.update(overrides)
    panel = shell_panel.ShellPanel(**kwargs)
    return panel, calls


def test_header_and_exit_button():
    panel, _calls = _panel()
    assert panel.clock_label.text() == "15:47"
    assert panel.greeting_label.text() == "Good afternoon, Alek"
    assert panel.date_label.text() == "Friday, October 30"
    assert panel.exit_btn.text() == "Exit to Windows"
    assert panel.count_label.text() == "3 apps"


def test_search_filters_the_list():
    panel, _calls = _panel()
    assert panel.app_list.count() == 3
    panel.search.setText("steam")
    assert panel.app_list.count() == 1
    assert panel.app_list.item(0).text() == "Steam"
    panel.search.setText("")
    assert panel.app_list.count() == 3


def test_command_bar_routes_launch_and_chat():
    panel, calls = _panel()
    panel.command.setText("open Steam")
    panel.command.returnPressed.emit()
    assert [a["name"] for a in calls["launch"]] == ["Steam"]
    assert calls["command"] == []
    panel.command.setText("what is the capital of France")
    panel.command.returnPressed.emit()
    assert calls["command"] == ["what is the capital of France"]


def test_item_activation_launches():
    panel, calls = _panel()
    panel.app_list.itemActivated.emit(panel.app_list.item(1))
    assert [a["name"] for a in calls["launch"]] == ["Steam"]


def test_exit_button_fires_callback():
    panel, calls = _panel()
    panel.exit_btn.click()
    assert calls["exit"] == 1


def test_shell_mode_checkbox_reports_toggles():
    panel, calls = _panel()
    assert panel.shell_mode_box.isChecked() is False
    panel.shell_mode_box.setChecked(True)
    assert calls["mode"] == [True]


# -- V2 layout: tile grid, rail cards, dock pill ------------------

def test_app_grid_is_a_tile_grid_with_icons():
    from PyQt6.QtWidgets import QListWidget
    panel, _calls = _panel()
    assert panel.app_list.viewMode() == QListWidget.ViewMode.IconMode
    assert panel.app_list.count() == 3
    for i in range(3):
        icon = panel.app_list.item(i).icon()
        assert icon is not None and not icon.isNull()
    panel.search.setText("steam")
    assert panel.app_list.count() == 1
    assert panel.app_list.item(0).text() == "Steam"


def test_cards_show_honest_empty_states_without_data():
    panel, _calls = _panel()
    assert "No briefing yet" in panel.briefing_body.text()
    assert panel.playing_body.text() == "Nothing playing"
    # Status provider gave nothing; only the panel's own shell
    # mode state may speak.
    assert panel.status_body.text() == "Shell Mode boot: off"


def test_cards_render_provider_data():
    report = {"sections": [
        {"label": "Today's schedule", "text": "Schedule: dentist.",
         "status": "ok"}]}
    info = {"title": "Artist - Track", "app": "Spotify",
            "playing": True}
    status = {"safe_mode": True, "approval_mode": "confirm-all",
              "voice_enabled": False}
    panel, _calls = _panel(briefing_provider=lambda: report,
                           now_playing_provider=lambda: info,
                           status_provider=lambda: status)
    assert panel.briefing_body.text() == "Schedule: dentist."
    assert panel.playing_body.text() == \
        "Artist - Track\nPlaying · Spotify"
    assert panel.status_body.text() == \
        "Approvals: confirm-all\nSafe mode: on\nVoice: off\n" \
        "Shell Mode boot: off"


def test_broken_card_provider_falls_back_to_empty_state():
    def boom():
        raise RuntimeError("seam exploded")
    panel, _calls = _panel(briefing_provider=boom,
                           now_playing_provider=boom,
                           status_provider=boom)
    assert "No briefing yet" in panel.briefing_body.text()
    assert panel.playing_body.text() == "Nothing playing"


def test_dock_holds_command_and_exit_stays_visible():
    panel, _calls = _panel()
    assert panel.command.parent() is panel.dock
    assert panel.dock.objectName() == "shellDock"
    assert panel.exit_btn.text() == "Exit to Windows"
    assert panel.orb is not None
