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
    assert 'On email: 10 unread — notable:' in result['text']
    assert "Bank regarding 'Declined transaction'" in result['text']
    assert "Recruiter regarding 'Interview confirmation'" in result['text']
    assert 'Plex' not in result['text'] and 'LinkedIn' not in result['text'] and 'Old alert' not in result['text']


def _mail_stub(monkeypatch, rows, unread_total):
    pkg = types.ModuleType('aria'); pkg.__path__ = [str(ROOT / 'aria')]
    monkeypatch.setitem(sys.modules, 'aria', pkg)
    mail = types.ModuleType('aria.mailview')
    mail.fetch_messages = lambda **kw: {'ok': True, 'unread_total': unread_total, 'messages': rows}
    monkeypatch.setitem(sys.modules, 'aria.mailview', mail)


def _row(sender, address, subject, age=1):
    return dict(sender=sender, address=address, subject=subject, unread=True,
                date=(NOW - timedelta(hours=age)).isoformat())


def test_email_lvi_rules(monkeypatch):
    # Google's own security alerts are skipped; a personal sender is
    # named in LVI form.
    _mail_stub(monkeypatch, [
        _row('Google', 'no-reply@accounts.google.com', 'Security alert'),
        _row('Sam', 'sam@example.com', 'Dinner Thursday?')], 2)
    text = b.email_section(NOW)['text']
    assert "Sam regarding 'Dinner Thursday?'" in text
    assert 'Security alert' not in text
    # An all-automated sample is said to be one.
    _mail_stub(monkeypatch, [
        _row('Plex', 'hello@plex.example', 'See what’s trending this week')], 1)
    assert 'mostly automated alerts' in b.email_section(NOW)['text']
    # Zero unread is the only 'Inbox is clear.'
    _mail_stub(monkeypatch, [], 0)
    assert b.email_section(NOW)['text'] == 'Inbox is clear.'


def test_report_text_lists_headlines_not_news_sources():
    funcs = sources()
    funcs['local_news'] = lambda: b.section(
        'Local headlines: Local one; Local two.',
        sources=[{'title': 'Local one', 'url': 'https://news.example/1'},
                 {'title': 'Local two', 'url': 'https://news.example/2'}])
    report = b.compose(NOW, funcs)
    text = b.report_text(report)
    assert 'Local one' in text and 'Local two' in text
    assert 'news.example' not in text          # no source URL blocks


def test_spoken_news_speaks_categorized_beats():
    # Alek, 2026-10-27: full sections in the tab, categorized
    # beats in the greeting — never the old single merged line.
    funcs = sources()
    funcs['local_news'] = lambda: b.section(
        'Local headlines: A; B.',
        sources=[{'title': 'Local one', 'url': 'https://x/1'},
                 {'title': 'Local two', 'url': 'https://x/2'}])
    funcs['world_news'] = lambda: b.section(
        'World headlines: C; D.',
        sources=[{'title': 'World one', 'url': 'https://x/3'},
                 {'title': 'World two', 'url': 'https://x/4'}])
    report = b.compose(NOW, funcs)
    assert 'Locally: Local one. Local two.' in report['text']
    assert 'Around the world: World one. World two.' in report['text']
    assert 'In the news:' not in report['text']
    assert 'Local headlines:' not in report['text']
    by_key = {s['key']: s for s in report['sections']}
    assert by_key['local_news']['text'] == 'Local headlines: A; B.'


def test_beats_condense_but_sections_keep_three():
    funcs = sources()
    stories = {
        'national_news': ('National', ['Senate passes budget bill',
                                       'Storm lashes the Gulf Coast',
                                       'Markets rally on jobs report']),
        'tech_news': ('Tech', ['New chip doubles battery life',
                               'AI lab open-sources its model',
                               'Rocket lands itself at sea'])}
    for key, (label, titles) in stories.items():
        funcs[key] = lambda lb=label, ts=titles: b.section(
            f'{lb} headlines: x.',
            sources=[{'title': t, 'url': 'https://x/' + t[:8].replace(' ', '-')}
                     for t in ts])
    report = b.compose(NOW, funcs)
    # Spoken: two per beat. Tab section keeps all three sources.
    assert ('Nationally: Senate passes budget bill. '
            'Storm lashes the Gulf Coast.') in report['text']
    assert ('In tech: New chip doubles battery life. '
            'AI lab open-sources its model.') in report['text']
    assert 'Markets rally on jobs report' not in report['text']
    by_key = {s['key']: s for s in report['sections']}
    assert len(by_key['national_news']['sources']) == 3
    assert len(by_key['tech_news']['sources']) == 3


def test_news_rejects_old_undated_and_duplicate_headlines():
    def item(title, date):
        return f'<item><title>{title}</title><link>https://news.example/{title}</link><pubDate>{date}</pubDate></item>'
    xml = '<rss><channel>' + item('Today event', 'Tue, 27 Oct 2026 07:00:00 +0000') * 2 + item('Old event', 'Fri, 23 Oct 2026 07:00:00 +0000') + item('No date', '') + '</channel></rss>'
    rows = b.parse_headlines(xml, NOW)
    assert len(rows) == 1 and rows[0]['title'] == 'Today event'
    assert rows[0]['url'].startswith('https://')


