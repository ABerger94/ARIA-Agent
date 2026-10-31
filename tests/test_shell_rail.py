"""Shell Home V16 rail layout fix (2026-10-31, Alek's live
witness on bcd52ef5): "briefing area needs to be bigger on
home shell so i can read it better."

His screenshot's measured cause: the panel itself sat at
its size-hint height in the dashboard's centerpiece grid
cell (default Preferred size policy) — the rail's stretch
only divided that hint height, so the Briefing card got a
sliver while dead space sat above and below the panel.
Pins here: the panel claims Expanding/Expanding; Today is
Maximum-height (natural size only); the briefing body has
a 140px floor; in a tall window the Briefing card is
materially taller than Today and grows with the window.
Same screenshot: Today's Open buttons rendered cut
mid-word — _today_row now gives the label an Ignored
policy (it absorbs all compression) and every button a
Fixed policy at its full size hint; the geometry of every
painted button must sit fully inside the card.
"""
import os
import sys
import types
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules.setdefault("aria", pkg)

import pytest
pytest.importorskip("PyQt6")
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QPushButton, QSizePolicy

_APP = QApplication.instance() or QApplication([])

import importlib
shell_panel = importlib.import_module("aria.shell_panel")

NOW = datetime(2026, 10, 31, 12, 0)


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


DUE = (5, "once", "take out the trash", 0,
       _ts(NOW - timedelta(minutes=5)))
LATER = (6, "once", "call the dentist", 0,
         _ts(NOW + timedelta(hours=3)))
EVENT = {"summary": "Dentist appointment",
         "start": NOW + timedelta(hours=2), "all_day": False}


def _panel():
    return shell_panel.ShellPanel(
        apps=[],
        on_launch=lambda app: None,
        on_command=lambda text: None,
        now_provider=lambda: NOW,
        briefing_provider=lambda: None,
        research_provider=lambda: "",
        weather_provider=lambda: None,
        pinned_provider=lambda: [],
        pinned_writer=lambda paths: None,
        approval_provider=lambda: [],
        reminder_provider=lambda: [],
        files_provider=lambda: [],
        history_provider=lambda: {},
        history_writer=lambda label: None,
        on_open_tab=lambda name: None,
        tasks_provider=lambda: [DUE, LATER],
        task_actor=lambda tid, action: None,
        today_remote_provider=lambda: {"event": dict(EVENT),
                                       "unread": 4})


def _pump(panel):
    thread = panel._today_thread
    if thread is not None:
        thread.join(timeout=5)
    # Two rounds, same as the V13 suite: the first delivers
    # the fetch signal (whose repaint schedules deferred
    # deletes), the second flushes them.
    _APP.processEvents()
    _APP.processEvents()


def _today_buttons(panel):
    """Painted Today-row buttons, walking the rows layout —
    never findChildren, which also sees rows whose
    deleteLater is still pending."""
    out = []
    for i in range(panel.today_rows.count()):
        row = panel.today_rows.itemAt(i).widget()
        if row is None or row.layout() is None:
            continue
        for j in range(row.layout().count()):
            widget = row.layout().itemAt(j).widget()
            if isinstance(widget, QPushButton):
                out.append(widget)
    return out


def test_panel_claims_full_cell():
    panel = _panel()
    policy = panel.sizePolicy()
    assert policy.horizontalPolicy() == \
        QSizePolicy.Policy.Expanding
    assert policy.verticalPolicy() == QSizePolicy.Policy.Expanding


def test_today_frame_stays_compact_by_policy():
    panel = _panel()
    assert panel.today_frame.sizePolicy().verticalPolicy() == \
        QSizePolicy.Policy.Maximum


def test_briefing_body_has_reading_floor():
    panel = _panel()
    assert panel.briefing_body.minimumHeight() >= 140


def test_briefing_grows_with_window_and_dominates_rail():
    panel = _panel()
    panel.resize(1920, 700)
    panel.show()
    _pump(panel)
    short = panel.briefing_frame.height()
    panel.resize(1920, 1050)
    _APP.processEvents()
    tall = panel.briefing_frame.height()
    # The extra window height lands in the Briefing card,
    # not in dead space and not in Today.
    assert tall - short >= 150
    assert tall > panel.today_frame.height() * 1.5
    panel.close()


def test_today_buttons_fixed_size_fully_inside_card():
    panel = _panel()
    panel.show()
    _pump(panel)
    for width in (1920, 1280):
        panel.resize(width, 900)
        _APP.processEvents()
        buttons = _today_buttons(panel)
        # Event Open, Done + Snooze per task row, unread Open.
        assert len(buttons) == 6
        frame_w = panel.today_frame.width()
        for btn in buttons:
            assert btn.sizePolicy().horizontalPolicy() == \
                QSizePolicy.Policy.Fixed
            # Full fixed size: never shrunk below its hint.
            assert btn.width() >= btn.sizeHint().width()
            origin = btn.mapTo(panel.today_frame, QPoint(0, 0))
            assert origin.x() >= 0
            assert origin.x() + btn.width() <= frame_w
    panel.close()
