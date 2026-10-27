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
    config = types.ModuleType('aria.config'); config.redact = str; config.log_silent = lambda *args: None; config.get_setting = lambda key, default=None: default
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
    assert window.tabs.count() == 7
    assert window.cpu._value == 25
    assert window.plaque_engine._value == 'Ollama'
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


def test_subsystems_all_rows_visible_in_scroll_area(monkeypatch, tmp_path):
    # Regression (Alek, 2026-10-27): the left column's subsystems
    # were one wrapped QLabel — crushed by the column's fixed
    # contents, the bottom entries were clipped and never showed.
    # Pins: every subsystem gets its own row inside a scroll area,
    # statuses render verbatim, and shrinking the window to its
    # minimum keeps every row (the area scrolls; nothing is cut).
    app = QApplication.instance() or QApplication([])
    calls = []
    subs = [('Voice Pathway', 'ARMED', True),
            ('Vision Optics', 'READY [Body Camera]', True),
            ('Screen Perception', 'IDLE', False),
            ('Semantic Memory', 'READY', True),
            ('Head Servos', 'VIRTUAL', False),
            ('Scheduler', 'ARMED (3)', True),
            ('GitHub Tools', 'OFFLINE', False),
            ('Autonomy & Workers', 'ACTIVE', True)]
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama', subsystems=subs,
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
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = 'Ready'; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud.set_hud_volume = calls.append
    config = types.ModuleType('aria.config'); config.redact = str; config.log_silent = lambda *args: None; config.get_setting = lambda key, default=None: default
    for name, mod in [('aria.frame', frame), ('aria.hud', hud), ('aria.config', config)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(sys.modules['aria'], name.rsplit('.', 1)[1], mod, raising=False)
    queue = types.ModuleType('aria.agent.taskqueue')
    queue.get_queue = lambda: types.SimpleNamespace(get_all_statuses=lambda: [])
    scheduler = types.ModuleType('aria.scheduler'); scheduler.sched_list = lambda: []
    monkeypatch.setitem(sys.modules, 'aria.agent.taskqueue', queue)
    monkeypatch.setitem(sys.modules, 'aria.scheduler', scheduler)
    speech = types.ModuleType('aria.speech'); speech.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, 'aria.speech', speech)
    monkeypatch.setattr(sys.modules['aria'], 'speech', speech, raising=False)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: None, ptt_release=lambda: None,
        interrupt=lambda: None, toggle_whisper=lambda: None,
        submit=calls.append, request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.resize(1080, 720)               # the window's minimum size
    window.show(); app.processEvents(); window.render_once()
    assert window.left_scroll.widget() is window.left_content
    assert len(window._subsystem_rows) == 8
    rendered = [(name_lbl.text(), status_lbl.text())
                for _dot, name_lbl, status_lbl, _w in window._subsystem_rows]
    assert rendered == [(n, s) for n, s, _ok in subs]
    # Colour coding: ok rows green, non-ok rows amber/red.
    assert '#2ee6a8' in window._subsystem_rows[0][0].styleSheet()
    assert '#ffb84d' in window._subsystem_rows[2][0].styleSheet()
    # Error status renders red; refresh with a changed count rebuilds.
    window._set_subsystems([('Semantic Memory', 'ERROR', False)])
    assert len(window._subsystem_rows) == 1
    assert '#ff6b6b' in window._subsystem_rows[0][0].styleSheet()
    window.close()


def test_now_playing_capped_and_panel_buttons_moved_to_settings(monkeypatch):
    # Alek, 2026-10-27: (1) a long Spotify title wrapped unbounded
    # and the NOW PLAYING text overran the subsystems — hard to
    # read; (2) the Providers / Audio / Customize / Pet buttons
    # leave the dashboard now that they live in Settings.
    # Pins: no overlap among left-column widgets at minimum window
    # size, the track label never exceeds two lines (full title on
    # the tooltip), and the four buttons are gone from the window.
    app = QApplication.instance() or QApplication([])
    calls = []
    long_title = ('An Extremely Long Spotify Track Title — '
                  'Featuring Several Guest Artists (Live at Some '
                  'Very Long Venue Name) [Remastered Edition] ') * 3
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama',
                subsystems=[('Voice Pathway', 'ARMED', True),
                            ('Vision Optics', 'READY', True),
                            ('Screen Perception', 'IDLE', False),
                            ('Semantic Memory', 'READY', True),
                            ('Head Servos', 'VIRTUAL', False),
                            ('Scheduler', 'ARMED (3)', True),
                            ('GitHub Tools', 'OFFLINE', False),
                            ('Autonomy & Workers', 'ACTIVE', True)],
                task_active=0, scheduled=1, voice_enabled=True, volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': long_title}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: calls.append('voice')
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = 'Ready'; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud.set_hud_volume = calls.append
    config = types.ModuleType('aria.config'); config.redact = str; config.log_silent = lambda *args: None; config.get_setting = lambda key, default=None: default
    for name, mod in [('aria.frame', frame), ('aria.hud', hud), ('aria.config', config)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(sys.modules['aria'], name.rsplit('.', 1)[1], mod, raising=False)
    queue = types.ModuleType('aria.agent.taskqueue')
    queue.get_queue = lambda: types.SimpleNamespace(get_all_statuses=lambda: [])
    scheduler = types.ModuleType('aria.scheduler'); scheduler.sched_list = lambda: []
    monkeypatch.setitem(sys.modules, 'aria.agent.taskqueue', queue)
    monkeypatch.setitem(sys.modules, 'aria.scheduler', scheduler)
    speech = types.ModuleType('aria.speech'); speech.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, 'aria.speech', speech)
    monkeypatch.setattr(sys.modules['aria'], 'speech', speech, raising=False)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: None, ptt_release=lambda: None,
        interrupt=lambda: None, toggle_whisper=lambda: None,
        submit=calls.append, request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.resize(1080, 720)
    window.show(); app.processEvents(); window.render_once(); app.processEvents()
    # (2) buttons: the four are gone; Commands / Add file remain.
    from PyQt6.QtWidgets import QPushButton
    texts = {b.text() for b in window.findChildren(QPushButton)}
    for gone in ('Providers', 'Audio', 'Customize', 'Pet'):
        assert gone not in texts, gone
    assert 'Commands' in texts and 'Add file' in texts
    # (1) the track label is capped at two lines, full title kept.
    assert window.track.toolTip() == long_title
    assert window.track.height() <= \
        window.track.fontMetrics().lineSpacing() * 2 + 8
    assert len(window.track.text()) < len(long_title)
    # (1) no two left-column widgets overlap (mapped into the
    # card's coordinates; the column may extend past the visible
    # card — that is the scroll area doing its job, not overlap).
    from PyQt6.QtCore import QPoint, QRect
    widgets = [window.plaque_engine, window.plaque_state,
               window.plaque_tasks, window.cpu, window.mem, window.net,
               window.power, window.subsystems,
               window.now_playing_title, window.track,
               window.remote_button]
    card = window.left_card
    rects = [QRect(w.mapTo(card, QPoint(0, 0)), w.size())
             for w in widgets]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not a.intersects(b), (a, b)
    window.close()


def test_classic_layout_rollback(monkeypatch):
    monkeypatch.delenv('ARIA_UI_LAYOUT', raising=False)
    assert dashboard_enabled()
    monkeypatch.setenv('ARIA_UI_LAYOUT', 'classic')
    assert not dashboard_enabled()
