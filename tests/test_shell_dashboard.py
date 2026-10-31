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


def _window(monkeypatch, show=True):
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
    if show:
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
    # V5: in the shell, the SAME row button is the Dashboard
    # button — the panel has no exit button of its own.
    assert window.shell_btn.text() == 'Dashboard'

    window.shell_btn.click()
    assert not window.shell.isVisible()
    assert window.face.isVisible()
    assert not window.isFullScreen()
    assert window.shell_btn.text() == 'Shell Home'
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
    window.shell_btn.click()
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


def test_shell_boot_unshown_window_button_reads_dashboard(monkeypatch):
    # V7 regression (Alek, 2026-10-30): Shell Mode boots through
    # qthost calling open_shell_home() BEFORE the window is shown.
    # _shell_is_home() derived state from shell.isVisible(), which
    # stays False under an unshown parent even after show() — so
    # the button synced to 'Shell Home' on an already-open shell,
    # and the first click re-opened instead of exiting. State is
    # now an explicit flag, so the boot order cannot desync it.
    # This test reproduces the boot order exactly: the dashboard
    # is built and NEVER shown before open_shell_home() runs.
    window, _calls = _window(monkeypatch, show=False)
    try:
        assert not window.isVisible()
        window.open_shell_home()          # the qthost boot order
        assert window._shell_is_home()
        assert window.shell_btn.text() == 'Dashboard'
        assert window._shell_full
        assert window.left_card.isHidden()
        assert window.right_card.isHidden()
        window.shell_btn.click()          # must EXIT, not re-open
        assert not window._shell_is_home()
        assert window.shell_btn.text() == 'Shell Home'
        assert not window._shell_full
        assert not window.left_card.isHidden()
        assert not window.right_card.isHidden()
    finally:
        window.close()


def test_exit_shell_home_preserves_fullscreen(monkeypatch):
    # V7 addendum (Alek, 2026-10-30): exiting Shell Home via the
    # Dashboard button used to drop the window out of fullscreen
    # (exit_shell_home called showMaximized when fullscreen).
    # Exit now preserves the window state — fullscreen stays
    # fullscreen; leaving fullscreen is F11's job.
    window, _calls = _window(monkeypatch)
    try:
        app = QApplication.instance()
        window.showFullScreen()
        app.processEvents()
        assert window.isFullScreen()
        window.open_shell_home()
        assert window.shell_btn.text() == 'Dashboard'
        window.shell_btn.click()
        assert not window._shell_is_home()
        assert window.isFullScreen()      # still fullscreen
        assert not window.left_card.isHidden()
        assert not window.right_card.isHidden()
    finally:
        window.close()


def test_exit_shell_home_leaves_maximized_state_untouched(monkeypatch):
    # Companion guard: a non-fullscreen exit changes no window
    # state either — a maximized dashboard stays maximized.
    window, _calls = _window(monkeypatch)
    try:
        app = QApplication.instance()
        window.showMaximized()
        app.processEvents()
        assert window.isMaximized()
        window.open_shell_home()
        window.shell_btn.click()
        assert not window._shell_is_home()
        assert window.isMaximized()
        assert not window.isFullScreen()
    finally:
        window.close()


def test_shell_button_is_state_aware(monkeypatch):
    # V5 (Alek, 2026-10-30): the dashboard's center-controls
    # button is the SAME button in both states — 'Shell Home'
    # opens the shell; while the shell is open it reads
    # 'Dashboard' and exits (restoring the side columns).
    win, calls = _window(monkeypatch)
    try:
        assert win.shell_btn.text() == 'Shell Home'
        win.shell_btn.click()
        assert win.shell.isVisible()
        assert win.shell_btn.text() == 'Dashboard'
        win.shell_btn.click()
        assert not win.shell.isVisible()
        assert win.shell_btn.text() == 'Shell Home'
        assert not win._shell_full
        assert not win.left_card.isHidden()
        assert not win.right_card.isHidden()
    finally:
        win.close()


def test_render_once_forwards_to_shell_orb_only_when_active(monkeypatch):
    # V9: render_once forwards its (state, level, muted) to the
    # shell header's waveform orb ONLY while Shell Home is the
    # active center (the V7 flag) — no forwarding, no second
    # drive, while the dashboard's own face owns the ticks.
    window, _calls = _window(monkeypatch)
    try:
        orb = window.shell.orb
        assert len(orb._amp_hist) == 0
        window.render_once()
        assert len(orb._amp_hist) == 0          # shell inactive
        window.open_shell_home()
        window.render_once()
        window.render_once()
        assert len(orb._amp_hist) == 2          # one per tick
        window.shell_btn.click()                # exit the shell
        assert not window._shell_is_home()
        window.render_once()
        assert len(orb._amp_hist) == 2          # forwarding stopped
    finally:
        window.close()


# -- Shell Home V17-C hosted geometry (2026-10-31) -------------
# V16's rail fix was verified standalone, and Alek's live
# witness on 789b0d26 still showed a short Briefing and Today
# 'Open' buttons cut mid-word. These tests therefore run in
# the REAL Dashboard window at his size (1920x1003), fed
# through the dashboard's own provider seams — never a
# standalone panel.

