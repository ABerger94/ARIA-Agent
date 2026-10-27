"""One source-backed daily briefing for startup, scheduler, desktop and phone.

The text is composed from observed data, without a model inventing plans or
headlines. Sources run concurrently under one deadline; missing sources are
reported separately from sources that returned an empty result.

The spoken composition follows Mark-LVI's morning briefing (Alek's
call, 2026-10-27 — 'that briefing is still better'): 'You've got:
Dentist (3 PM to 4 PM).' for the calendar, 'On email: N unread —
notable: Sender regarding 'Subject'' for the inbox (automated
senders counted, never named; Google's own security alerts
skipped), 'In Dundalk it's 61 degrees and clear, with a high of…'
for the weather, and a single 'In the news:' line of headline
titles instead of both news sections read aloud. The full
per-section texts remain in the report for the Briefing tab.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

_LAST = None
_CACHE_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()
_TITLES = {'schedule': 'Today’s schedule', 'email': 'Important email',
           'weather': 'Local weather', 'local_news': 'Local news', 'world_news': 'World news'}


def section(text, status='ok', sources=None):
    return {'text': text, 'status': status, 'sources': sources or []}


def _get(url, timeout=7):
    req = urllib.request.Request(url, headers={'User-Agent': 'ARIA-Agent/1.0 daily briefing'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(2_000_000)


def _time(dt):
    return f"{dt.hour % 12 or 12}" + (f":{dt.minute:02d}" if dt.minute else '') + (' PM' if dt.hour >= 12 else ' AM')


def calendar_section(now):
    from aria import config, scheduler
    from aria.ical import fetch_ical, parse_ical, upcoming
    url = (config.key_get('ICAL_URL')[0] or '').strip()
    if not url or url == 'INSERT':
        saved = [row for row in scheduler._load_schedule() if row.get('date') == now.strftime('%Y-%m-%d')]
        if saved:
            return section('Your live calendar is not connected. On your saved schedule today: ' + scheduler._entries_line(saved) + '.', 'partial')
        return section("Your calendar isn't connected yet.", 'unconfigured')
    events = upcoming(parse_ical(fetch_ical(url, timeout=7)), days=2, now=now)
    today = now.date(); tomorrow = today + timedelta(days=1)
    def describe(event):
        # Mark-LVI spoken form: 'Dentist (3 PM to 4 PM)' — the time
        # rides in parentheses instead of a 'from X to Y' clause.
        if event['all_day']:
            return event['summary']
        if event['start'].date() < today:
            return f"{event['summary']} (until {_time(event['end'])})"
        return f"{event['summary']} ({_time(event['start'])} to {_time(event['end'])})"
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    todays = [e for e in events if e['start'] < day_start + timedelta(days=1) and e['end'] > day_start]
    tomorrows = [e for e in events if e['start'].date() == tomorrow]
    if todays:
        text = "You've got: " + ', '.join(describe(e) for e in todays[:6]) + '.'
        if len(todays) > 6:
            text += f' Plus {len(todays) - 6} more.'
    else:
        text = 'Nothing on the schedule today.'
    if tomorrows:
        text += ' Tomorrow: ' + ', '.join(describe(e) for e in tomorrows[:2]) + '.'
    # Never put the secret iCal URL into the briefing or its sources.
    return section(text)


def email_priority(row):
    """Conservative header-only ranking, never a claim about unread body text."""
    subject = row.get('subject', '').casefold()
    sender = row.get('address', '').casefold()
    if re.search(r'\b(declined|low balance|security alert|suspicious|fraud|overdue|past due|action required|interview|application update|appointment|rescheduled|cancelled|canceled|payment failed)\b', subject):
        return 3
    if re.search(r'\b(sale|discount|coupon|newsletter|digest|trending|reward|points|just posted|job alert|job openings|terms of use|policies)\b', subject) or re.search(r'jobalert|linkedin|subscribe|marketing|newsletter', sender):
        return 0
    if re.search(r'no.?reply|donotreply|notification|updates', sender):
        return 0
    return 1  # Possible personal mail; explicitly labelled for review below.


def email_section(now):
    """Mark-LVI spoken form over this build's mailview rows:
    'On email: N unread — notable: Sender regarding 'Subject'; …'
    with this build's noise filter kept (automated senders never
    get named) and LVI's own rules on top: Google's security alerts
    are skipped, an all-automated sample is said to be one, and an
    empty inbox is 'Inbox is clear.' — a claim only made when the
    unread count actually is zero."""
    from aria.mailview import fetch_messages
    result = fetch_messages(limit=30)
    if not result.get('ok'):
        error = result.get('error', '')
        if "isn't set up" in error or 'not connected' in error:
            return section("Email isn't connected yet.", 'unconfigured')
        return section("I couldn't check your email just now.", 'unavailable')
    count = result.get('unread_total')
    if count == 0:
        return section('Inbox is clear.')
    sample = []
    for row in result['messages']:
        if not row.get('unread'):
            continue
        try:
            stamp = datetime.fromisoformat(row['date'])
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            age = now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)
        except (KeyError, ValueError, TypeError):
            continue
        if timedelta(0) <= age <= timedelta(hours=48):
            sample.append(row)
    sample.sort(key=lambda row: row.get('date', ''), reverse=True)
    sample = sample[:10]
    notable = []
    for row in sample:
        if not email_priority(row):
            continue                    # automated: counted, never named
        sender = str(row.get('sender', '')).strip().strip('"')
        subject = str(row.get('subject', '')).strip()
        if 'security alert' in subject.casefold() and 'google' in (sender + ' ' + str(row.get('address', ''))).casefold():
            continue                    # LVI: Google's own alerts are noise
        notable.append((sender, subject))
    # LVI names the first actionable unread in INBOX (recency)
    # order. This build used to re-sort by its priority regex,
    # which is exactly what headlined 'Declined transaction'
    # notices over personal mail in the greeting Alek rejected —
    # the regex now only decides automated-or-not, never order.
    head = f'On email: {count} unread' if isinstance(count, int) else 'On email'
    if notable:
        named = '; '.join(f"{sender} regarding '{subject}'" for sender, subject in notable[:2])
        rest = f' and {len(notable) - 2} other(s)' if len(notable) > 2 else ''
        return section(f'{head} — notable: {named}{rest}.')
    if sample:
        return section(f'{head}, mostly automated alerts.')
    return section(head + '.')


def weather_section(city):
    data = json.loads(_get('https://wttr.in/' + urllib.parse.quote(city) + '?format=j1'))
    cur = data['current_condition'][0]; today = data['weather'][0]
    name = city.split(',')[0]
    desc = (cur.get('weatherDesc') or [{}])[0].get('value', '')
    cond = f' and {desc}' if desc else ''
    return section(f"In {name} it's {cur['temp_F']} degrees{cond}, with a high of {today['maxtempF']} and a low of {today['mintempF']}.",
                   sources=[{'title': 'Weather', 'url': 'https://wttr.in/' + urllib.parse.quote(city)}])


def parse_headlines(xml, now, limit=3):
    root = ET.fromstring(xml)
    results = []; seen = set()
    for item in root.findall('.//item'):
        title = ' '.join((item.findtext('title') or '').split())
        link = (item.findtext('link') or '').strip()
        try:
            published = parsedate_to_datetime(item.findtext('pubDate') or '')
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            age = now.astimezone(timezone.utc) - published.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if not timedelta(minutes=-5) <= age <= timedelta(hours=48):
            continue
        if not title or not link.startswith(('http://', 'https://')):
            continue
        normalized = re.sub(r'[^\w]+', ' ', title.casefold()).strip()
        words = set(normalized.split())
        if normalized in seen or any(len(words & row['_words']) / max(1, len(words | row['_words'])) > .65 for row in results):
            continue
        seen.add(normalized)
        results.append({'title': title[:250], 'url': link, 'published': published.isoformat(), '_words': words})
        if len(results) >= limit:
            break
    return [{k: v for k, v in row.items() if k != '_words'} for row in results]


def news_section(now, kind, city, topics):
    if kind == 'local_news':
        query = city + ' Baltimore County local news when:1d'
        url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})
        label = 'Local headlines'
    elif topics:
        url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': '(' + ' OR '.join(topics[:4]) + ') world news when:1d', 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})
        label = 'World headlines'
    else:
        url = 'https://feeds.bbci.co.uk/news/world/rss.xml'; label = 'World headlines'
    items = parse_headlines(_get(url), now, limit=2 if kind == 'local_news' else 3)
    if not items:
        return section('No recent ' + ('local' if kind == 'local_news' else 'world') + ' headlines were returned by the news source.', 'empty')
    return section(label + ': ' + '; '.join(row['title'] for row in items) + '.', sources=items)


def compose(now, sources, name='Alek', timeout=14):
    """Bounded parallel collection; injected sources keep tests offline."""
    completed = queue.Queue()
    def run(key, fn):
        try:
            value = fn()
        except Exception:
            value = section('I could not check ' + _TITLES[key].lower() + ' just now.', 'unavailable')
        completed.put((key, value))
    for key, fn in sources.items():
        threading.Thread(target=run, args=(key, fn), daemon=True).start()
    results = {}; deadline = time.monotonic() + timeout
    while len(results) < len(sources):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            key, value = completed.get(timeout=remaining)
            results[key] = value
        except queue.Empty:
            break
    part = 'morning' if now.hour < 12 else 'afternoon' if now.hour < 18 else 'evening'
    intro = f"Good {part}, {name}. Today is {now.strftime('%A, %B')} {now.day}."
    sections = []
    for key, title in _TITLES.items():
        value = results.get(key, section(title + ' is taking too long to load; I skipped it for now.', 'timeout'))
        sections.append({'key': key, 'title': title, **value})
    # The SPOKEN text follows Mark-LVI's composition: calendar,
    # email, weather, then ONE 'In the news:' line of headline
    # titles (local first, three total). The full local/world
    # sections stay in 'sections' for the Briefing tab — speaking
    # both blocks verbatim made the greeting a five-part report.
    by = {s['key']: s for s in sections}
    spoken = [by[k]['text'] for k in ('schedule', 'email', 'weather')]
    titles = []
    for key in ('local_news', 'world_news'):
        for item in by[key].get('sources', []):
            t = str(item.get('title', '')).strip()
            if t and t not in titles:
                titles.append(t)
    if titles:
        spoken.append('In the news: ' + '. '.join(titles[:3]) + '.')
    else:
        ok_texts = [by[k]['text'] for k in ('local_news', 'world_news')
                    if by[k]['status'] == 'ok' and by[k]['text']]
        if ok_texts:
            spoken.extend(ok_texts)
        elif any(by[k]['status'] != 'ok' for k in ('local_news', 'world_news')):
            spoken.append("I couldn't fetch the news right now.")
    return {'generated_at': now.isoformat(), 'text': intro + ' ' + ' '.join(spoken), 'sections': sections}


def latest_briefing():
    with _CACHE_LOCK:
        return _LAST


def build_briefing(force=False):
    global _LAST
    from aria import config
    with _BUILD_LOCK:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(str(config.get_setting('briefing_timezone', 'America/New_York'))))
        except Exception:
            now = datetime.now().astimezone()
        previous = latest_briefing()
        if not force and previous:
            age = now - datetime.fromisoformat(previous['generated_at'])
            if timedelta(0) <= age < timedelta(seconds=60):
                return previous
        city = str(config.get_setting('briefing_city', 'Dundalk, Maryland'))
        topics = config.get_setting('briefing_world_topics', [])
        if not isinstance(topics, list): topics = []
        topics = [str(t) for t in topics if str(t).strip()]
        sources = {'schedule': lambda: calendar_section(now), 'email': lambda: email_section(now),
                   'weather': lambda: weather_section(city),
                   'local_news': lambda: news_section(now, 'local_news', city, topics),
                   'world_news': lambda: news_section(now, 'world_news', city, topics)}
        report = compose(now, sources, name=str(config.get_setting('briefing_name', 'Alek')))
        with _CACHE_LOCK:
            _LAST = report
        return report


def report_text(report):
    if not report:
        return 'Your daily briefing will appear here after startup. Select Refresh to generate one.'
    blocks = ['DAILY BRIEFING  ·  ' + report['generated_at'], report['text'].split('. ', 2)[0] + '.']
    for item in report['sections']:
        blocks.append(item['title'].upper() + '\n' + item['text'])
        if item['key'] in ('local_news', 'world_news'):
            # Alek (2026-10-27): no source listings in the
            # briefing — the headlines are the content; repeating
            # each one as a title+URL block under its section is
            # noise. (The weather section keeps its single source
            # link; the news sections' sources ARE the headlines.)
            continue
        for source in item['sources']:
            blocks.append(source['title'] + '\n' + source['url'])
    return '\n\n'.join(blocks)
