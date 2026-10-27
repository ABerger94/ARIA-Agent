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
    config = types.ModuleType('aria.config'); config.redact = str; config.log_silent = lambda *args: None
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
    speech = types.ModuleType('aria.speech'); speech.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, 'aria.speech', speech)
    monkeypatch.setattr(sys.modules['aria'], 'speech', speech, raising=False)
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
    assert not window.face.grab().isNull()
    window.entry.setText('  hello ARIA  '); window.entry.returnPressed.emit()
    assert 'hello ARIA' in calls and window.entry.text() == ''
    window.mic.pressed.emit(); window.mic.released.emit(); window.voice.click()
    assert all(c in calls for c in ['press', 'release', 'voice'])
    window.panel_finished('Inbox', '<script>untrusted mail</script>')
    assert '<script>' in window.views['Inbox'].toPlainText()
    mail = types.ModuleType('aria.mailview')
    mail.fetch_message = lambda uid: {'ok': True, 'message': {'subject': 'Message body', 'sender': 'Example sender', 'address': 'hello@example.com', 'date': '2026-10-27T07:26:04+00:00', 'body': '<img src=\"https://tracker.invalid\"> stays plain text'}}
    monkeypatch.setitem(sys.modules, 'aria.mailview', mail)
    window.panel_finished('Inbox', {'ok': True, 'messages': [
        {'uid': '10', 'sender': 'Plex Team', 'address': 'hello@example.com', 'subject': 'See what’s trending this week', 'date': '2026-10-27T07:26:04+00:00', 'unread': True},
        {'uid': '11', 'sender': 'Example Calendar', 'address': 'calendar@example.com', 'subject': 'Your event is tomorrow', 'date': '2026-10-26T07:26:04+00:00', 'unread': False}]})
    assert window.mail_list.count() == 2
    assert 'uid=' not in window.mail_list.item(0).text()
    window.mail_search.setText('calendar')
    assert window.mail_list.item(0).isHidden() and not window.mail_list.item(1).isHidden()
    window.mail_search.clear()
    window.mail_list.setCurrentRow(0)
    for _ in range(20): app.processEvents()
    window.mail_finished(window._email_request, mail.fetch_message('10'))
    assert '<img src=' in window.views['Inbox'].toPlainText()
    before = window.views['Inbox'].toPlainText()
    window.mail_finished(window._email_request - 1, {'ok': False, 'error': 'stale'})
    assert window.views['Inbox'].toPlainText() == before
    window.tabs.setCurrentIndex(1)
    window.grab().save(str(tmp_path / 'dashboard-inbox.png'))
    window.tabs.setCurrentIndex(0)
    window.grab().save(str(tmp_path / 'dashboard.png'))
    window.close(); assert 'shutdown' in calls


def test_classic_layout_rollback(monkeypatch):
    monkeypatch.delenv('ARIA_UI_LAYOUT', raising=False)
    assert dashboard_enabled()
    monkeypatch.setenv('ARIA_UI_LAYOUT', 'classic')
    assert not dashboard_enabled()
