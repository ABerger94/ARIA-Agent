"""Dashboard wiring for ARIA OS Shell Home (2026-10-30).

Offscreen Qt, services stubbed like test_room_dashboard.py: the
Shell Home button opens the shell panel over the centerpiece,
its command bar routes chat text through the dashboard's normal
submit callback, and the Dashboard button hides the shell, leaves
fullscreen, and restores the persisted centerpiece.
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
    return window, calls


def test_shell_home_opens_and_exit_restores_centerpiece(monkeypatch):
    window, _calls = _window(monkeypatch)
    assert window.shell is not None
    window.set_centerpiece('orb')
    assert window.face.isVisible()
    assert not window.shell.isVisible()

    window.shell_btn.click()
    assert window.shell.isVisible()
    assert not window.face.isVisible()
    assert window.shell.exit_btn.text() == 'Dashboard'

    window.shell.exit_btn.click()
    assert not window.shell.isVisible()
    assert window.face.isVisible()
    assert not window.isFullScreen()
    window.close()


def test_shell_command_bar_routes_chat_to_submit(monkeypatch):
    window, calls = _window(monkeypatch)
    window.open_shell_home()
    window.shell.command.setText('what time is it in Berlin')
    window.shell.command.returnPressed.emit()
    assert calls == ['what time is it in Berlin']
    window.close()


def test_centerpiece_swap_hides_shell(monkeypatch):
    window, _calls = _window(monkeypatch)
    window.open_shell_home()
    assert window.shell.isVisible()
    window.set_centerpiece('room')
    assert not window.shell.isVisible()
    assert window.room.isVisible()
    window.close()


# -- V3: Shell Home takes the full window -------------------------

def test_shell_home_takes_full_window_and_exit_restores(monkeypatch):
    window, _calls = _window(monkeypatch)
    assert window.left_card.isVisible()
    assert window.right_card.isVisible()
    before = window._body_splitter.sizes()
    window.open_shell_home()
    assert window.shell.isVisible()
    assert not window.left_card.isVisible()
    assert not window.right_card.isVisible()
    window.shell.exit_btn.click()
    assert window.left_card.isVisible()
    assert window.right_card.isVisible()
    assert window._body_splitter.sizes() == before
    window.close()


def test_centerpiece_swap_restores_side_columns(monkeypatch):
    window, _calls = _window(monkeypatch)
    window.open_shell_home()
    assert not window.left_card.isVisible()
    assert not window.right_card.isVisible()
    window.set_centerpiece('room')
    assert not window.shell.isVisible()
    assert window.left_card.isVisible()
    assert window.right_card.isVisible()
    window.close()


def test_window_apply_accent_rethemes_shell(monkeypatch):
    # V4 addendum: the dashboard's one accent path (Customize /
    # Settings) reaches Shell Home like it reaches viz and room.
    window, _calls = _window(monkeypatch)
    window.apply_accent('#e83a9a')
    assert window.shell._accent == '#e83a9a'
    assert '#57c7d4' not in window.shell.styleSheet().lower()
    window.close()


def test_open_shell_home_reresolves_accent(monkeypatch):
    # A colour set while the shell was closed is already applied
    # when Shell Home opens — no restart.
    window, _calls = _window(monkeypatch)
    window.shell.set_accent('#57c7d4')
    import aria.dashboard as dash
    monkeypatch.setattr(dash, '_current_accent',
                        lambda: '#e83a9a')
    window.open_shell_home()
    assert window.shell._accent == '#e83a9a'
    window.close()
