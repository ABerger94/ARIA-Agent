"""Real offscreen Qt acceptance tests; service access is injected/stubbed."""
import os
import sys
import types

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
import numpy as np
from PyQt6.QtWidgets import QApplication, QLabel
pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "aria")]
sys.modules["aria"] = pkg
from aria.dashboard import create_dashboard, dashboard_enabled, _services_status_text


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
    frame.media_data = lambda: {'title': 'Test track'}
    frame.chat_entries = lambda n: [('12:00', 'ALEK', '<b>literal text</b>')]
    frame.activity_events = lambda n: [('12:00', 'INFO', 'Ready', {})]
    frame.toggle_voice = lambda: calls.append('voice')
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = 'Ready'; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud._dispatch_media_action = calls.append
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
    assert window.status.text() == 'All services connected'
    dlg = window.show_services_dialog()
    assert dlg.isVisible()
    assert 'Vision' in '\n'.join(w.text() for w in dlg.findChildren(QLabel))
    dlg.close()
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


def test_services_status_text_counts_armed():
    # The header label used to be born 'Connecting to services'
    # and never updated again.
    assert _services_status_text([]) == 'Connecting to services'
    assert _services_status_text(None) == 'Connecting to services'
    assert _services_status_text([('Vision', 'ONLINE', True)]) == 'All services connected'
    assert _services_status_text([('A', 'OK', True), ('B', 'ERROR', False),
                                  ('C', 'OK', True)]) == '2 of 3 services connected'


def test_subsystems_all_rows_visible_in_scroll_area(monkeypatch, tmp_path):
    # Regression (Alek, 2026-10-27): the left column's subsystems
    # were one wrapped QLabel — crushed by the column's fixed
    # contents, the bottom entries were clipped and never showed.
    # Pins: every subsystem gets its own row, statuses render
    # verbatim, and at the minimum window size every row FITS
    # inside the card — no scroll area anywhere (his later call
    # the same day superseded the scroll design).
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
    frame.media_data = lambda: {'title': 'Test track'}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: calls.append('voice')
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = 'Ready'; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud._dispatch_media_action = calls.append
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
    window.show(); app.processEvents(); window.render_once(); app.processEvents()
    assert not hasattr(window, 'left_scroll')
    assert len(window._subsystem_rows) == 8
    from PyQt6.QtCore import QPoint, QRect
    card = window.left_card
    for _dot, _n, _s, row_w in window._subsystem_rows:
        r = QRect(row_w.mapTo(card, QPoint(0, 0)), row_w.size())
        assert card.rect().contains(r), r
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
    frame.media_data = lambda: {'title': long_title}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: calls.append('voice')
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = 'Ready'; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud._dispatch_media_action = calls.append
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
    for gone in ('Providers', 'Audio', 'Pet',
                 'REMOTE CONTROL'):
        assert gone not in texts, gone
    # 'Customize' exists exactly once: the header button that
    # opens the in-window overlay (Batch E) — not the old
    # bottom-row button this test originally pinned away.
    assert 'Commands' in texts and 'Add file' in texts
    # (1) the track label is capped at two lines, full title kept.
    assert window.track.toolTip() == long_title
    assert window.track.height() <= \
        window.track.fontMetrics().lineSpacing() * 2 + 8
    assert len(window.track.text()) < len(long_title)
    # (1) no two left-column widgets overlap, and every one FITS
    # inside the card at minimum size — no scrolling (Alek,
    # 2026-10-27: 'make all the stuff in the systems monitor panel
    # fit in the page without scrolling').
    from PyQt6.QtCore import QPoint, QRect
    widgets = [window.plaque_engine, window.plaque_state,
               window.plaque_tasks, window.cpu, window.mem, window.net,
               window.power, window.subsystems,
               window.now_playing_title, window.track,
               window.volume]
    card = window.left_card
    rects = [QRect(w.mapTo(card, QPoint(0, 0)), w.size())
             for w in widgets]
    for i, a in enumerate(rects):
        assert card.rect().contains(a), a
        for b in rects[i + 1:]:
            assert not a.intersects(b), (a, b)
    window.close()


