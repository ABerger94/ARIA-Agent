"""Dashboard wiring for ARIA's Room centerpiece (Alek, 2026-10-29).

Offscreen Qt, services stubbed like test_viz_dashboard.py: the
room is a persisted centerpiece mode — set_centerpiece('room')
shows it and hides the orb, its ✕ returns to the orb, other
centerpieces still swap, and an unknown name collapses to orb.
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
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': 'Test track'}
    frame.media_data = lambda: {}
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
    return window


def test_room_centerpiece_swaps_and_close_returns_to_orb(monkeypatch):
    window = _window(monkeypatch)
    assert window.room is not None
    window.set_centerpiece('orb')
    assert window.face.isVisible()
    assert not window.room.isVisible()

    window.set_centerpiece('room')
    assert window._centerpiece == 'room'
    assert window.room.isVisible()
    assert not window.face.isVisible()

    # the room's ✕ hands the center back to the orb
    window.room_close.click()
    assert window._centerpiece == 'orb'
    assert window.face.isVisible()
    assert not window.room.isVisible()

    # other centerpieces still swap cleanly past the room
    window.set_centerpiece('room')
    window.set_centerpiece('map')
    assert window.mem_map_center.isVisible()
    assert not window.room.isVisible()

    # unknown names still collapse to the orb
    window.set_centerpiece('bogus')
    assert window._centerpiece == 'orb'
    assert window.face.isVisible()
    window.close()
