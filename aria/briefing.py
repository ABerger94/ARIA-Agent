"""One source-backed daily briefing for startup, scheduler, desktop and phone.

The text is composed from observed data, without a model inventing plans or
headlines. Sources run concurrently under one deadline; missing sources are
reported separately from sources that returned an empty result.
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
        return section('Your calendar is not connected yet.', 'unconfigured')
    events = upcoming(parse_ical(fetch_ical(url, timeout=7)), days=2, now=now)
    today = now.date(); tomorrow = today + timedelta(days=1)
    def describe(event):
        if event['all_day']:
            return event['summary'] + ' (all day)'
        if event['start'].date() < today:
            return f"{event['summary']}, continuing until {_time(event['end'])}"
        return f"{event['summary']} from {_time(event['start'])} to {_time(event['end'])}"
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    todays = [e for e in events if e['start'] < day_start + timedelta(days=1) and e['end'] > day_start]
    tomorrows = [e for e in events if e['start'].date() == tomorrow]
    text = 'Today: ' + '; '.join(describe(e) for e in todays[:6]) + '.' if todays else 'Nothing is on your connected calendar today.'
    if len(todays) > 6:
        text += f' Plus {len(todays) - 6} more events.'
    if tomorrows:
        text += ' Tomorrow: ' + '; '.join(describe(e) for e in tomorrows[:2]) + '.'
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
    from aria.mailview import fetch_messages
    result = fetch_messages(limit=30)
    if not result.get('ok'):
        error = result.get('error', '')
        if "isn't set up" in error or 'not connected' in error:
            return section('Email is not connected yet.', 'unconfigured')
        return section('I could not check your email just now.', 'unavailable')
    recent = []
    for row in result['messages']:
        try:
            stamp = datetime.fromisoformat(row['date'])
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            age = now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)
        except (KeyError, ValueError, TypeError):
            continue
        if timedelta(0) <= age <= timedelta(hours=48) and email_priority(row):
            recent.append(row)
    recent.sort(key=lambda row: (email_priority(row), bool(row.get('unread')), row.get('date', '')), reverse=True)
    count = result.get('unread_total')
    text = f'You have {count} unread emails. ' if isinstance(count, int) else ''
    urgent = [r for r in recent if email_priority(r) == 3]
    possible = [r for r in recent if email_priority(r) == 1 and r.get('unread')]
    if urgent:
        text += 'Important recent subjects: ' + '; '.join(f"{r['sender']}: {r['subject']}" for r in urgent[:3]) + '.'
    elif possible:
        text += 'Recent unread mail worth reviewing: ' + '; '.join(f"{r['sender']}: {r['subject']}" for r in possible[:2]) + '.'
    else:
        text += 'No important subjects stood out in the most recent 30 messages from the last 48 hours.'
    if len(urgent) > 3:
        text += f' There are {len(urgent) - 3} more important subjects in that recent sample.'
    return section(text)


def weather_section(city):
    data = json.loads(_get('https://wttr.in/' + urllib.parse.quote(city) + '?format=j1'))
    cur = data['current_condition'][0]; today = data['weather'][0]
    name = city.split(',')[0]
    desc = (cur.get('weatherDesc') or [{}])[0].get('value', '')
    return section(f"In {name}, it is {cur['temp_F']} degrees Fahrenheit and {desc.lower()}, with a high of {today['maxtempF']} and a low of {today['mintempF']}.",
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
    return {'generated_at': now.isoformat(), 'text': intro + ' ' + ' '.join(s['text'] for s in sections), 'sections': sections}


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
        for source in item['sources']:
            blocks.append(source['title'] + '\n' + source['url'])
    return '\n\n'.join(blocks)
