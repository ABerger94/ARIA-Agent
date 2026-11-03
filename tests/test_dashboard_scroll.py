"""Tasks/Activity tabs must keep the user's scroll position.

Regression test for Alek's 2026-11-03 report: "When I try to look
at tasks tab, and scroll to the bottom it doesn't let me, it jumps
back up to the top of tasks tab."

Root cause: Dashboard.render_once() rebuilt the Tasks view with
setPlainText on EVERY render tick (~0.8s) with no change guard, and
setPlainText resets the scrollbar to 0. Activity had the same yank
whenever a new event landed mid-read. The fix: only swap a view's
content when its text actually changed, and preserve the scrollbar
value across the swap (_set_view_content).
"""
import os
import sys
import types

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
import numpy as np
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "aria")]
sys.modules["aria"] = pkg
from aria.dashboard import create_dashboard


def _build(monkeypatch, sched_rows, activity):
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
    frame.activity_events = lambda n: list(activity)
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
    for name, mod in [('aria.frame', frame), ('aria.hud', hud),
                      ('aria.config', config)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(sys.modules['aria'],
                            name.rsplit('.', 1)[1], mod, raising=False)
    queue = types.ModuleType('aria.agent.taskqueue')
    queue.get_queue = lambda: types.SimpleNamespace(
        get_all_statuses=lambda: [])
    scheduler = types.ModuleType('aria.scheduler')
    scheduler.sched_list = lambda: list(sched_rows)
    monkeypatch.setitem(sys.modules, 'aria.agent.taskqueue', queue)
    monkeypatch.setitem(sys.modules, 'aria.scheduler', scheduler)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: calls.append('press'),
        ptt_release=lambda: calls.append('release'),
        interrupt=lambda: calls.append('stop'),
        toggle_whisper=lambda: calls.append('whisper'),
        submit=calls.append,
        request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    speech = types.ModuleType('aria.speech')
    speech.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, 'aria.speech', speech)
    monkeypatch.setattr(sys.modules['aria'], 'speech', speech,
                        raising=False)
    window = create_dashboard(callbacks)
    window.resize(1100, 700)
    window.show()
    app.processEvents()
    return app, window


def _render(app, window):
    # render_once throttles its body to one pass per 0.8s; reset the
    # stamp so each test call is a real pass.
    window._last_refresh = 0.0
    window.render_once()
    app.processEvents()


def test_tasks_tab_scroll_survives_render_ticks(monkeypatch):
    rows = [(i, 'daily', f'check item number {i}', 86400,
             '2026-11-03 06:04:00') for i in range(80)]
    activity = [('12:00', 'INFO', f'event {i}', {}) for i in range(40)]
    app, window = _build(monkeypatch, rows, activity)
    window.tabs.setCurrentIndex(3)  # Tasks
    app.processEvents()
    _render(app, window)
    view = window.views['Tasks']
    bar = view.verticalScrollBar()
    assert bar.maximum() > 0, 'test setup: Tasks content must overflow'
    bar.setValue(bar.maximum())
    app.processEvents()
    assert bar.value() == bar.maximum()
    # Several render ticks with UNCHANGED content must not move it.
    for _ in range(3):
        _render(app, window)
        assert bar.value() == bar.maximum(), \
            'render tick reset the Tasks scrollbar to the top'
    # A content change mid-read updates the text but keeps position.
    saved = bar.value()
    rows.append((999, 'daily', 'brand new scheduled row', 86400,
                 '2026-11-03 07:00:00'))
    _render(app, window)
    assert 'brand new scheduled row' in view.toPlainText()
    assert bar.value() == min(saved, bar.maximum())
    assert bar.value() > 0
    window.close()


def test_activity_tab_scroll_survives_new_events(monkeypatch):
    rows = []
    activity = [('12:00', 'INFO', f'event {i}', {}) for i in range(60)]
    app, window = _build(monkeypatch, rows, activity)
    window.tabs.setCurrentIndex(4)  # Activity
    app.processEvents()
    _render(app, window)
    view = window.views['Activity']
    bar = view.verticalScrollBar()
    assert bar.maximum() > 0, 'test setup: Activity content must overflow'
    bar.setValue(bar.maximum() // 2)
    app.processEvents()
    saved = bar.value()
    assert saved > 0
    activity.append(('12:01', 'INFO', 'a fresh event lands', {}))
    _render(app, window)
    assert 'a fresh event lands' in view.toPlainText()
    assert bar.value() == min(saved, bar.maximum()), \
        'new activity content yanked the scrollbar'
    window.close()
