"""Pass 29 #53: OS reminders mishandled timezone-aware inputs and
near-term minute rounding.

Two defects, one handler. First, _parse_when returned
datetime.fromisoformat(at_raw) with no tz handling, so
2026-11-07T12:34:26+00:00 parsed tz-aware and the next line —
`if when <= datetime.now():` — compared aware against naive
and raised TypeError, uncaught in tool_os_reminder. Second, a
request 6 seconds ahead passed validation (when > now) but the
Windows path sent "/ST", f"{when:%H:%M}" to schtasks — the
requested minute, already in the past by fire time.

The fix: _parse_when normalizes tz-aware input to naive local
time (offsets are converted, not rejected — documented on the
function), and the minute-precision schedulers (schtasks /ST,
`at`) receive the requested time rounded forward to the next
whole minute, with the exact timestamp sent validated before
use. The systemd-run path keeps second precision. Date
rollover (23:59:30 -> 00:00 next day) falls out of the
arithmetic.
"""
import importlib
import os
import sys
import types
from datetime import datetime, timedelta, timezone

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

gap = importlib.import_module("aria.tools.gaptools")


def _win32(monkeypatch, tmp_path, calls):
    monkeypatch.setattr(gap, "_os_name", lambda: "win32")
    monkeypatch.setattr(gap, "_workspace_dir", lambda: tmp_path)

    def ok_run(cmd):
        calls.append(cmd)
        return 0, "SUCCESS"
    monkeypatch.setattr(gap, "_run", ok_run)


def _scheduled(cmd):
    """(date, time) the schtasks call was registered for."""
    sd = cmd[cmd.index("/SD") + 1]
    st = cmd[cmd.index("/ST") + 1]
    return datetime.strptime(sd + " " + st, "%m/%d/%Y %H:%M")


# ── Parsing: tz-aware input ────────────────────────────────────────

def test_parse_when_tz_aware_normalizes_to_naive_local():
    when, err = gap._parse_when(
        {"at": "2026-11-07T12:34:26+00:00"})
    assert err is None
    assert when.tzinfo is None
    # Same instant as the UTC reading, expressed in local time.
    assert when == datetime(2026, 11, 7, 12, 34, 26,
                            tzinfo=timezone.utc).astimezone().replace(
                                tzinfo=None)


def test_parse_when_same_instant_different_offsets_agree():
    a, _ = gap._parse_when({"at": "2026-11-07T12:34:26+00:00"})
    b, _ = gap._parse_when({"at": "2026-11-07T14:34:26+02:00"})
    assert a == b


def test_parse_when_z_suffix_is_utc():
    when, err = gap._parse_when({"at": "2026-11-07T12:34:26Z"})
    assert err is None and when.tzinfo is None
    assert when == datetime(2026, 11, 7, 12, 34, 26,
                            tzinfo=timezone.utc).astimezone().replace(
                                tzinfo=None)


def test_parse_when_naive_input_unchanged():
    when, err = gap._parse_when({"at": "2026-11-07T12:34"})
    assert err is None
    assert when == datetime(2026, 11, 7, 12, 34)


# ── Rounding ───────────────────────────────────────────────────────

def test_round_up_to_minute():
    assert gap._round_up_to_minute(
        datetime(2026, 11, 7, 12, 34)) == datetime(2026, 11, 7, 12, 34)
    assert gap._round_up_to_minute(
        datetime(2026, 11, 7, 12, 34, 26)) == datetime(
            2026, 11, 7, 12, 35)
    assert gap._round_up_to_minute(
        datetime(2026, 11, 7, 12, 34, 0, 500000)) == datetime(
            2026, 11, 7, 12, 35)


def test_round_up_to_minute_rolls_over_midnight():
    assert gap._round_up_to_minute(
        datetime(2026, 11, 7, 23, 59, 30)) == datetime(
            2026, 11, 8, 0, 0)


# ── End to end: the timestamp actually sent ────────────────────────

def test_tz_aware_input_no_longer_raises(monkeypatch, tmp_path):
    calls = []
    _win32(monkeypatch, tmp_path, calls)
    before = datetime.now()
    future = (datetime.now(timezone.utc) + timedelta(hours=2))
    out = gap.tool_os_reminder({
        "message": "tz reminder",
        "at": future.strftime("%Y-%m-%dT%H:%M:%S+00:00")})
    assert out.startswith("status=done"), out
    assert _scheduled(calls[0]) > before


def test_six_seconds_ahead_never_registers_a_past_minute(
        monkeypatch, tmp_path):
    calls = []
    _win32(monkeypatch, tmp_path, calls)
    before = datetime.now()
    # Six seconds ahead: the old code sent this minute to
    # schtasks, already past by fire time.
    at = (datetime.now() + timedelta(seconds=6)).strftime(
        "%Y-%m-%dT%H:%M:%S")
    out = gap.tool_os_reminder({"message": "almost now", "at": at})
    assert out.startswith("status=done"), out
    scheduled = _scheduled(calls[0])
    assert scheduled > before, (scheduled, before)
    assert scheduled == gap._round_up_to_minute(
        datetime.strptime(at, "%Y-%m-%dT%H:%M:%S"))


def test_tz_aware_past_time_is_a_clean_refusal_not_a_crash(
        monkeypatch, tmp_path):
    calls = []
    _win32(monkeypatch, tmp_path, calls)
    past = (datetime.now(timezone.utc) - timedelta(hours=1))
    out = gap.tool_os_reminder({
        "message": "too late",
        "at": past.strftime("%Y-%m-%dT%H:%M:%S+00:00")})
    assert out.startswith("status=failed"), out
    assert "has passed" in out
    assert calls == []


def test_delay_minutes_rounds_forward_on_win32(monkeypatch, tmp_path):
    calls = []
    _win32(monkeypatch, tmp_path, calls)
    before = datetime.now()
    out = gap.tool_os_reminder({"message": "Soon", "delay_minutes": 30})
    assert out.startswith("status=done"), out
    assert _scheduled(calls[0]) > before


def test_systemd_run_keeps_second_precision(monkeypatch, tmp_path):
    monkeypatch.setattr(gap, "_os_name", lambda: "linux")
    monkeypatch.setattr(gap, "_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(
        gap.shutil, "which",
        lambda name: "/usr/bin/systemd-run"
        if name == "systemd-run" else None)
    calls = []
    monkeypatch.setattr(gap, "_run",
                        lambda cmd: calls.append(cmd) or (0, ""))
    when = datetime.now() + timedelta(hours=2)
    out = gap.tool_os_reminder({
        "message": "precise",
        "at": when.strftime("%Y-%m-%dT%H:%M:%S")})
    assert out.startswith("status=done"), out
    assert calls[0][0] == "systemd-run"
    assert calls[0][2] == (
        f"--on-calendar={when:%Y-%m-%d %H:%M:%S}")
