"""Pass 18: banner-resolved approvals must be VISIBLE to the
user without the model narrating them (ChatGPT pass-17 finding
#2 — with the model down, an executed approval was previously
indistinguishable from a denial or a crash).

Pinned here:
- aria.dashboard.activity_html renders the approval journal as
  a RECENT APPROVALS section above the log, escaped like
  everything else in the view.
- Dashboard.render_once actually shows it: the Activity change
  guard keys on the journal too, so a resolution re-renders the
  view even when no new log event landed with it.
- The phone bridge's GET /api/approvals carries the same
  journal as `recent` (covered in test_bridge_approvals.py).

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import time
import types

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
import numpy as np
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "aria")]
sys.modules["aria"] = pkg

from aria.dashboard import activity_html, create_dashboard

REC = {'ts': time.time(), 'tool': 'run_shell_command',
       'desc': "run shell command 'git pull origin main'",
       'verdict': 'executed',
       'detail': 'Exit code: 0 — Already up to date.'}


def test_activity_html_renders_resolutions_escaped():
    html = activity_html([], resolutions=[REC])
    assert 'RECENT APPROVALS' in html
    assert 'run_shell_command' in html and 'executed' in html
    assert 'Already up to date.' in html
    # A resolution is text, never markup.
    evil = dict(REC, desc='<script>alert(1)</script>')
    html = activity_html([], resolutions=[evil])
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_activity_html_without_resolutions_is_unchanged():
    events = [('12:00', 'INFO', 'hello', {})]
    assert 'RECENT APPROVALS' not in activity_html(events)
    assert 'hello' in activity_html(events)


def _build(monkeypatch, resolutions):
    app = QApplication.instance() or QApplication([])
    calls = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama',
                subsystems=[('Vision', 'ONLINE', True)],
                task_active=0, scheduled=1, voice_enabled=True,
                volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': 'Test track'}
    frame.media_data = lambda: {'title': 'Test track'}
    frame.chat_entries = lambda n: [('12:00', 'ALEK', 'hello')]
    frame.activity_events = lambda n: [('12:00', 'INFO', 'boot', {})]
    frame.toggle_voice = lambda: calls.append('voice')
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'
    hud.SUBTITLE_TEXT = 'Ready'
    hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud._dispatch_media_action = calls.append
    hud.set_hud_volume = calls.append
    config = types.ModuleType('aria.config')
    config.redact = str
    config.log_silent = lambda *args: None
    config.get_setting = lambda key, default=None: default
    approval = types.ModuleType('aria.approval')
    approval.pending_entries = lambda: []
    approval.recent_resolutions = \
        lambda limit=5, within_s=1800: list(resolutions)
    for name, mod in [('aria.frame', frame), ('aria.hud', hud),
                      ('aria.config', config),
                      ('aria.approval', approval)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(sys.modules['aria'],
                            name.rsplit('.', 1)[1], mod, raising=False)
    queue = types.ModuleType('aria.agent.taskqueue')
    queue.get_queue = lambda: types.SimpleNamespace(
        get_all_statuses=lambda: [])
    scheduler = types.ModuleType('aria.scheduler')
    scheduler.sched_list = lambda: []
    monkeypatch.setitem(sys.modules, 'aria.agent.taskqueue', queue)
    monkeypatch.setitem(sys.modules, 'aria.scheduler', scheduler)
    speech = types.ModuleType('aria.speech')
    speech.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, 'aria.speech', speech)
    monkeypatch.setattr(sys.modules['aria'], 'speech', speech,
                        raising=False)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: calls.append('press'),
        ptt_release=lambda: calls.append('release'),
        interrupt=lambda: calls.append('stop'),
        toggle_whisper=lambda: calls.append('whisper'),
        submit=calls.append,
        request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.resize(1100, 700)
    window.show()
    app.processEvents()
    return app, window


def test_render_once_shows_recent_approvals(monkeypatch):
    app, window = _build(monkeypatch, [REC])
    window._last_refresh = 0.0
    window.render_once()
    app.processEvents()
    html = window.views['Activity'].toHtml()
    assert 'RECENT APPROVALS' in html
    assert 'Already up to date.' in html
    window.close()