def test_classic_layout_rollback(monkeypatch):
    monkeypatch.delenv('ARIA_UI_LAYOUT', raising=False)
    assert dashboard_enabled()
    monkeypatch.setenv('ARIA_UI_LAYOUT', 'classic')
    assert not dashboard_enabled()


def test_chat_html_colours_speakers_and_escapes():
    # Alek, 2026-10-27, revised same day: Mark-LVI's LogWidget
    # tagged lines by kind — ARIA in the accent family, "you" in
    # WHITE, system muted. The first cut here made his side
    # amber/pale-yellow and he called it "all text yellow". Chat
    # content is escaped — a message is never markup.
    from aria.dashboard import chat_html, activity_html
    out = chat_html([('10:00:01', 'ARIA', 'Hello <b>there</b>'),
                     ('10:00:05', 'Alek', 'Hi ARIA'),
                     ('10:00:09', 'System', 'Voice ready')])
    assert '#54d9e8' in out and '#dff9ff' in out      # ARIA: name + text
    assert '#ffffff' in out and '#f4f7fa' in out      # Alek: white, not yellow
    assert '#ffe3b3' not in out and '#ffb84d' not in out
    assert '#8ea3b6' in out                          # System: muted
    assert '&lt;b&gt;' in out and '<b>there</b>' not in out.replace(
        '<b style=', '')
    # Activity: levels earn their colours (LVI's err/file/sys
    # tags) — error red, warn amber, ok green, info muted.
    act = activity_html([('10:01:00', 'error', 'Boom <i>x</i>', {}),
                         ('10:01:01', 'warn', 'Careful', {}),
                         ('10:01:02', 'ok', 'Done', {}),
                         ('10:01:03', 'info', 'Routine', {})])
    assert '#ff6b6b' in act and '#ffb84d' in act
    assert '#2ee6a8' in act and '#c2d2de' in act
    assert '&lt;i&gt;' in act


def test_pet_mode_hides_the_dashboard(monkeypatch, tmp_path):
    # Alek, 2026-10-27: pet mode replaces the dashboard — entering
    # it hides the main window; leaving it (toggle or the pet's
    # own raise) brings the window back.
    app = QApplication.instance() or QApplication([])
    calls = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama', subsystems=[],
                task_active=0, scheduled=0, voice_enabled=True, volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': ''}
    frame.media_data = lambda: {'title': ''}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: None
    frame.receive_file = lambda p: None
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = ''; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = lambda a: None
    hud._dispatch_media_action = lambda a: None
    hud.set_hud_volume = lambda v: None
    config = types.ModuleType('aria.config'); config.redact = str
    config.log_silent = lambda *args: None
    config.get_setting = lambda key, default=None: default
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
        submit=calls.append, request_stop=lambda: None,
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    assert window.isVisible()
    # An open dialog (Settings et al.) must close when pet mode
    # starts — pet mode is ONLY the pet (Alek, 2026-10-27).
    from PyQt6.QtWidgets import QDialog
    stray = QDialog(window); stray.show(); app.processEvents()
    assert stray.isVisible()
    window.toggle_pet(); app.processEvents()
    assert window._pet.isVisible() and not window.isVisible()
    assert not stray.isVisible()
    window.toggle_pet(); app.processEvents()
    assert not window._pet.isVisible() and window.isVisible()
    window.toggle_pet(); app.processEvents()
    window._raise_from_pet(); app.processEvents()
    assert not window._pet.isVisible() and window.isVisible()
    window.close()


