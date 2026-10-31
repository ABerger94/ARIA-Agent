"""Shell Home V11 action cards (2026-10-31, Alek's order):
pending approvals and delivered-but-unacknowledged reminders
surface IN Shell Home with working controls.

Two layers under test:
- the pure card derivation in aria/shell_home.py
  (approval_card / reminder_card over the real seams'
  snapshot shapes), and
- the panel wiring with fake providers/resolvers: cards show
  only while something is pending, and the buttons call the
  resolution seams with the painted card's token — the same
  approve_token / deny_token and acknowledge paths the
  dashboard banner and voice tools use (faked here, so no
  test executes a stashed tool call).
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
shell_home = importlib.import_module("aria.shell_home")
shell_panel = importlib.import_module("aria.shell_panel")

NOW = datetime(2026, 10, 31, 1, 20)
ENTRY = {"token": "tok1", "tool": "launch_app",
         "desc": "launch app 'Steam'", "age_s": 42}
ENTRY2 = {"token": "tok2", "tool": "run_command",
          "desc": "run 'del x'", "age_s": 5}
CARD = {"tid": 7, "file": "reminder-7.txt",
        "message": "get another beer",
        "delivered_at": "2026-10-31T01:10:00"}


# -- pure derivation -------------------------------------------

def test_approval_card_none_when_nothing_pending():
    assert shell_home.approval_card([]) is None
    assert shell_home.approval_card(None) is None
    assert shell_home.approval_card([{"desc": "no token"}]) is None


def test_approval_card_takes_oldest_and_counts():
    card = shell_home.approval_card([ENTRY, ENTRY2])
    assert card["token"] == "tok1"
    assert card["tool"] == "launch_app"
    assert card["desc"] == "launch app 'Steam'"
    assert card["age_s"] == 42
    assert card["pending"] == 2


def test_reminder_card_none_when_nothing_waiting():
    assert shell_home.reminder_card([]) is None
    assert shell_home.reminder_card(None) is None
    assert shell_home.reminder_card([{"message": "  "}]) is None


def test_reminder_card_first_message_and_count():
    card = shell_home.reminder_card(
        [CARD, {"tid": 8, "message": "second one"}])
    assert card["count"] == 2
    assert card["message"] == "get another beer"


# -- panel wiring ------------------------------------------------

def _panel(state, **overrides):
    calls = {"resolved": [], "acks": 0}

    def ack():
        calls["acks"] += 1
        state["reminders"] = []

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
        approval_provider=lambda: list(state["approvals"]),
        approval_resolver=lambda tok, ok: calls["resolved"].append(
            (tok, ok)),
        reminder_provider=lambda: list(state["reminders"]),
        reminder_ack=ack)
    kwargs.update(overrides)
    return shell_panel.ShellPanel(**kwargs), calls


def test_no_cards_when_nothing_pending():
    panel, _calls = _panel({"approvals": [], "reminders": []})
    assert panel.approval_frame.isHidden()
    assert panel.reminder_frame.isHidden()


def test_approval_card_shows_oldest_with_desc():
    panel, _calls = _panel(
        {"approvals": [ENTRY, ENTRY2], "reminders": []})
    assert not panel.approval_frame.isHidden()
    assert "launch app 'Steam'" in panel.approval_text.text()
    assert "+1 more waiting" in panel.approval_text.text()
    assert panel.reminder_frame.isHidden()


def test_approve_button_resolves_painted_token():
    panel, calls = _panel(
        {"approvals": [ENTRY], "reminders": []})
    panel.approve_btn.click()
    assert calls["resolved"] == [("tok1", True)]


def test_deny_button_resolves_painted_token():
    panel, calls = _panel(
        {"approvals": [ENTRY], "reminders": []})
    panel.deny_btn.click()
    assert calls["resolved"] == [("tok1", False)]


def test_reminder_card_shows_and_got_it_acks():
    state = {"approvals": [], "reminders": [CARD]}
    panel, calls = _panel(state)
    assert not panel.reminder_frame.isHidden()
    assert "get another beer" in panel.reminder_text.text()
    panel.gotit_btn.click()
    assert calls["acks"] == 1
    # The ack seam cleared the fake store; the card repaints gone.
    assert panel.reminder_frame.isHidden()


def test_refresh_picks_up_new_pending():
    state = {"approvals": [], "reminders": []}
    panel, _calls = _panel(state)
    assert panel.approval_frame.isHidden()
    state["approvals"] = [ENTRY2]
    panel.refresh_actions()
    assert not panel.approval_frame.isHidden()
    assert "run 'del x'" in panel.approval_text.text()