def _hosted_today(monkeypatch):
    """Feed Today through the seams the hosted panel actually
    reads: tasks via aria.scheduler.sched_list (the harness's
    stub module), the next event via aria.ical, unread via
    aria.tools.builtins — the exact lazy imports
    shell_panel._default_tasks / _default_today_remote make."""
    import time as _time
    from datetime import datetime, timedelta
    sched = sys.modules['aria.scheduler']
    past = (datetime.now() - timedelta(minutes=30)).strftime(
        '%Y-%m-%d %H:%M:%S')
    monkeypatch.setattr(
        sched, 'sched_list',
        lambda: [(7, 'once', 'Take your medication', None, past)],
        raising=False)
    builtins = types.ModuleType('aria.tools.builtins')
    builtins._ical_url = lambda: 'stub://feed'
    builtins.tool_read_email = (
        lambda unread_only=True, limit=99: '\n'.join(
            f'[uid={i}] A — S{i}' for i in range(50)))
    tools_pkg = types.ModuleType('aria.tools')
    tools_pkg.__path__ = []
    tools_pkg.builtins = builtins
    ical = types.ModuleType('aria.ical')
    now = datetime.now()
    ical.upcoming_from_feed = lambda url, days=7: [{
        'summary': 'Little Caesers',
        'start': now + timedelta(hours=8),
        'end': now + timedelta(hours=15),
        'all_day': False}]
    monkeypatch.setitem(sys.modules, 'aria.tools', tools_pkg)
    monkeypatch.setitem(sys.modules, 'aria.tools.builtins',
                        builtins)
    monkeypatch.setitem(sys.modules, 'aria.ical', ical)
    return _time


def _kick_remote(shell):
    """Reset the Today fetch throttle so a fetch runs against
    the just-patched seams (the panel throttles refetches to
    WEATHER_REFETCH_SECONDS; the first fetch at window build
    time used the unpatched stubs)."""
    shell._today_fetched_at = None
    shell.refresh_today_remote()
    shell.refresh_today()


def _settle_today(shell, time_mod):
    """Pump events until the off-thread remote fetch has
    landed AND the repainted model carries all three rows."""
    app = QApplication.instance()
    end = time_mod.time() + 10
    while time_mod.time() < end:
        app.processEvents()
        model = shell._today_model
        if (shell._today_remote.get('unread') == 50
                and model and model['event']
                and model['tasks']):
            break
        time_mod.sleep(0.01)
    for _ in range(3):
        app.processEvents()


def test_shell_hosted_briefing_gets_the_height(monkeypatch):
    """In the hosted window at 1920x1003 the Briefing card is
    materially taller than Today and its body clears 200px;
    growing the window grows the body (the rail's leftover is
    really the Briefing's)."""
    window, _calls = _window(monkeypatch, show=False)
    time_mod = _hosted_today(monkeypatch)
    try:
        shell = window.shell
        window.resize(1920, 1003)
        window.show()
        window.open_shell_home()
        _kick_remote(shell)
        _settle_today(shell, time_mod)
        assert shell.today_frame.isVisible()
        assert (shell.briefing_frame.height()
                > shell.today_frame.height() + 40)
        body = shell.briefing_body.height()
        assert body >= 200
        window.resize(1920, 1200)
        QApplication.instance().processEvents()
        assert shell.briefing_body.height() > body + 80
    finally:
        window.close()


def test_shell_hosted_today_buttons_render_whole(monkeypatch):
    """Alek's named acceptance item (2026-10-31 07:33): every
    Today row button in the HOSTED window — Open, Done,
    Snooze +1h — renders at no less than its size hint, fits
    its full caption width with padding to spare, and sits
    fully inside the Today card."""
    window, _calls = _window(monkeypatch, show=False)
    time_mod = _hosted_today(monkeypatch)
    try:
        from PyQt6.QtWidgets import QPushButton
        shell = window.shell
        window.resize(1920, 1003)
        window.show()
        window.open_shell_home()
        _kick_remote(shell)
        _settle_today(shell, time_mod)
        btns = [b for b in shell.today_frame.findChildren(
            QPushButton) if b.isVisible()]
        assert ({b.text() for b in btns}
                >= {'Open', 'Done', 'Snooze +1h'})
        frame_w = shell.today_frame.width()
        for b in btns:
            assert b.width() >= b.sizeHint().width()
            need = (b.fontMetrics().horizontalAdvance(b.text())
                    + 36)
            assert b.width() >= need
            origin = b.mapTo(shell.today_frame,
                             b.rect().topLeft())
            assert origin.x() + b.width() <= frame_w
    finally:
        window.close()


def test_shell_hosted_chrome_steps_aside_and_restores(
        monkeypatch):
    """The V17-C hosting fix: while Shell Home holds the
    window, the dashboard's state line and bottom command row
    are hidden (their height reaches the shell), the controls
    row with the Dashboard escape stays, and exiting restores
    everything — with the orb centerpiece laying out sanely
    again afterwards."""
    window, _calls = _window(monkeypatch, show=False)
    try:
        app = QApplication.instance()
        window.resize(1920, 1003)
        window.show()
        app.processEvents()
        stack_before = window.center_stack.height()
        window.open_shell_home()
        app.processEvents()
        assert window._shell_is_home()
        assert not window.state.isVisible()
        assert not window.entry.isVisible()
        assert window.shell_btn.isVisible()   # escape stays
        assert (window.center_stack.height()
                > stack_before + 60)
        window.exit_shell_home()
        app.processEvents()
        assert window.state.isVisible()
        assert window.entry.isVisible()
        assert not window.shell.isVisible()
        # The persisted centerpiece (whichever Settings holds)
        # is back on stage and lays out sanely.
        centerpiece = {'orb': window.face, 'room': window.room,
                       'holo': window.holo,
                       'map': window.mem_map_center,
                       'orb3d': window.orb3d}[
                           window._centerpiece]
        assert centerpiece.isVisible()
        stack = window.center_stack
        assert stack.width() > 400 and stack.height() > 300
    finally:
        window.close()
