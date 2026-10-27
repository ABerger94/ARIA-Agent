"""Calendar iCal parser + recurrence expansion (ported behaviour).

Pins: UTC/TZID/date-only forms parse; folded lines and escaped commas
come through; a LOCATION joins the summary; RRULE events expand into
the upcoming() window (COUNT/UNTIL honoured, EXDATE removes, RDATE
adds); a missing python-dateutil degrades to first-occurrence-only
instead of breaking the calendar.
"""
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg

from aria import ical  # noqa: E402

SAMPLE = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART:20261002T200000Z\r\n"
    "DTEND:20261003T010000Z\r\n"
    "SUMMARY:Dock of the Bay \\, Wait\r\n"
    "LOCATION:Sparrows Point\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "DTSTART;VALUE=DATE:20261004\r\n"
    "DTEND;VALUE=DATE:20261005\r\n"
    "SUMMARY:All Day Thing\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _now():
    return datetime(2026, 10, 2, 12, 0, 0)


def test_parse_forms_and_text():
    events = ical.parse_ical(SAMPLE)
    assert len(events) == 2
    first = events[0]
    assert first['summary'] == 'Dock of the Bay , Wait @ Sparrows Point'
    assert first['start'].tzinfo is not None
    assert events[1]['all_day'] is True


def test_upcoming_window_and_sort():
    out = ical.upcoming(ical.parse_ical(SAMPLE), days=3, now=_now())
    assert [e['summary'].split(' @')[0] for e in out] == [
        'Dock of the Bay , Wait', 'All Day Thing']
    assert all(e['start'].tzinfo is None for e in out)   # naive local


def _weekly_feed(extra=''):
    monday = _now() - timedelta(days=18)      # a Monday, 18 days back
    stamp = monday.strftime('%Y%m%dT150000Z')
    end = (monday + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')
    return (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
        f"DTSTART:{stamp}\r\nDTEND:{end}\r\n"
        "RRULE:FREQ=WEEKLY\r\n"
        f"{extra}"
        "SUMMARY:Weekly Sync\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def test_rrule_expands_into_window():
    events = ical.parse_ical(_weekly_feed())
    assert events[0].get('rrule') == 'FREQ=WEEKLY'
    out = ical.upcoming(events, days=14, now=_now())
    starts = [e['start'] for e in out if e['summary'] == 'Weekly Sync']
    assert len(starts) == 2                    # two Mondays in 14 days
    assert all(s.weekday() == 0 for s in starts)
    # 15:00Z expressed in LOCAL time — the expectation must be
    # derived, not hardcoded to EDT: on a UTC host (the CI runner)
    # local IS UTC and the hour is 15. (Run 41's failure, 2026-10-27:
    # this assert assumed the laptop's timezone.)
    from datetime import timezone as _tz
    expected_hour = datetime(2026, 10, 5, 15, 0,
                             tzinfo=_tz.utc).astimezone().hour
    assert all(s.hour == expected_hour for s in starts)


def test_rrule_count_and_exdate():
    monday = _now() - timedelta(days=18)
    ex = (monday + timedelta(days=21)).strftime('%Y%m%dT150000Z')
    feed = _weekly_feed(
        f"EXDATE:{ex}\r\n").replace('RRULE:FREQ=WEEKLY',
                                    'RRULE:FREQ=WEEKLY;COUNT=5')
    out = ical.upcoming(ical.parse_ical(feed), days=30, now=_now())
    starts = [e['start'] for e in out if e['summary'] == 'Weekly Sync']
    # COUNT=5: Sep 14 / 21 / 28 (past), Oct 5 (EXDATE'd), Oct 12 —
    # only Oct 12 survives inside the window.
    assert len(starts) == 1
    assert starts[0].day == 12                 # Oct 12 2026, a Monday


def test_rrule_without_dateutil_degrades(monkeypatch):
    monkeypatch.setitem(sys.modules, 'dateutil', None)
    monkeypatch.setitem(sys.modules, 'dateutil.rrule', None)
    out = ical.upcoming(ical.parse_ical(_weekly_feed()), days=14,
                        now=_now())
    # First occurrence is 18 days in the past: nothing shows, and
    # crucially nothing raises — the calendar survives the missing dep.
    assert [e for e in out if e['summary'] == 'Weekly Sync'] == []


def test_malformed_rrule_degrades():
    feed = _weekly_feed().replace('RRULE:FREQ=WEEKLY', 'RRULE:FREQ=NOPE;;')
    out = ical.upcoming(ical.parse_ical(feed), days=14, now=_now())
    assert [e for e in out if e['summary'] == 'Weekly Sync'] == []
