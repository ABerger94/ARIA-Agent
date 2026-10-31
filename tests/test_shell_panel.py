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
    calls = {"launch": [], "command": []}
    # V8: pins live behind injected seams over an in-memory
    # store — no test in this suite reads or writes a real
    # ui.json. The store starts with every sample app pinned
    # (in APPS order), so the default pinned view shows the
    # full sample grid the pre-V8 tests were written against;
    # V8 tests rewrite the store to exercise the pin flows.
    pin_store = {"paths": [a["path"] for a in APPS]}
    kwargs = dict(
        apps=APPS,
        on_launch=calls["launch"].append,
        on_command=calls["command"].append,
        now_provider=lambda: NOW,
        briefing_provider=lambda: None,
        research_provider=lambda: "",
        # Hermetic: no panel in this suite touches the network
        # unless its test injects a weather provider of its own.
        weather_provider=lambda: None,
        pinned_provider=lambda: list(pin_store["paths"]),
        pinned_writer=lambda paths: pin_store.__setitem__(
            "paths", list(paths)))
    kwargs.update(overrides)
    panel = shell_panel.ShellPanel(**kwargs)
    panel._test_pin_store = pin_store
    return panel, calls


def test_header_and_no_exit_button():
    panel, _calls = _panel()
    assert panel.clock_label.text() == "15:47"
    assert panel.greeting_label.text() == "Good afternoon, Alek"
    assert panel.date_label.text() == "Friday, October 30"
    # V5 (Alek: "I only need one Dashboard button"): the panel
    # has NO exit button — the dashboard's center-controls
    # button becomes the Dashboard button while the shell is
    # open (test_shell_dashboard covers that side).
    assert not hasattr(panel, "exit_btn")
    # V8: the count line belongs to the grid's view — the
    # helper seeds all three sample apps pinned, so the home
    # (pinned) view honestly reads "3 pinned".
    assert panel.count_label.text() == "3 pinned"


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
    # And the Shell Mode boot checkbox moved to Settings — the
    # rail carries nothing but the Briefing card now.
    assert not hasattr(panel, "shell_mode_box")
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


def test_dock_holds_command_and_no_exit_button():
    panel, _calls = _panel()
    assert panel.command.parent() is panel.dock
    assert panel.dock.objectName() == "shellDock"
    assert not hasattr(panel, "exit_btn")
    assert panel.orb is not None


# -- V4 addendum: the shell follows the Customize accent ---------

def test_accent_style_builder_default_is_stock():
    # No accent (or the stock accent itself) leaves the sheet
    # byte-identical — the default path can never lose its theme.
    assert shell_panel.shell_style(None) == shell_panel._STYLE
    assert "#57c7d4" in shell_panel.shell_style(None)
    assert shell_panel.shell_style("#57c7d4") == shell_panel._STYLE
    assert shell_panel.shell_style("not-a-colour") == \
        shell_panel._STYLE


def test_custom_accent_rethemes_panel_and_orbs():
    panel, _calls = _panel(accent="#e83a9a")
    assert panel._accent == "#e83a9a"
    sheet = panel.styleSheet().lower()
    # No stock-cyan accent hue survives anywhere in the sheet,
    # and the shifted family colour is what replaced it.
    assert "#57c7d4" not in sheet
    assert "#2b5873" not in sheet
    assert "#d4579c" in sheet
    # The dock dot (still an OrbDisc) repainted to the accent
    # family. The header orb is the ResonanceOrb since V9 —
    # its accent path is the resonance palette, proven by the
    # pixel test in the V9 section below, not by disc colours.
    assert panel.dock_orb._core_color.name() == "#d4579c"
    assert panel.dock_orb._ring.name() == "#732b3f"


def test_set_accent_rethemes_live_and_ignores_invalid():
    panel, _calls = _panel(accent="#57c7d4")
    assert "#57c7d4" in panel.styleSheet().lower()
    panel.set_accent("#e83a9a")
    assert panel._accent == "#e83a9a"
    assert "#57c7d4" not in panel.styleSheet().lower()
    assert panel.dock_orb._core_color.name() == "#d4579c"
    panel.set_accent("bogus")
    assert panel._accent == "#e83a9a"


# -- V6: header weather -------------------------------------------

WEATHER = {"temp_F": "61", "desc": "Clear",
           "high_F": "68", "low_F": "49"}


def _pump_weather(panel):
    """Let the off-thread fetch land: join the worker thread,
    then deliver its queued signal to the labels."""
    thread = panel._weather_thread
    if thread is not None:
        thread.join(timeout=5)
    _APP.processEvents()