def test_now_playing_panel(monkeypatch, tmp_path):
    # Batch B (Alek, 2026-10-27): NOW PLAYING shows track and
    # artist on separate lines with a state dot. (The camera PiP
    # tile that shipped in the same batch was REMOVED the same
    # day at Alek's word — annoying on the dash, and he doesn't
    # want constant camera watching. The last assert pins its
    # absence.)
    app = QApplication.instance() or QApplication([])
    calls = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama', subsystems=[],
                task_active=0, scheduled=0, voice_enabled=True, volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.media_data = lambda: {'running': True, 'playing': True,
                                'status': 'playing', 'source': 'smtc',
                                'app': 'Spotify',
                                'title': 'The Artist - The Song',
                                'artist': 'The Artist', 'track': 'The Song'}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: None
    frame.receive_file = lambda p: None
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = ''; hud.WHISPER_MODE = False
    spotify_calls = []
    hud._dispatch_media_action = spotify_calls.append
    hud._dispatch_spotify_action = spotify_calls.append
    hud._dispatch_media_action = spotify_calls.append
    hud.set_hud_volume = lambda v: None
    config = types.ModuleType('aria.config'); config.redact = str
    config.log_silent = lambda *args: None
    config.get_setting = lambda key, default=None: default
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
        submit=calls.append, request_stop=lambda: None,
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.show(); app.processEvents(); window.render_once(); app.processEvents()
    assert window.track.text() == 'The Song\nThe Artist'
    assert '#2ee6a8' in window.now_playing_title.styleSheet()
    assert not hasattr(window, 'cam_tile')   # PiP removed (Alek)
    # Explicit Play AND Pause buttons dispatch the explicit
    # actions (Alek, 2026-10-27 — no blind toggle labelled Play).
    assert set(window.media_buttons) == {'previous', 'play', 'pause', 'next'}
    window.media_buttons['pause'].click(); app.processEvents()
    window.media_buttons['play'].click(); app.processEvents()
    assert spotify_calls == ['pause', 'play']
    # Paused (SMTC status) shows PAUSED — and still shows the
    # actual track, not a standby placeholder.
    frame.media_data = lambda: {'running': True, 'playing': False,
                                'status': 'paused', 'source': 'smtc',
                                'app': 'Spotify',
                                'title': 'The Artist - The Song',
                                'artist': 'The Artist', 'track': 'The Song'}
    window._last_refresh = 0   # age past render_once's 0.8s panel throttle
    window.render_once(); app.processEvents()
    assert window.now_playing_title.text() == '❚❚ PAUSED'
    assert window.track.text() == 'The Song\nThe Artist'
    assert window.media_progress.isHidden()
    # A MOVIE in another app (Alek, 2026-10-27 — the panel is
    # not Spotify-only): header names the app, second line is
    # the player since a movie has no artist.
    frame.media_data = lambda: {'running': True, 'playing': False,
                                'status': 'paused', 'source': 'smtc',
                                'app': 'VLC', 'title': 'Some Movie.mkv',
                                'artist': '', 'track': 'Some Movie.mkv'}
    window._last_refresh = 0
    window.render_once(); app.processEvents()
    assert window.now_playing_title.text() == '❚❚ PAUSED · VLC'
    assert window.track.text() == 'Some Movie.mkv\nVLC'
    # ARIA's own player: real position/duration drives the bar.
    frame.media_data = lambda: {'running': True, 'playing': True,
                                'status': 'playing', 'source': 'hud',
                                'app': 'ARIA player', 'title': 'Clip.mp4',
                                'artist': '', 'track': 'Clip.mp4',
                                'position_ms': 30000, 'duration_ms': 60000}
    window._last_refresh = 0
    window.render_once(); app.processEvents()
    assert not window.media_progress.isHidden()
    assert window.media_progress.value() == 500
    window.close()