def test_news_titles_drop_the_publisher_suffix():
    def item(title):
        return (f'<item><title>{title}</title><link>https://news.example/x</link>'
                '<pubDate>Tue, 27 Oct 2026 07:00:00 +0000</pubDate></item>')
    xml = ('<rss><channel>'
           + item('Florist to close after 115 years - WBAL-TV')
           + item('Fashion AI era arrives - Reuters')
           + item('A well-known shop - a history in flowers')
           + '</channel></rss>')
    titles = [r['title'] for r in b.parse_headlines(xml, NOW)]
    assert 'Florist to close after 115 years' in titles
    assert 'Fashion AI era arrives' in titles
    # A lowercase dash tail is part of the headline, not a publisher.
    assert 'A well-known shop - a history in flowers' in titles


def test_news_dedupes_the_same_story_across_outlets():
    # Alek's 2026-10-27 greeting carried this story twice, from
    # two outlets, in two phrasings. One story = one headline.
    def item(title):
        return (f'<item><title>{title}</title><link>https://news.example/x</link>'
                '<pubDate>Tue, 27 Oct 2026 07:00:00 +0000</pubDate></item>')
    xml = ('<rss><channel>'
           + item('Baltimore County florist to close after 115 years in business - WBAL-TV')
           + item('Longtime Floral Shop To Close In Baltimore County After 115 Years - Patch')
           + '</channel></rss>')
    rows = b.parse_headlines(xml, NOW)
    assert len(rows) == 1


def test_news_strips_wire_section_prefixes():
    def item(title):
        return (f'<item><title>{title}</title><link>https://news.example/x</link>'
                '<pubDate>Tue, 27 Oct 2026 07:00:00 +0000</pubDate></item>')
    xml = '<rss><channel>' + item("Insight: Fashion's AI era arrives - Reuters") + '</channel></rss>'
    rows = b.parse_headlines(xml, NOW)
    assert rows[0]['title'] == "Fashion's AI era arrives"


def test_spoken_news_never_repeats_a_story_across_sections():
    funcs = sources()
    funcs['local_news'] = lambda: b.section(
        'Local headlines: F.',
        sources=[{'title': 'Baltimore County florist to close after 115 years', 'url': 'https://x/1'}])
    funcs['world_news'] = lambda: b.section(
        'World headlines: F2; S.',
        sources=[{'title': 'Longtime floral shop to close in Baltimore County after 115 years', 'url': 'https://x/2'},
                 {'title': 'World leaders reach climate deal at summit', 'url': 'https://x/3'}])
    report = b.compose(NOW, funcs)
    assert report['text'].count('115 years') == 1
    assert 'climate deal' in report['text']
    by_key = {s['key']: s for s in report['sections']}
    assert 'floral shop' not in by_key['world_news']['text']


def test_weather_uses_observed_values(monkeypatch):
    monkeypatch.setattr(b, '_get', lambda url: b'{"current_condition":[{"temp_F":"59","weatherDesc":[{"value":"Clear"}]}],"weather":[{"maxtempF":"66","mintempF":"54"}]}')
    result = b.weather_section('Dundalk, Maryland')
    assert 'Dundalk' in result['text'] and '59 degrees' in result['text'] and '66' in result['text']


def _fake_feed(monkeypatch, items_by_topic):
    asked = []
    fake = types.SimpleNamespace(
        fetch_section=lambda topic, limit=10: asked.append(topic)
        or list(items_by_topic.get(topic, [])))
    monkeypatch.setattr(b, '_NEWS_FEED_MOD', fake)
    return asked


def _feed_item(title, pub='Tue, 27 Oct 2026 07:00:00 +0000'):
    return {'title': title, 'source': 'Wire',
            'link': 'https://news.example/' + title[:12].replace(' ', '-'),
            'published': pub}


def test_news_sources_local_search_and_tab_section_feeds(monkeypatch):
    # Local still searches the city feed; National/World/Tech
    # come from the News tab's section feeds (Alek, 2026-10-27).
    seen = []
    def fetch(url):
        seen.append(url); return b'<rss><channel/></rss>'
    monkeypatch.setattr(b, '_get', fetch)
    asked = _fake_feed(monkeypatch, {
        'WORLD': [_feed_item('Summit reaches deal - Reuters')],
        'NATION': [_feed_item('Senate passes bill - AP')],
        'TECHNOLOGY': [_feed_item('New chip unveiled - The Verge')]})
    b.news_section(NOW, 'local_news', 'Dundalk, Maryland', [])
    assert 'Dundalk' in seen[0] and 'Baltimore' in seen[0]
    world = b.news_section(NOW, 'world_news', 'Dundalk, Maryland', [])
    nation = b.news_section(NOW, 'national_news', 'Dundalk, Maryland', [])
    tech = b.news_section(NOW, 'tech_news', 'Dundalk, Maryland', [])
    assert asked == ['WORLD', 'NATION', 'TECHNOLOGY']
    # Titles arrive cleaned: the publisher tail never reaches
    # the briefing.
    assert world['text'] == 'World headlines: Summit reaches deal.'
    assert nation['text'] == 'National headlines: Senate passes bill.'
    assert tech['text'] == 'Tech headlines: New chip unveiled.'


def test_section_items_drop_stale_dupes_and_bad_links(monkeypatch):
    _fake_feed(monkeypatch, {'NATION': [
        _feed_item('Bridge reopens downtown - AP'),
        _feed_item('Bridge reopens downtown - AP'),
        _feed_item('Old bridge news - AP', 'Fri, 23 Oct 2026 07:00:00 +0000'),
        {'title': 'No scheme - AP', 'source': 'AP', 'link': 'ftp://x',
         'published': 'Tue, 27 Oct 2026 07:00:00 +0000'}]})
    items = b._section_items('NATION', NOW, limit=3)
    assert [i['title'] for i in items] == ['Bridge reopens downtown']
