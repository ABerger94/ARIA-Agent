"""Minimal iCalendar (ICS) fetch + parse for ARIA's live calendar feed.

Covers what Google Calendar's secret iCal URL serves: VEVENTs with
DTSTART/DTEND in UTC (...Z), floating local, TZID, or date-only (all-day)
forms. Recurring RRULE/RDATE/EXDATE events and explicit occurrence overrides are expanded in upcoming().
"""

from __future__ import annotations

import re
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

USER_AGENT = "ARIA-Agent/8.0"


def fetch_ical(url: str, timeout: int = 20) -> str:
    """Download raw ICS text from a (secret) iCal URL."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _unfold(text: str) -> List[str]:
    lines: List[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _unescape(s: str) -> str:
    return (s.replace("\\n", "\n").replace("\\N", "\n")
             .replace("\\,", ",").replace("\\;", ";"))


def _tzid_zone(params: str):
    m = re.search(r"TZID=([^;:]+)", params or "")
    if not m:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(m.group(1))
    except Exception:
        return None


def _parse_dt(value: str, params: str) -> Optional[datetime]:
    """Parse an ICS date/time into a datetime (aware when UTC/TZID known)."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            from datetime import timezone
            return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        mt = re.match(r"(\d{8})T(\d{6})$", value)
        if mt:
            dt = datetime.strptime(mt.group(1) + "T" + mt.group(2), "%Y%m%dT%H%M%S")
            tz = _tzid_zone(params)
            return dt.replace(tzinfo=tz) if tz else dt
        if re.match(r"^\d{8}$", value):  # date-only: all-day
            return datetime.strptime(value, "%Y%m%d")
    except ValueError:
        pass
    return None


def _build_event(props: Dict[str, Tuple[str, str]]) -> Optional[Dict]:
    raw_start = props.get("DTSTART")
    if not raw_start:
        return None
    start = _parse_dt(raw_start[1], raw_start[0])
    if not start:
        return None
    all_day = "T" not in raw_start[1].strip().upper()
    raw_end = props.get("DTEND")
    end = _parse_dt(raw_end[1], raw_end[0]) if raw_end else None
    if end is None:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))

    def txt(name: str) -> str:
        return _unescape(props.get(name, ("", ""))[1]).strip()

    summary = txt("SUMMARY") or "(no title)"
    loc = txt("LOCATION")
    if loc:
        summary = f"{summary} @ {loc}"
    def dates(name):
        entry = props.get(name)
        return [dt for value in entry[1].split(',') if (dt := _parse_dt(value, entry[0]))] if entry else []
    recurrence = props.get('RECURRENCE-ID')
    return {"summary": summary, "description": txt("DESCRIPTION"),
            "start": start, "end": end, "all_day": all_day,
            "uid": txt('UID'), "rrule": txt('RRULE'),
            "rdates": dates('RDATE'), "exdates": dates('EXDATE'),
            "recurrence_id": _parse_dt(recurrence[1], recurrence[0]) if recurrence else None,
            "cancelled": txt('STATUS').upper() == 'CANCELLED'}


def parse_ical(text: str) -> List[Dict]:
    """Parse ICS text into event dicts. Times stay as parsed (see upcoming)."""
    events: List[Dict] = []
    in_event = False
    props: Dict[str, Tuple[str, str]] = {}
    for line in _unfold(text or ""):
        if line == "BEGIN:VEVENT":
            in_event, props = True, {}
        elif line == "END:VEVENT":
            if in_event:
                ev = _build_event(props)
                if ev:
                    events.append(ev)
            in_event = False
        elif in_event and ":" in line:
            prop, _, val = line.partition(":")
            name = prop.split(";")[0].strip().upper()
            if name in ('RDATE', 'EXDATE') and name in props:
                props[name] = (props[name][0], props[name][1] + ',' + val)
            else:
                props[name] = (prop[len(name):], val)
    return events


def _as_local_naive(dt: datetime, local_tz) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(local_tz).replace(tzinfo=None)
    return dt


def upcoming(events: List[Dict], days: int = 1,
             now: Optional[datetime] = None) -> List[Dict]:
    """Events overlapping [local today 00:00, +days), times as naive local."""
    now = now or datetime.now()
    local_tz = now.tzinfo
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    window_end = start_of_today + timedelta(days=max(1, days))
    out = []
    def occurrence_key(event, dt):
        from datetime import timezone
        stamp = dt.astimezone(timezone.utc).isoformat() if dt.tzinfo else dt.isoformat()
        return event.get('uid', ''), stamp
    overrides = {occurrence_key(e, e['recurrence_id']) for e in events
                 if e.get('uid') and e.get('recurrence_id')}
    for e in events:
        if e.get('cancelled'):
            continue
        starts = [e['start']]
        duration = e['end'] - e['start']
        if not e.get('recurrence_id') and (e.get('rrule') or e.get('rdates')):
            from dateutil.rrule import rruleset, rrulestr
            rules = rruleset()
            if e.get('rrule'):
                if re.search(r'FREQ=(SECONDLY|MINUTELY|HOURLY)|BYSECOND=|BYMINUTE=', e['rrule'], re.I):
                    raise ValueError('High-frequency calendar recurrence is not supported.')
                rules.rrule(rrulestr(e['rrule'], dtstart=e['start']))
            else:
                rules.rdate(e['start'])
            for dt in e.get('rdates', []): rules.rdate(dt)
            for dt in e.get('exdates', []): rules.exdate(dt)
            lower, upper = start_of_today - duration, window_end
            if e['start'].tzinfo is not None:
                lower = lower.replace(tzinfo=local_tz).astimezone(e['start'].tzinfo) if local_tz else lower.astimezone(e['start'].tzinfo)
                upper = upper.replace(tzinfo=local_tz).astimezone(e['start'].tzinfo) if local_tz else upper.astimezone(e['start'].tzinfo)
            starts = rules.between(lower, upper, inc=True)
        for occurrence in starts:
            if not e.get('recurrence_id') and occurrence_key(e, occurrence) in overrides:
                continue
            s = _as_local_naive(occurrence, local_tz)
            en = _as_local_naive(occurrence + duration, local_tz)
            if s < window_end and en > start_of_today:
                out.append({**e, "start": s, "end": en})
    return sorted(out, key=lambda e: e["start"])