def test_center_composite_is_opaque_and_layered(monkeypatch, tmp_path):
    # Alek, 2026-10-27: the phone bridge orb was a blown-out cyan
    # disc — the publish path dropped the centrepiece grab's
    # alpha, flattening every glow gradient. The composite must
    # be opaque, filled with the card colour at the corners, and
    # visibly layered (field/orb) toward the middle.
    app = QApplication.instance() or QApplication([])
    calls = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama', subsystems=[],
                task_active=0, scheduled=0, voice_enabled=True, volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': ''}
    frame.media_data = lambda: {'title': ''}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: None
    frame.receive_file = lambda p: None
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = ''; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = lambda a: None
    hud._dispatch_media_action = lambda a: None
    hud.set_hud_volume = lambda v: None
    config = types.ModuleType('aria.config'); config.redact = str
    config.log_silent = lambda *args: None
    config.get_setting = lambda key, default=None: default
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
        submit=calls.append, request_stop=lambda: None,
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.show(); app.processEvents(); window.render_once(); app.processEvents()
    from PyQt6.QtGui import QImage
    img = window._composite_center_image(window.face)
    assert img.format() == QImage.Format.Format_BGR888
    corner = img.pixelColor(2, 2)
    # The corner is backdrop-dark (card #0d1520 / face #080d14
    # family), never blown-out glow colour.
    assert corner.red() < 40 and corner.green() < 48 and corner.blue() < 56
    centre = img.pixelColor(img.width() // 2, img.height() // 2)
    assert (centre.red(), centre.green(), centre.blue()) != (
        corner.red(), corner.green(), corner.blue())
    window.close()


def test_no_customize_or_extensions_buttons_on_dashboard(monkeypatch, tmp_path):
    # Alek, 2026-10-27 (later): the header Customize / Extensions
    # overlay buttons are REMOVED — Extensions lives as a tab in
    # Settings, Customize was already there. The header keeps
    # Settings only.
    app = QApplication.instance() or QApplication([])
    calls = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama', subsystems=[],
                task_active=0, scheduled=0, voice_enabled=True, volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': ''}
    frame.media_data = lambda: {'title': ''}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: None
    frame.receive_file = lambda p: None
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = ''; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = lambda a: None
    hud._dispatch_media_action = lambda a: None
    hud.set_hud_volume = lambda v: None
    config = types.ModuleType('aria.config'); config.redact = str
    config.log_silent = lambda *args: None
    config.get_setting = lambda key, default=None: default
    config.set_setting = lambda *a, **k: None
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
    mcp = types.ModuleType('aria.mcp')
    mcp.get_servers = lambda: {'fs': {'transport': 'stdio', 'command': 'npx fs'}}
    monkeypatch.setitem(sys.modules, 'aria.mcp', mcp)
    monkeypatch.setattr(sys.modules['aria'], 'mcp', mcp, raising=False)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: None, ptt_release=lambda: None,
        interrupt=lambda: None, toggle_whisper=lambda: None,
        submit=calls.append, request_stop=lambda: None,
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    assert not hasattr(window, 'customize_button')
    assert not hasattr(window, 'extensions_button')
    assert not hasattr(window, '_overlay')
    from PyQt6.QtWidgets import QPushButton
    texts = [b.text() for b in window.findChildren(QPushButton)]
    assert 'Customize' not in texts and 'Extensions' not in texts, texts
    assert 'Settings' in texts
    window.close()


def test_orb_publish_falls_back_when_composite_fails(monkeypatch, tmp_path):
    # Alek, 2026-10-27 07:17: after the composite landed, the
    # bridge orb VANISHED — a failing composite must degrade to
    # the plain grab, never to no frames at all.
    app = QApplication.instance() or QApplication([])
    calls = []
    published = []
    data = dict(cpu=25, mem=40, battery='80%', uptime_s=600,
                active_provider='Ollama', subsystems=[],
                task_active=0, scheduled=0, voice_enabled=True, volume=.5)
    frame = types.ModuleType('aria.frame')
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: '10m'
    frame.spotify_data = lambda: {'title': ''}
    frame.media_data = lambda: {'title': ''}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: None
    frame.receive_file = lambda p: None
    hud = types.ModuleType('aria.hud')
    hud.CURRENT_STATE = 'idle'; hud.SUBTITLE_TEXT = ''; hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = lambda a: None
    hud._dispatch_media_action = lambda a: None
    hud.set_hud_volume = lambda v: None
    config = types.ModuleType('aria.config'); config.redact = str
    config.log_silent = lambda *args: None
    config.get_setting = lambda key, default=None: default
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
    vision = types.ModuleType('aria.vision')
    vision.publish_face_frame = lambda pixels, box: published.append(pixels)
    monkeypatch.setitem(sys.modules, 'aria.vision', vision)
    monkeypatch.setattr(sys.modules['aria'], 'vision', vision, raising=False)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: None, ptt_release=lambda: None,
        interrupt=lambda: None, toggle_whisper=lambda: None,
        submit=calls.append, request_stop=lambda: None,
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    def boom(center):
        raise RuntimeError('composite unavailable')
    window._composite_center_image = boom
    window.render_once(); app.processEvents()
    assert published, 'publish must still happen via the grab fallback'
    assert published[0].size > 0
    window.close()