def test_weather_line_and_symbol_show_from_provider():
    panel, _calls = _panel(weather_provider=lambda: WEATHER)
    _pump_weather(panel)
    assert panel.weather_label.text() == \
        "61°F · Clear · High 68 · Low 49"
    assert panel.weather_symbol_label.text() == "☀"
    assert panel.weather_label.objectName() == "shellWeather"
    assert panel.weather_symbol_label.objectName() == \
        "shellWeatherSymbol"


def test_weather_none_provider_leaves_labels_empty():
    panel, _calls = _panel(weather_provider=lambda: None)
    _pump_weather(panel)
    # Fail-soft: no data means NO readout — no placeholder text.
    assert panel.weather_label.text() == ""
    assert panel.weather_symbol_label.text() == ""


def test_weather_broken_provider_leaves_labels_empty():
    def boom():
        raise RuntimeError("offline")
    panel, _calls = _panel(weather_provider=boom)
    _pump_weather(panel)
    assert panel.weather_label.text() == ""
    assert panel.weather_symbol_label.text() == ""


def test_weather_fetch_is_throttled():
    clock = [5000.0]
    fetches = []

    def provider():
        fetches.append(1)
        return WEATHER

    panel, _calls = _panel(weather_provider=provider,
                           time_provider=lambda: clock[0])
    # The constructor's refresh already fetched once.
    _pump_weather(panel)
    assert len(fetches) == 1
    # Repeated refreshes inside the window never refetch — the
    # 20s clock tick calls refresh_weather every tick.
    assert panel.refresh_weather() is False
    assert panel.refresh_weather() is False
    _pump_weather(panel)
    assert len(fetches) == 1
    # Past the ~15-minute window, a refresh fetches again.
    clock[0] += shell_panel.WEATHER_REFETCH_SECONDS + 1
    assert panel.refresh_weather() is True
    _pump_weather(panel)
    assert len(fetches) == 2


def test_weather_style_rules_and_accent_shift():
    stock = shell_panel.shell_style(None)
    assert "#shellWeather" in stock
    assert "#shellWeatherSymbol" in stock
    # The symbol carries an accent-family colour (27px, stock
    # cyan), so a custom accent hue-shifts it like the shell's
    # other accent elements (#57c7d4 -> #d4579c family).
    assert "QLabel#shellWeatherSymbol { color: #57c7d4;" in stock
    shifted = shell_panel.shell_style("#e83a9a")
    assert "QLabel#shellWeatherSymbol { color: #d4579c;" in \
        shifted


# -- V8: pinned apps -------------------------------------------------

def _names(panel):
    return [panel.app_list.item(i).text()
            for i in range(panel.app_list.count())]


def _set_pins(panel, paths):
    panel._test_pin_store["paths"] = list(paths)
    panel._refilter()


def test_default_view_is_pinned_only():
    panel, _calls = _panel()
    _set_pins(panel, ["/x/steam.lnk"])
    assert _names(panel) == ["Steam"]
    assert panel.count_label.text() == "1 pinned"
    assert panel.view_toggle.text() == "All apps"


def test_all_apps_toggle_shows_everything_and_back():
    panel, _calls = _panel()
    _set_pins(panel, ["/x/steam.lnk"])
    panel.view_toggle.click()
    assert _names(panel) == ["Google Chrome", "Steam", "Calculator"]
    assert panel.count_label.text() == "3 apps"
    assert panel.view_toggle.text() == "Pinned"
    panel.view_toggle.click()
    assert _names(panel) == ["Steam"]
    assert panel.count_label.text() == "1 pinned"
    assert panel.view_toggle.text() == "All apps"


def test_search_from_pinned_view_finds_unpinned_apps():
    panel, _calls = _panel()
    _set_pins(panel, ["/x/steam.lnk"])
    panel.search.setText("calc")
    # Search searches ALL apps, pinned or not...
    assert _names(panel) == ["Calculator"]
    assert panel.count_label.text() == "1 of 3 apps"
    panel.search.setText("")
    # ...and clearing it returns to the pinned view.
    assert _names(panel) == ["Steam"]
    assert panel.count_label.text() == "1 pinned"


def test_pin_order_governs_the_home_grid():
    panel, _calls = _panel()
    _set_pins(panel, ["/x/calc.lnk", "/x/chrome.lnk"])
    assert _names(panel) == ["Calculator", "Google Chrome"]


