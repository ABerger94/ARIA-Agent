"""Shell Home V13 actionable Today cards (2026-10-31, Alek's
order): next calendar event, due/pending scheduled tasks with
Done/Snooze, and the unread mail count — derived purely in
aria/shell_home.today_cards from the seams' own shapes, and
wired in the panel to scheduler ops + the V12 tab callback
(faked here: no real DB, no network).
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
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import importlib
shell_home = importlib.import_module("aria.shell_home")
shell_panel = importlib.import_module("aria.shell_panel")

NOW = datetime(2026, 10, 31, 12, 0)


def _ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


DUE = (5, "once", "take out the trash", 0,
       _ts(NOW - timedelta(minutes=5)))
LATER = (6, "once", "call the dentist", 0,
         _ts(NOW + timedelta(hours=3)))
FAR = (7, "once", "next week thing", 0,
       _ts(NOW + timedelta(days=3)))
RECUR = (8, "interval", "water the plants", 3600,
         _ts(NOW - timedelta(minutes=1)))
ONRET = (9, "on_return", "beer reminder", 0, "9999-12-31 00:00:00")
EVENT = {"summary": "Dentist appointment",
         "start": NOW + timedelta(hours=2), "all_day": False}


# -- pure derivation -------------------------------------------

def test_today_empty_when_nothing_exists():
    model = shell_home.today_cards(NOW, [], None, None)
    assert model["empty"] is True
    model0 = shell_home.today_cards(NOW, [], None, 0)
    assert model0["empty"] is True


def test_today_tasks_due_first_window_and_kinds():
    model = shell_home.today_cards(NOW, [FAR, LATER, RECUR, DUE,
                                         ONRET], None, None)
    prompts = [t["prompt"] for t in model["tasks"]]
    # due first (both DUE and RECUR are due; soonest next_run
    # first among due), then today's later task; FAR is outside
    # the 24h window and ONRET is presence-triggered — both out.
    assert prompts == ["take out the trash", "water the plants",
                       "call the dentist"]
    by_prompt = {t["prompt"]: t for t in model["tasks"]}
    assert by_prompt["take out the trash"]["due"] is True
    assert by_prompt["take out the trash"]["actions"] == \
        ["done", "snooze"]
    # Recurring rows show state only — no fake controls.
    assert by_prompt["water the plants"]["actions"] == []
    assert by_prompt["call the dentist"]["when"] == "today 3:00 PM"


def test_today_event_and_unread():
    model = shell_home.today_cards(NOW, [], EVENT, 4)
    assert model["empty"] is False
    assert model["event"]["summary"] == "Dentist appointment"
    assert model["event"]["when"] == "Sat 2:00 PM"
    assert model["unread"] == 4
    hidden = shell_home.today_cards(NOW, [], None, None)
    assert hidden["unread"] is None


# -- panel wiring -------------------------------------------------

def _panel(state, **overrides):
    calls = {"acted": [], "tab": []}

    def actor(tid, action):
        calls["acted"].append((tid, action))
        if action == "done":
            state["tasks"] = [t for t in state["tasks"]
                              if t[0] != tid]

    kwargs = dict(
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
        on_open_tab=calls["tab"].append,
        tasks_provider=lambda: list(state["tasks"]),
        task_actor=actor,
        today_remote_provider=lambda: dict(state["remote"]))
    kwargs.update(overrides)
    panel = shell_panel.ShellPanel(**kwargs)
    return panel, calls


def _pump(panel):
    thread = panel._today_thread
    if thread is not None:
        thread.join(timeout=5)
    # Two rounds: the first delivers the fetch signal (whose
    # repaint schedules deferred deletes), the second flushes
    # them — stale rows must not leak into the assertions.
    _APP.processEvents()
    _APP.processEvents()


def _rows(panel):
    """[(row label text, [buttons])] in painted order, walking
    the Today layout — never findChildren, which also sees
    rows whose deleteLater is still pending."""
    from PyQt6.QtWidgets import QLabel
    out = []
    for i in range(panel.today_rows.count()):
        row = panel.today_rows.itemAt(i).widget()
        if row is None:
            continue
        lay = row.layout()
        label = lay.itemAt(0).widget()
        btns = [lay.itemAt(j).widget()
                for j in range(1, lay.count())]
        out.append((label.text() if isinstance(label, QLabel)
                     else "", btns))
    return out


def test_panel_today_hidden_when_empty():
    panel, _calls = _panel({"tasks": [],
                            "remote": {"event": None,
                                       "unread": None}})
    _pump(panel)
    assert panel.today_frame.isHidden()


def test_panel_today_rows_and_buttons():
    state = {"tasks": [DUE, LATER],
             "remote": {"event": EVENT, "unread": 2}}
    panel, calls = _panel(state)
    _pump(panel)
    assert not panel.today_frame.isHidden()
    rows = _rows(panel)
    # Painted order: event, due task, later task, mail.
    assert "Dentist appointment" in rows[0][0]
    assert [b.text() for b in rows[0][1]] == ["Open"]
    assert "take out the trash" in rows[1][0]
    assert [b.text() for b in rows[1][1]] == ["Done", "Snooze +1h"]
    assert "call the dentist" in rows[2][0]
    assert "2 unread emails" in rows[3][0]
    # Done on the due task runs the actor and the row goes.
    rows[1][1][0].click()
    assert calls["acted"] == [(5, "done")]
    assert panel._today_model["tasks"][0]["prompt"] == \
        "call the dentist"
    # Event Open jumps to the Calendar tab via the V12 callback.
    _rows(panel)[0][1][0].click()
    assert calls["tab"] == ["Calendar"]
