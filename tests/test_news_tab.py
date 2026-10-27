"""Port 7 News tab: RSS parsing honesty + lazy dashboard loading.

Pinned behaviours: the feed parser returns [] on any failure (never
invents headlines), titles are HTML-escaped before they reach the
tab, the tab loads lazily on first show, Refresh forces a refetch,
and a failed load does not count as fresh (the next visit retries).
"""
import io
import os
import sys
import time
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
import numpy as np
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Top stories</title>
<item><title>Fed holds rates &lt;steady&gt;</title>
<link>https://example.com/a</link><pubDate>Tue, 27 Oct 2026 08:00:00 GMT</pubDate>
<source url="https://example.com">Example Wire</source></item>
<item><title>Local team wins</title><link>https://example.com/b</link>
<pubDate>Tue, 27 Oct 2026 09:00:00 GMT</pubDate><source>City Paper</source></item>
</channel></rss>"""


def _app():
    return QApplication.instance() or QApplication([])


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def test_fetch_headlines_parses_rss(monkeypatch):
    import aria.news_feed as feed
    monkeypatch.setattr(feed.urllib.request, 'urlopen',
                        lambda req, timeout=0: _Resp(RSS))
    items = feed.fetch_headlines()
    assert len(items) == 2
    assert items[0]['title'] == 'Fed holds rates <steady>'
    assert items[0]['source'] == 'Example Wire'
    assert items[1]['link'] == 'https://example.com/b'


def test_fetch_headlines_failure_is_empty(monkeypatch):
    import aria.news_feed as feed

    def boom(req, timeout=0):
        raise OSError('no network')
    monkeypatch.setattr(feed.urllib.request, 'urlopen', boom)
    assert feed.fetch_headlines() == []
    monkeypatch.setattr(feed.urllib.request, 'urlopen',
                        lambda req, timeout=0: _Resp(b'not xml'))
    assert feed.fetch_headlines() == []


def test_format_escapes_and_handles_empty():
    import aria.news_feed as feed
    html = feed.format_headlines_html(
        [{'title': '<b>Hi</b> & bye', 'source': 'S', 'link': 'https://x',
          'published': 'now'}])
    assert '&lt;b&gt;Hi&lt;/b&gt;' in html and '<b>Hi</b>' not in html
    assert 'Could not load' in feed.format_headlines_html([])


def test_fetch_section_uses_the_topic_feed(monkeypatch):
    import aria.news_feed as feed
    urls = []

    def fake_urlopen(req, timeout=0):
        urls.append(req.full_url)
        return _Resp(RSS)
    monkeypatch.setattr(feed.urllib.request, 'urlopen', fake_urlopen)
    items = feed.fetch_section('TECHNOLOGY')
    assert len(items) == 2
    assert 'headlines/section/topic/TECHNOLOGY' in urls[0]


def test_grouped_html_headers_and_empty_states():
    import aria.news_feed as feed
    one = [{'title': 'Big story', 'source': 'Wire',
            'link': 'https://x', 'published': 'now'}]
    html = feed.format_grouped_html(
        [('Top stories', one), ('National', []), ('World', one)])
    assert 'TOP STORIES' in html and 'NATIONAL' in html
    assert 'No headlines right now.' in html      # one quiet group
    assert 'Could not load' not in html
    # Everything empty is the single failure state the tab shows.
    assert 'Could not load' in feed.format_grouped_html(
        [('Top stories', []), ('National', [])])


def _stubs(monkeypatch, fetch):
    calls = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama',
                subsystems=[('Vision', 'ONLINE', True)],
                task_active=0, scheduled=1, voice_enabled=True, volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': 'Test track'}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: None
    frame.receive_file = lambda p: None
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = 'Ready'
    hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud.set_hud_volume = calls.append
    config = types.ModuleType('aria.config')
    config.redact = str; config.log_silent = lambda *args: None
    config.add_log = lambda *args: None
    prefs = types.ModuleType('aria.ui_prefs')
    prefs.get_accent = lambda: None
    prefs.get_centerpiece = lambda: 'orb'
    prefs.set_centerpiece = lambda name: None
    prefs.set_accent = lambda a: None
    for name, mod in [('aria.frame', frame), ('aria.hud', hud),
                      ('aria.config', config), ('aria.ui_prefs', prefs)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(sys.modules['aria'], name.rsplit('.', 1)[1],
                            mod, raising=False)
    queue = types.ModuleType('aria.agent.taskqueue')
    queue.get_queue = lambda: types.SimpleNamespace(
        get_all_statuses=lambda: [])
    scheduler = types.ModuleType('aria.scheduler')
    scheduler.sched_list = lambda: []
    monkeypatch.setitem(sys.modules, 'aria.agent.taskqueue', queue)
    monkeypatch.setitem(sys.modules, 'aria.scheduler', scheduler)
    speech = types.ModuleType('aria.speech'); speech.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, 'aria.speech', speech)
    monkeypatch.setattr(sys.modules['aria'], 'speech', speech, raising=False)
    feed = types.ModuleType('aria.news_feed')
    import aria.news_feed as real_feed
    feed.fetch_headlines = fetch
    feed.format_headlines_html = real_feed.format_headlines_html
    feed.fetch_grouped = lambda: [('Top stories', fetch())]
    feed.format_grouped_html = real_feed.format_grouped_html
    monkeypatch.setitem(sys.modules, 'aria.news_feed', feed)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: None, ptt_release=lambda: None,
        interrupt=lambda: None, toggle_whisper=lambda: None,
        submit=calls.append, request_stop=lambda: None,
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    return callbacks


def _pump_until(app, predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_news_tab_lazy_load_refresh_and_failure_retry(monkeypatch):
    app = _app()
    state = {'calls': 0, 'fail': False}

    def fetch(limit=12):
        state['calls'] += 1
        if state['fail']:
            return []
        return [{'title': 'Big headline', 'source': 'Wire',
                 'link': 'https://example.com/x', 'published': 'now'}]
    callbacks = _stubs(monkeypatch, fetch)
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    names = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert 'News' in names
    assert state['calls'] == 0                      # nothing fetched yet
    window.tabs.setCurrentIndex(names.index('News'))
    assert _pump_until(
        app, lambda: 'Big headline' in window.views['News'].toHtml())
    assert state['calls'] == 1
    # Fresh cache: leaving and returning does not refetch.
    window.tabs.setCurrentIndex(0)
    window.tabs.setCurrentIndex(names.index('News'))
    app.processEvents()
    assert state['calls'] == 1
    # Failure renders the empty state and does NOT mark the tab fresh.
    state['fail'] = True
    window.load_news(force=True)
    assert _pump_until(
        app, lambda: 'Could not load' in window.views['News'].toPlainText())
    assert window._news_loaded_at == 0.0
    window.close(); app.processEvents()
