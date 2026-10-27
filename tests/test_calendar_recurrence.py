import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location('ical_test', Path(__file__).parents[1] / 'aria' / 'ical.py')
i = importlib.util.module_from_spec(spec); spec.loader.exec_module(i)


def test_daily_shift_exdate_and_rdate():
    text = '''BEGIN:VCALENDAR
BEGIN:VEVENT
UID:shift
DTSTART:20261020T160000Z
DTEND:20261020T220000Z
RRULE:FREQ=DAILY;COUNT=10
EXDATE:20261027T160000Z
RDATE:20261027T170000Z
SUMMARY:Little Caesars
END:VEVENT
END:VCALENDAR'''
    rows = i.upcoming(i.parse_ical(text), 1, datetime(2026, 10, 27, 9, tzinfo=timezone.utc))
    assert len(rows) == 1 and rows[0]['start'].hour == 17
    assert rows[0]['summary'] == 'Little Caesars'


def test_weekly_recurrence_preserves_local_time_across_dst():
    text = '''BEGIN:VCALENDAR
BEGIN:VEVENT
UID:shift
DTSTART;TZID=America/New_York:20261025T160000
DTEND;TZID=America/New_York:20261025T220000
RRULE:FREQ=WEEKLY;BYDAY=SU
SUMMARY:Weekly shift
END:VEVENT
END:VCALENDAR'''
    now = datetime(2026, 10, 31, 9, tzinfo=ZoneInfo('America/New_York'))
    rows = i.upcoming(i.parse_ical(text), 2, now)
    assert len(rows) == 1 and rows[0]['start'] == datetime(2026, 11, 1, 16)


def test_cancelled_occurrence_is_not_briefed():
    text = '''BEGIN:VCALENDAR
BEGIN:VEVENT
UID:shift
DTSTART:20261020T160000Z
DTEND:20261020T220000Z
RRULE:FREQ=DAILY;COUNT=10
SUMMARY:Work
END:VEVENT
BEGIN:VEVENT
UID:shift
RECURRENCE-ID:20261027T160000Z
DTSTART:20261027T160000Z
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR'''
    assert i.upcoming(i.parse_ical(text), 1, datetime(2026, 10, 27, tzinfo=timezone.utc)) == []


def test_overnight_event_and_all_day_event_are_included():
    text = '''BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261026T230000Z
DTEND:20261027T080000Z
SUMMARY:Night shift
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261027
DTEND;VALUE=DATE:20261028
SUMMARY:All day
END:VEVENT
END:VCALENDAR'''
    rows = i.upcoming(i.parse_ical(text), 1, datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert len(rows) == 2
