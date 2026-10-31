"""Shell Home panel (ARIA OS, 2026-10-30): the real Qt widget,
offscreen — header paints the model, search filters the list,
the command bar routes launch vs chat to the injected callbacks,
Dashboard button is present and fires its callback, and the
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
briefing_mod = importlib.import_module("aria.briefing")

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
        research_provider=lambda: "")
    kwargs.update(overrides)
    panel = shell_panel.ShellPanel(**kwargs)
    return panel, calls


def test_header_and_exit_button():
    panel, _calls = _panel()
    assert panel.clock_label.text() == "15:47"
    assert panel.greeting_label.text() == "Good afternoon, Alek"
    assert panel.date_label.text() == "Friday, October 30"
    assert panel.exit_btn.text() == "Dashboard"
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


# -- V3: the double-launch fix ------------------------------------
# The bug shipped because offscreen tests emitted signals by hand
# and never sent a REAL double-click. V2 had connected both
# itemActivated and itemDoubleClicked to the launch handler, and
# Qt fires both for one double-click — so these tests drive the
# widget the way a hand does.

def _send_mouse(widget, etype, pos):
    """Dispatch one QMouseEvent of *etype* at *pos*, synchronously.

    A physical double-click reaches a Qt widget as the platform
    sequence Press, Release, MouseButtonDblClick, Release — this
    sends exactly those events through the widget's real event
    handlers. (QTest.mouseDClick cannot be used here: on this
    offscreen platform it drops every event after the first
    press — verified against a bare QListWidget, which also
    received only itemPressed — so its sequence never becomes a
    double-click at all.)"""
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    buttons = (Qt.MouseButton.LeftButton
               if etype != QEvent.Type.MouseButtonRelease
               else Qt.MouseButton.NoButton)
    event = QMouseEvent(etype, QPointF(pos),
                        QPointF(widget.mapToGlobal(pos)),
                        Qt.MouseButton.LeftButton, buttons,
                        Qt.KeyboardModifier.NoModifier)
    _APP.sendEvent(widget, event)


def test_real_double_click_launches_exactly_once():
    from PyQt6.QtCore import QEvent
    panel, calls = _panel()
    panel.resize(1000, 700)
    panel.show()
    _APP.processEvents()
    item = panel.app_list.item(1)  # Steam
    centre = panel.app_list.visualItemRect(item).center()
    viewport = panel.app_list.viewport()
    _send_mouse(viewport, QEvent.Type.MouseButtonPress, centre)
    _send_mouse(viewport, QEvent.Type.MouseButtonRelease, centre)
    _send_mouse(viewport, QEvent.Type.MouseButtonDblClick, centre)
    _send_mouse(viewport, QEvent.Type.MouseButtonRelease, centre)
    _APP.processEvents()
    # One physical double-click: itemDoubleClicked AND
    # itemActivated both fire (proven at the widget level) — the
    # launch callback must still run exactly once.
    assert [a["name"] for a in calls["launch"]] == ["Steam"]
    panel.hide()


def test_relaunch_guard_blocks_repeat_within_window():
    clock = [1000.0]
    panel, calls = _panel(time_provider=lambda: clock[0])
    steam = panel.app_list.item(1)
    calc = panel.app_list.item(2)
    panel.app_list.itemActivated.emit(steam)
    panel.app_list.itemActivated.emit(steam)  # bounce: dropped
    assert [a["name"] for a in calls["launch"]] == ["Steam"]
    clock[0] += 1.0  # still inside the 1.5s guard window
    panel.app_list.itemActivated.emit(steam)
    assert len(calls["launch"]) == 1
    panel.app_list.itemActivated.emit(calc)  # different app: fine
    assert [a["name"] for a in calls["launch"]] == \
        ["Steam", "Calculator"]
    clock[0] += 2.0  # past the window: same app launches again
    panel.app_list.itemActivated.emit(steam)
    assert [a["name"] for a in calls["launch"]] == \
        ["Steam", "Calculator", "Steam"]


def test_v3_layout_grid_metrics():
    from PyQt6.QtCore import QSize
    panel, _calls = _panel()
    # Tiles: 48px icons, cells tall enough for two-line names.
    assert panel.app_list.iconSize() == QSize(48, 48)
    assert panel.app_list.gridSize() == QSize(120, 122)


def test_v4_rail_is_briefing_only_and_it_fills_the_rail():
    from PyQt6.QtWidgets import QLabel, QPlainTextEdit
    panel, _calls = _panel()
    # Now Playing and Status are GONE — no bodies, no titles.
    assert not hasattr(panel, "playing_body")
    assert not hasattr(panel, "status_body")
    titles = [w.text() for w in panel.findChildren(QLabel)]
    assert "NOW PLAYING" not in titles
    assert "STATUS" not in titles
    assert "BRIEFING" in titles
    # The Briefing body is a read-only text view (the Briefing
    # tab's widget family), and it FILLS the rail: at full-window
    # size the card + view take the rail's whole height, so a
    # typical briefing reads whole instead of through the V3
    # card's short scroll window.
    assert isinstance(panel.briefing_body, QPlainTextEdit)
    assert panel.briefing_body.isReadOnly()
    assert panel.briefing_text is panel.briefing_body
    panel.resize(1600, 900)
    panel.show()
    _APP.processEvents()
    assert panel.briefing_frame.height() > 500
    assert panel.briefing_body.height() > 450
    panel.hide()


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


FULL_REPORT = {
    "generated_at": "2026-10-30T08:00:00-04:00",
    "text": "Good morning, Alek. You've got: Dentist (3 PM to "
            "4 PM). More follows.",
    "sections": [
        {"title": "Schedule", "key": "schedule",
         "text": "You've got: Dentist (3 PM to 4 PM).", "sources": []},
        {"title": "Weather", "key": "weather",
         "text": "In Dundalk it's 61 degrees and clear.",
         "sources": [{"title": "National Weather Service",
                      "url": "https://example.com/nws"}]},
    ]}


def test_briefing_shows_placeholder_without_a_briefing():
    panel, _calls = _panel()
    # report_text(None): the tab's own honest placeholder.
    assert panel.briefing_body.toPlainText() == \
        briefing_mod.report_text(None)


def test_briefing_body_is_the_briefing_tab_text_verbatim():
    panel, _calls = _panel(briefing_provider=lambda: FULL_REPORT)
    assert panel.briefing_body.toPlainText() == \
        briefing_mod.report_text(FULL_REPORT)
    # Research rides along exactly like the tab's load.
    panel2, _calls2 = _panel(
        briefing_provider=lambda: FULL_REPORT,
        research_provider=lambda: "RESEARCH BRIEFS\nA brief.")
    assert panel2.briefing_body.toPlainText() == \
        briefing_mod.report_text(FULL_REPORT) + \
        "\nRESEARCH BRIEFS\nA brief."


def test_broken_briefing_provider_falls_back_to_placeholder():
    def boom():
        raise RuntimeError("seam exploded")
    panel, _calls = _panel(briefing_provider=boom,
                           research_provider=boom)
    assert panel.briefing_body.toPlainText() == \
        briefing_mod.report_text(None)


def test_dock_holds_command_and_exit_stays_visible():
    panel, _calls = _panel()
    assert panel.command.parent() is panel.dock
    assert panel.dock.objectName() == "shellDock"
    assert panel.exit_btn.text() == "Dashboard"
    assert panel.orb is not None
