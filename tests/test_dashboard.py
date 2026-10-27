"""Real offscreen Qt acceptance tests; service access is injected/stubbed."""
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
from aria.dashboard import create_dashboard, dashboard_enabled


def test_dashboard_routes_controls_and_refreshes(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    calls = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama', subsystems=[('Vision', 'ONLINE', True)],
                task_active=0, scheduled=1, voice_enabled=True, volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': 'Test track'}
    frame.chat_entries = lambda n: [('12:00', 'ALEK', '<b>literal text</b>')]
    frame.activity_events = lambda n: [('12:00', 'INFO', 'Ready', {})]
    frame.toggle_voice = lambda: calls.append('voice')
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = 'Ready'; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud.set_hud_volume = calls.append
    config = types.ModuleType('aria.config'); config.redact = str
    for name, mod in [('aria.frame', frame), ('aria.hud', hud), ('aria.config', config)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(sys.modules['aria'], name.rsplit('.', 1)[1], mod, raising=False)
    queue = types.ModuleType('aria.agent.taskqueue')
    queue.get_queue = lambda: types.SimpleNamespace(get_all_statuses=lambda: [])
    scheduler = types.ModuleType('aria.scheduler'); scheduler.sched_list = lambda: []
    monkeypatch.setitem(sys.modules, 'aria.agent.taskqueue', queue)
    monkeypatch.setitem(sys.modules, 'aria.scheduler', scheduler)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: calls.append('press'), ptt_release=lambda: calls.append('release'),
        interrupt=lambda: calls.append('stop'), toggle_whisper=lambda: calls.append('whisper'),
        submit=calls.append, request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    from aria.pixel_avatar import draw_pixel_aria
    def render():
        canvas = np.zeros((800, 1280, 3), dtype=np.uint8)
        canvas[:] = (20, 13, 8)
        draw_pixel_aria(canvas, "idle", 1.0, mood="calm")
        return canvas
    callbacks.render_frame = render
    window = create_dashboard(callbacks)
    window.show(); app.processEvents(); window.render_once()
    assert window.tabs.count() == 5
    assert window.cpu[1].value() == 25
    assert '<b>literal text</b>' in window.views['Conversation'].toPlainText()
    assert not window.face.pixmap().isNull()
    window.entry.setText('  hello ARIA  '); window.entry.returnPressed.emit()
    assert 'hello ARIA' in calls and window.entry.text() == ''
    window.mic.pressed.emit(); window.mic.released.emit(); window.voice.click()
    assert all(c in calls for c in ['press', 'release', 'voice'])
    window.panel_finished('Inbox', '<script>untrusted mail</script>')
    assert '<script>' in window.views['Inbox'].toPlainText()
    window.grab().save(str(tmp_path / 'dashboard.png'))
    window.close(); assert 'shutdown' in calls


def test_classic_layout_rollback(monkeypatch):
    monkeypatch.delenv('ARIA_UI_LAYOUT', raising=False)
    assert dashboard_enabled()
    monkeypatch.setenv('ARIA_UI_LAYOUT', 'classic')
    assert not dashboard_enabled()
