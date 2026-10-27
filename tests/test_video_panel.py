"""VideoPanel port: facade API, seek honesty, dashboard swap.

Adapted from Mark-LVI-ARIA's in-HUD player (see aria/video_panel.py).
Pinned behaviours: seek/seek_percent refuse (return 0) with no media —
never a guessed position; show_video rejects empty sources; the
dashboard swaps orb<->video and restores the orb on close.
"""
import os
import sys
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
pytest.importorskip('PyQt6.QtMultimedia')
import numpy as np
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg


def _app():
    return QApplication.instance() or QApplication([])


def test_fmt_ms():
    from aria.video_panel import fmt_ms
    assert fmt_ms(0) == '0:00'
    assert fmt_ms(65_000) == '1:05'
    assert fmt_ms(3_725_000) == '1:02:05'
    assert fmt_ms(-5) == '0:00'


def test_panel_api_and_seek_honesty(tmp_path):
    app = _app()
    from aria.video_panel import VideoPanel
    panel = VideoPanel(); panel.show()
    assert panel.show_video('') is False
    # No media loaded: seek refuses rather than guessing a position.
    assert panel.seek(5_000) == 0
    assert panel.seek_percent(50) == 0

    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'not a real video')
    assert panel.show_video(str(clip)) is True
    assert panel._source == str(clip)
    assert panel._title_label.text() == 'clip.mp4'
    panel.set_muted(True)
    assert panel._audio.isMuted()
    assert panel._mute_btn.text() == 'Unmute'
    panel.set_volume(150)
    assert panel._audio.volume() == 1.0
    panel.stop_video()
    assert panel._source == ''
    panel.close(); app.processEvents()


def test_panel_command_signal_and_snapshot(tmp_path):
    # Gap fill 6: the unified media_control tool reaches the panel
    # through the command signal (queued to the GUI thread) and
    # reads a plain-data snapshot built from cached values.
    app = _app()
    from aria.video_panel import VideoPanel
    panel = VideoPanel(); panel.show()
    snap = panel.snapshot()
    assert snap['loaded'] is False and snap['playing'] is False
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'not a real video')
    assert panel.show_video(str(clip), title='Movie Night') is True
    panel.command.emit('mute', None)
    app.processEvents()
    assert panel._audio.isMuted()
    snap = panel.snapshot()
    assert snap['loaded'] is True and snap['title'] == 'Movie Night'
    assert snap['muted'] is True
    # apply_command is the same code path, callable on-thread:
    out = panel.apply_command('seek_percent', 50)
    assert out['loaded'] is True  # fake media: duration 0, seek is a no-op
    panel.command.emit('stop', None)
    app.processEvents()
    assert panel.snapshot()['loaded'] is False
    panel.close(); app.processEvents()


def _dashboard_stubs(monkeypatch):
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
    frame.toggle_voice = lambda: calls.append('voice')
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = 'Ready'
    hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud.set_hud_volume = calls.append
    config = types.ModuleType('aria.config')
    config.redact = str; config.log_silent = lambda *args: None
    config.add_log = lambda *args: None
    for name, mod in [('aria.frame', frame), ('aria.hud', hud),
                      ('aria.config', config)]:
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
    return types.SimpleNamespace(
        ptt_press=lambda: None, ptt_release=lambda: None,
        interrupt=lambda: None, toggle_whisper=lambda: None,
        submit=calls.append, request_stop=lambda: None,
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))


def test_dashboard_swaps_orb_for_video(monkeypatch, tmp_path):
    app = _app()
    callbacks = _dashboard_stubs(monkeypatch)
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    assert window.video is not None
    assert window.face.isVisible() and not window.video.isVisible()

    clip = tmp_path / 'movie.mp4'
    clip.write_bytes(b'not a real video')
    assert window.show_video(str(clip), title='Movie Night') is True
    assert window.video.isVisible() and not window.face.isVisible()
    assert window.video._title_label.text() == 'Movie Night'

    assert window.show_video('') is False
    window.stop_video()
    assert window.face.isVisible() and not window.video.isVisible()
    window.close(); app.processEvents()
