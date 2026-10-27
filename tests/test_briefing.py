import importlib.util
import sys
import threading
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('briefing_test', ROOT / 'aria' / 'briefing.py')
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
NOW = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)


def sources():
    return {key: lambda k=key: b.section(k + ' observed data') for key in b._TITLES}


def test_one_composition_has_all_requested_sections():
    report = b.compose(NOW, sources())
    assert report['text'].startswith('Good morning, Alek. Today is Tuesday, October 27.')
    assert [s['key'] for s in report['sections']] == list(b._TITLES)
    assert all(s['text'] in report['text'] for s in report['sections'])
    assert 'tidied my memory' not in report['text']


def test_slow_and_failed_sources_do_not_block_or_claim_empty_results():
    funcs = sources(); release = threading.Event()
    funcs['schedule'] = lambda: release.wait(1)
    def fail(): raise OSError('offline')
    funcs['email'] = fail
    start = time.monotonic()
    report = b.compose(NOW, funcs, timeout=.03)
    release.set()
    assert time.monotonic() - start < .3
    by_key = {s['key']: s for s in report['sections']}
    assert by_key['schedule']['status'] == 'timeout'
    assert by_key['email']['status'] == 'unavailable'
    assert 'Inbox is clear' not in report['text']
    assert by_key['world_news']['status'] == 'ok'


def test_recent_important_email_precedes_promotions(monkeypatch):
    pkg = types.ModuleType('aria'); pkg.__path__ = [str(ROOT / 'aria')]
    monkeypatch.setitem(sys.modules, 'aria', pkg)
    mail = types.ModuleType('aria.mailview')
    def row(sender, address, subject, age=1):
        return dict(sender=sender, address=address, subject=subject, unread=True,
                    date=(NOW - timedelta(hours=age)).isoformat())
    mail.fetch_messages = lambda **kw: {'ok': True, 'unread_total': 10, 'messages': [
        row('Plex', 'hello@plex.example', 'See what’s trending this week'),
        row('LinkedIn', 'updates@linkedin.example', 'James just posted something'),
        row('Bank', 'noreply@bank.example', 'Declined transaction'),
        row('Recruiter', 'person@jobs.example', 'Interview confirmation'),
        row('Old alert', 'person@example.com', 'Security alert', 80)]}
    monkeypatch.setitem(sys.modules, 'aria.mailview', mail)
    result = b.email_section(NOW)
    assert '10 unread' in result['text']
    assert 'Declined transaction' in result['text'] and 'Interview confirmation' in result['text']
    assert 'Plex' not in result['text'] and 'LinkedIn' not in result['text'] and 'Old alert' not in result['text']


def test_news_rejects_old_undated_and_duplicate_headlines():
    def item(title, date):
        return f'<item><title>{title}</title><link>https://news.example/{title}</link><pubDate>{date}</pubDate></item>'
    xml = '<rss><channel>' + item('Today event', 'Tue, 27 Oct 2026 07:00:00 +0000') * 2 + item('Old event', 'Fri, 23 Oct 2026 07:00:00 +0000') + item('No date', '') + '</channel></rss>'
    rows = b.parse_headlines(xml, NOW)
    assert len(rows) == 1 and rows[0]['title'] == 'Today event'
    assert rows[0]['url'].startswith('https://')


def test_weather_uses_observed_values(monkeypatch):
    monkeypatch.setattr(b, '_get', lambda url: b'{"current_condition":[{"temp_F":"59","weatherDesc":[{"value":"Clear"}]}],"weather":[{"maxtempF":"66","mintempF":"54"}]}')
    result = b.weather_section('Dundalk, Maryland')
    assert 'Dundalk' in result['text'] and '59 degrees' in result['text'] and '66' in result['text']


def test_feed_urls_include_local_area_and_separate_world_source(monkeypatch):
    seen = []
    def fetch(url):
        seen.append(url); return b'<rss><channel/></rss>'
    monkeypatch.setattr(b, '_get', fetch)
    b.news_section(NOW, 'local_news', 'Dundalk, Maryland', [])
    b.news_section(NOW, 'world_news', 'Dundalk, Maryland', [])
    assert 'Dundalk' in seen[0] and 'Baltimore' in seen[0]
    assert 'feeds.bbci.co.uk/news/world' in seen[1]