def test_right_click_menu_pins_and_unpins():
    panel, _calls = _panel()
    _set_pins(panel, [])
    panel.set_view_all(True)
    chrome = panel._apps[0]
    assert panel.pin_action_label(chrome) == "Pin to Home"
    menu = panel._pin_menu(chrome)
    assert [a.text() for a in menu.actions()] == ["Pin to Home"]
    menu.actions()[0].trigger()
    # The pin persisted through the writer seam...
    assert panel._test_pin_store["paths"] == ["/x/chrome.lnk"]
    # ...and the same tile now offers removal.
    assert panel.pin_action_label(chrome) == "Remove from Home"
    menu = panel._pin_menu(chrome)
    # V14: a PINNED tile's menu also carries the arrangement
    # actions (Move up / Move down / Move to group ▸) after the
    # V8 pin action — the unpinned menu above stays one action.
    # V17-B renamed the moves Move left / Move right (the
    # shelves and the grid both flow left-to-right).
    texts = [a.text() for a in menu.actions()]
    assert texts[0] == "Remove from Home"
    assert "Move left" in texts and "Move right" in texts
    assert "Move to group" in texts
    menu.actions()[0].trigger()
    assert panel._test_pin_store["paths"] == []


def test_unpin_from_pinned_view_removes_the_tile():
    panel, _calls = _panel()
    _set_pins(panel, ["/x/steam.lnk", "/x/calc.lnk"])
    assert _names(panel) == ["Steam", "Calculator"]
    panel.toggle_pin(panel._apps[1])       # Steam
    assert panel._test_pin_store["paths"] == ["/x/calc.lnk"]
    assert _names(panel) == ["Calculator"]


def test_pin_from_search_results_works():
    panel, _calls = _panel()
    _set_pins(panel, [])
    panel.search.setText("steam")
    assert _names(panel) == ["Steam"]
    panel.toggle_pin(panel._apps[1])       # Steam, found by search
    assert panel._test_pin_store["paths"] == ["/x/steam.lnk"]
    panel.search.setText("")
    assert _names(panel) == ["Steam"]      # and it's home now


def test_empty_state_when_nothing_pinned():
    panel, _calls = _panel()
    _set_pins(panel, [])
    assert panel.app_list.count() == 0
    assert panel.grid_stack.currentWidget() is panel.empty_label
    assert "No pinned apps yet" in panel.empty_label.text()
    assert "right-click" in panel.empty_label.text()
    assert panel.count_label.text() == "0 pinned"
    # The way out is right there: the toggle reveals all apps.
    panel.view_toggle.click()
    assert panel.grid_stack.currentWidget() is panel.app_list
    assert panel.app_list.count() == 3


def test_stale_pin_is_skipped_silently():
    panel, _calls = _panel()
    _set_pins(panel, ["/x/uninstalled.lnk", "/x/steam.lnk"])
    assert _names(panel) == ["Steam"]
    assert panel.count_label.text() == "1 pinned"
    assert panel.grid_stack.currentWidget() is panel.app_list


# -- V9: the header orb is the waveform orb ---------------------------

def test_header_orb_is_the_waveform_orb():
    panel, _calls = _panel()
    from aria.resonance import ResonanceOrb
    assert isinstance(panel.orb, ResonanceOrb)
    assert panel.orb._transparent_background
    # 96px fixed in the old static disc's slot (the orb's own
    # 280px centerpiece minimum is overridden by the fix).
    assert panel.orb.minimumWidth() == 96
    assert panel.orb.maximumWidth() == 96
    assert panel.orb.minimumHeight() == 96
    assert panel.orb.maximumHeight() == 96
    # The dock's status dot stays a painted OrbDisc.
    assert isinstance(panel.dock_orb, shell_panel.OrbDisc)
    assert not isinstance(panel.dock_orb, ResonanceOrb)


def test_advance_orb_drives_the_waveform():
    panel, _calls = _panel()
    for _ in range(6):
        panel.advance_orb("speaking", 0.9)
    assert panel.orb.state == "SPEAKING"
    assert panel.orb.speaking
    assert panel.orb._amp_disp > 0.3
    assert len(panel.orb._amp_hist) == 6
    # Muted + idle are accepted without a crash (the dashboard
    # forwards whatever the tick computed).
    panel.advance_orb("idle", 0.0, muted=True)
    assert panel.orb.muted
    assert panel.orb.state == "IDLE"


