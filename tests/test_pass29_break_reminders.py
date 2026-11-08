"""Pass 29 #54: the break-reminder toggle must reach its consumer.

Bug: proactive.py bound BREAK_REMINDERS by value at import, while
tool_break_reminders rebound only scheduler's module global — the
toggle never changed what the heartbeat loop read. The preference
also died with the process: nothing persisted it.

Fix contract:
- proactive reads the live preference through
  scheduler.break_reminders_enabled() at decision time;
- the tool persists the preference in settings, so a restart keeps
  it; the getter prefers the persisted value over the import-time
  default;
- the 1.5 h session / 90 min activity windows are unchanged.
"""
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


_pkg("aria", PKG)
_pkg("aria.tools", os.path.join(PKG, "tools"))
_pkg("aria.agent", os.path.join(PKG, "agent"))
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
sys.modules["aria.speech"] = types.ModuleType("aria.speech")

import pytest  # noqa: E402

from aria import scheduler  # noqa: E402
from aria.agent import proactive  # noqa: E402


@pytest.fixture()
def settings(monkeypatch):
    """A fake settings store standing in for the real settings file."""
    store = {}

    def fake_get(key, default=None):
        return store.get(key, default)

    def fake_set(key, value):
        store[key] = value
        return True

    monkeypatch.setattr(scheduler._config_silent, "get_setting", fake_get)
    monkeypatch.setattr(scheduler._config_silent, "set_setting", fake_set)
    monkeypatch.setattr(proactive, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(proactive, "spine_append", lambda *a, **k: None)
    monkeypatch.setattr(scheduler, "BREAK_REMINDERS", True)
    return store


def _due_kwargs():
    now = time.time()
    return {"now": now, "session_start": now - 2 * 3600,
            "last_activity": now - 600}


def test_default_is_enabled_with_no_persisted_preference(settings):
    assert scheduler.break_reminders_enabled() is True
    assert proactive._break_due(**_due_kwargs()) is True


def test_toggle_off_stops_break_suggestions(settings):
    assert scheduler.tool_break_reminders(False) == "Break reminders disabled."
    assert settings == {"break_reminders": False}  # persisted
    assert scheduler.break_reminders_enabled() is False
    assert proactive._break_due(**_due_kwargs()) is False


def test_toggle_on_resumes_break_suggestions(settings):
    scheduler.tool_break_reminders(False)
    assert proactive._break_due(**_due_kwargs()) is False
    assert scheduler.tool_break_reminders(True) == "Break reminders enabled."
    assert settings == {"break_reminders": True}
    assert scheduler.break_reminders_enabled() is True
    assert proactive._break_due(**_due_kwargs()) is True


def test_preference_survives_a_fresh_import(settings):
    # Simulate a restart: the module global is back to its
    # import-time default (True) but the persisted setting says off.
    scheduler.tool_break_reminders(False)
    scheduler.BREAK_REMINDERS = True  # fresh-import default
    assert scheduler.break_reminders_enabled() is False
    assert proactive._break_due(**_due_kwargs()) is False
    # And the other way: a stale global False cannot override a
    # persisted True.
    scheduler.tool_break_reminders(True)
    scheduler.BREAK_REMINDERS = False
    assert scheduler.break_reminders_enabled() is True
    assert proactive._break_due(**_due_kwargs()) is True


def test_proactive_no_longer_binds_a_stale_copy(settings):
    # The by-value import is gone: toggling the scheduler global
    # directly (as the old tests do) still flows through the getter.
    assert getattr(proactive, "BREAK_REMINDERS", None) is None
    scheduler.BREAK_REMINDERS = False
    assert proactive._break_due(**_due_kwargs()) is False
    scheduler.BREAK_REMINDERS = True
    assert proactive._break_due(**_due_kwargs()) is True


def test_session_and_activity_windows_unchanged(settings):
    now = time.time()
    # Session under 1.5 h: no nudge even when enabled.
    assert proactive._break_due(now, now - 3600, now - 600) is False
    # Idle over 90 min: no nudge even when enabled.
    assert proactive._break_due(now, now - 2 * 3600, now - 6000) is False
    # Both windows met: nudge.
    assert proactive._break_due(now, now - 2 * 3600, now - 600) is True


def test_string_toggle_values_still_work(settings):
    assert scheduler.tool_break_reminders("off") == "Break reminders disabled."
    assert settings == {"break_reminders": False}
    assert scheduler.tool_break_reminders("on") == "Break reminders enabled."
    assert settings == {"break_reminders": True}
