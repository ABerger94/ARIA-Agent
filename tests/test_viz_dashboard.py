"""Dashboard wiring for the media visualizer (Alek, 2026-10-29).

Offscreen Qt, services stubbed like test_dashboard.py. Drives
render_once with a controllable media_data: the visualizer must
pop up when playback starts, step aside while ARIA is being
talked to, come back afterwards, stay away after its ✕ is
clicked, and re-arm on the next playback session.
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


_APP = None


def _window(monkeypatch):
    global _APP
    _APP = QApplication.instance() or QApplication([])
    app = _APP
    calls = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama',
                subsystems=[('Vision', 'ONLINE', True)],
                task_active=0, scheduled=1, voice_enabled=True,
                volume=.5)
    music = {'state': {}}
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': 'Test track'}
    frame.media_data = lambda: dict(music['state'])
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
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
        submit=calls.append, request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.show()
    app.processEvents()
    return window, music, hud


def _render(window):
    window._last_refresh = 0   # age past render_once's 0.8s throttle
    window.render_once()
    QApplication.instance().processEvents()


PLAYING = {'track': 'Song', 'artist': 'Artist', 'title': 'Song',
           'playing': True, 'running': True, 'status': 'playing'}
PAUSED = {'track': 'Song', 'artist': 'Artist', 'title': 'Song',
          'playing': False, 'running': True, 'status': 'paused'}


def test_visualizer_follows_playback_and_interaction(monkeypatch):
    window, music, hud = _window(monkeypatch)
    assert window.viz is not None
    assert not window.viz.isVisible()

    # Music starts: the visualizer takes the centerpiece.
    music['state'] = dict(PLAYING)
    _render(window)
    assert window.viz.isVisible()
    assert window._viz_on
    assert not window.face.isVisible()

    # Alek talks to ARIA: it steps aside, the orb returns.
    hud.CURRENT_STATE = 'listening'
    _render(window)
    assert not window.viz.isVisible()
    assert window.face.isVisible()

    # Interaction ends, music still playing: it comes back.
    hud.CURRENT_STATE = 'idle'
    _render(window)
    assert window.viz.isVisible()

    # The ✕ dismisses it for the rest of the session.
    window.viz_close.click()
    assert not window.viz.isVisible()
    _render(window)
    assert not window.viz.isVisible()

    # Pause + resume is the same session: still dismissed.
    music['state'] = dict(PAUSED)
    _render(window)
    music['state'] = dict(PLAYING)
    _render(window)
    assert not window.viz.isVisible()

    # The session ends, a new one starts: it pops up again.
    music['state'] = {}
    _render(window)
    music['state'] = dict(PLAYING)
    _render(window)
    assert window.viz.isVisible()
    window.close()