def test_header_orb_pixels_follow_resonance_accent():
    # In the test_resonance style: shift the RESONANCE module
    # palette to magenta (the dashboard's _apply_painted_accent
    # does exactly this on a Customize change), drive the shell
    # orb speaking, render it, and count pixel families. The
    # palette is module-global, so it is restored in finally.
    import aria.resonance as resonance
    panel, _calls = _panel()
    try:
        assert resonance.apply_accent("#e83a9a")
        for _ in range(30):
            panel.advance_orb("speaking", 0.95)
        panel.show()
        _APP.processEvents()
        img = panel.orb.grab().toImage()
        magenta = cyan = 0
        for x in range(img.width()):
            for y in range(img.height()):
                c = img.pixelColor(x, y)
                if c.alpha() < 200:
                    continue        # transparent surround
                # Shifted PRI family: strong red, real blue,
                # suppressed green.
                if c.red() > 120 and c.blue() > 100 \
                        and c.green() < 115:
                    magenta += 1
                # Stock PRI cyan (#00d4ff): near-zero red,
                # strong green AND blue.
                if c.red() < 90 and c.green() > 150 \
                        and c.blue() > 180:
                    cyan += 1
        # Thresholds MEASURED on the real 96px render: the
        # magenta palette scores magenta 22 / cyan 0; the stock
        # palette scores magenta 0 / cyan 20. Asserted with
        # ~2x headroom on the presence side, exact on absence.
        assert magenta >= 10, (magenta, cyan)
        assert cyan == 0, (magenta, cyan)
    finally:
        resonance.apply_accent(None)


# -- V10: the accent shift covers the SURFACES too ----------------

def test_shell_style_shifts_every_hex_in_the_sheet():
    # The invariant (Alek's witnessed report: "not all of the
    # home shell changed color"): with a non-default accent,
    # NO stock hex from the stylesheet survives in the
    # generated sheet — surfaces and text tones shift with the
    # accent hues. Expected values come from shift_palette
    # itself, never a second copy of the math.
    import re
    from aria.hue_wheel import shift_palette
    stock_hexes = set(re.findall(r"#[0-9a-f]{6}",
                                 shell_panel._STYLE))
    assert len(stock_hexes) >= 10
    expected = shift_palette({h: h for h in stock_hexes},
                             shell_panel._ACCENT_BASE, "#e83a9a")
    sheet = shell_panel.shell_style("#e83a9a")
    for h in stock_hexes:
        assert h not in sheet, h
    for shifted in expected.values():
        assert shifted in sheet, shifted
    # The surfaces Alek actually saw stuck on navy:
    for surface in ("#0b1622", "#0e1c2c", "#101f30"):
        assert expected[surface] in sheet


def test_shell_style_stock_paths_stay_byte_identical():
    # The other half of the invariant: no accent, the stock
    # accent itself, and junk all return the untouched sheet
    # (the full-set HSV round-trip at the base hue is exact —
    # measured when V10 landed).
    assert shell_panel.shell_style(None) == shell_panel._STYLE
    assert shell_panel.shell_style("#57c7d4") == shell_panel._STYLE
    assert shell_panel.shell_style("junk") == shell_panel._STYLE


def test_panel_magenta_surfaces_in_sheet_and_pixels():
    from aria.hue_wheel import shift_palette
    panel, _calls = _panel(accent="#e83a9a")
    sheet = panel.styleSheet()
    expected = shift_palette(
        {h: h for h in shell_panel._STYLE_HEXES},
        shell_panel._ACCENT_BASE, "#e83a9a")
    # The generated sheet carries the shifted surfaces in
    # their actual rules.
    assert f"QWidget#shellHome {{ background: " \
        f"{expected['#0b1622']}" in sheet
    assert f"QFrame#shellCard {{ background: " \
        f"{expected['#0e1c2c']}" in sheet
    assert f"QFrame#shellDock {{ background: " \
        f"{expected['#101f30']}" in sheet
    # Pixel probe (the V4 white-slab pattern): the Briefing
    # card's top strip renders in the accent family — shifted
    # plum is RED-dominant where stock navy is BLUE-dominant —
    # and stays a dark surface either way.
    panel.resize(1200, 800)
    panel.show()
    _APP.processEvents()
    img = panel.briefing_frame.grab().toImage()
    c = img.pixelColor(img.width() // 2, 5)
    assert c.red() > c.blue(), (c.red(), c.green(), c.blue())
    assert max(c.red(), c.green(), c.blue()) < 110, \
        (c.red(), c.green(), c.blue())


def test_tile_ink_comes_from_the_shifted_map():
    # Painted-surface audit: the letter tiles' glyph ink is
    # the shell background THROUGH the shift, not a frozen
    # stock hex; unaccented it is the stock background.
    from aria.hue_wheel import shift_palette
    expected = shift_palette(
        {h: h for h in shell_panel._SHIFT_HEXES},
        shell_panel._ACCENT_BASE, "#e83a9a")
    assert shell_panel._tile_ink("#e83a9a") == expected["#0b1622"]
    assert shell_panel._tile_ink(None) == "#0b1622"
    assert shell_panel._tile_ink("junk") == "#0b1622"
