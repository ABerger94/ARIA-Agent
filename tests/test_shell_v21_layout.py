"""Shell Home V21 layout (2026-10-31, Alek's order on his
09:20 live screenshot): "Take the briefing card use the
entire space of both the today and briefing card. Bump the
today card up higher so it's in line with the Good morning,
Alek and time lines."

So: Today leaves the right rail and becomes a compact
horizontal strip in the header band (aligned with the
clock/greeting lines); the Briefing card takes the WHOLE
rail — the full space Today + Briefing used to share.

ALL geometry here is measured in the REAL Dashboard window
(the V16 lesson: standalone panel geometry passed while the
hosted layout failed on Alek's machine), at his size
(1920x1003) and a squeezed laptop size (1366x740).

Pre-change hosted baseline (measured on 8f41bd5c):
  1920x1003: Today top 213 (in the rail), Briefing 323px
             (body 275); full-vs-empty Briefing differs 44px.
  1366x740:  Briefing squeezed to 117px.
"""
import os
import sys
import types

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
import numpy as np
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QPushButton

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


def _patch_today_remote(monkeypatch, empty=False):
    """Feed Today's remote half through the seams the hosted
    panel actually reads (aria.ical + aria.tools.builtins —
    the exact lazy imports _default_today_remote makes)."""
    import time as _time
    from datetime import datetime, timedelta
    builtins = types.ModuleType('aria.tools.builtins')
    tools_pkg = types.ModuleType('aria.tools')
    tools_pkg.__path__ = []
    tools_pkg.builtins = builtins
    ical = types.ModuleType('aria.ical')
    if empty:
        builtins._ical_url = lambda: ''
        builtins.tool_read_email = (
            lambda unread_only=True, limit=99:
            'No messages match the search criteria.')
        ical.upcoming_from_feed = lambda url, days=7: []
    else:
        builtins._ical_url = lambda: 'stub://feed'
        builtins.tool_read_email = (
            lambda unread_only=True, limit=99: '\n'.join(
                f'[uid={i}] A — S{i}' for i in range(50)))
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


def _patch_tasks(monkeypatch):
    from datetime import datetime, timedelta
    sched = sys.modules['aria.scheduler']
    past = (datetime.now() - timedelta(minutes=30)).strftime(
        '%Y-%m-%d %H:%M:%S')
    monkeypatch.setattr(
        sched, 'sched_list',
        lambda: [(7, 'once', 'Take your medication', None, past)],
        raising=False)


def _kick_remote(shell):
    shell._today_fetched_at = None
    shell.refresh_today_remote()
    shell.refresh_today()


def _settle(shell, time_mod, want_rows):
    app = QApplication.instance()
    end = time_mod.time() + 10
    while time_mod.time() < end:
        app.processEvents()
        model = shell._today_model
        if want_rows:
            if (shell._today_remote.get('unread') == 50
                    and model and model['event']
                    and model['tasks']):
                break
        else:
            thread = shell._today_thread
            if (thread is None or not thread.is_alive()) \
                    and model is not None:
                break
        time_mod.sleep(0.01)
    for _ in range(3):
        app.processEvents()
    if not want_rows:
        shell.refresh_today()
        app.processEvents()


def _hosted(monkeypatch, width, height, today='full',
            approval_entries=None):
    """A hosted Shell Home in the real Dashboard window.
    today: 'full' (event + due task + 50 unread), 'empty'
    (nothing to show), or None (Today never fed). When
    approval_entries is a list, a fake aria.approval seam
    serves it — the panel's default provider reads it through
    the same `from aria import approval` path production
    uses. Returns (window, control) where control can mutate
    the approval list live."""
    control = {'approval_entries': approval_entries}
    if approval_entries is not None:
        fake = types.ModuleType('aria.approval')
        fake.pending_entries = lambda: list(
            control['approval_entries'])
        fake.approve_token = lambda token: 'executed'
        fake.deny_token = lambda token: None
        monkeypatch.setitem(sys.modules, 'aria.approval', fake)
        monkeypatch.setattr(sys.modules['aria'], 'approval',
                            fake, raising=False)
    window, _calls = _window(monkeypatch, show=False)
    time_mod = None
    if today is not None:
        time_mod = _patch_today_remote(
            monkeypatch, empty=(today == 'empty'))
        if today == 'full':
            _patch_tasks(monkeypatch)
    window.resize(width, height)
    window.show()
    window.open_shell_home()
    if today is not None:
        _kick_remote(window.shell)
        _settle(window.shell, time_mod,
                want_rows=(today == 'full'))
    else:
        QApplication.instance().processEvents()
    return window, control


def _top(widget, ancestor):
    return widget.mapTo(ancestor, QPoint(0, 0)).y()


def _left(widget, ancestor):
    return widget.mapTo(ancestor, QPoint(0, 0)).x()


# -- (a) the strip lives in the header band ---------------------

@pytest.mark.parametrize('size', [(1920, 1003), (1366, 740)])
def test_v21_today_strip_sits_in_header_band(monkeypatch, size):
    window, _c = _hosted(monkeypatch, *size)
    try:
        shell = window.shell
        panel = shell
        assert shell.today_frame.isVisible()
        clock_top = _top(shell.clock_label, panel)
        today_top = _top(shell.today_frame, panel)
        today_bottom = today_top + shell.today_frame.height()
        # Aligned with the clock/greeting lines: the strip's
        # top sits in the header band, not down in the rail.
        assert today_top <= clock_top + 40
        # Right of the greeting/clock column (the header row),
        # and fully above the app area it used to sit beside.
        assert (_left(shell.today_frame, panel)
                > _left(shell.greeting_label, panel))
        assert today_bottom <= _top(shell.grid_stack, panel)
        assert today_top < _top(shell.briefing_frame, panel)
    finally:
        window.close()


# -- (b) Briefing takes the whole rail --------------------------

@pytest.mark.parametrize('size,min_h,min_body,min_frac', [
    ((1920, 1003), 450, 400, 0.55),
    ((1366, 740), 220, 180, None),
])
def test_v21_briefing_fills_the_whole_rail(monkeypatch, size,
                                           min_h, min_body,
                                           min_frac):
    window, _c = _hosted(monkeypatch, *size)
    try:
        shell = window.shell
        panel = shell
        brief_top = _top(shell.briefing_frame, panel)
        # The card now starts where the rail starts — at the
        # main row's top, above the app grid's top edge (the
        # search row shares that top on the left column).
        assert brief_top < _top(shell.grid_stack, panel) - 30
        # And it runs the rail's full height: materially the
        # old Today+Briefing space combined (pre-change the
        # card was 323px at 1920x1003, 117px at 1366x740).
        assert shell.briefing_frame.height() >= min_h
        assert shell.briefing_body.height() >= min_body
        # The whole rail span, top to the dock: nothing else
        # lives in the rail anymore, so the card's height is
        # the span itself (minus layout spacing slack). This
        # is the size-invariant form — at 740px tall the fixed
        # header band takes a larger share of the panel, so a
        # panel fraction is only asserted at the large size.
        span = _top(shell.dock, panel) - brief_top
        assert shell.briefing_frame.height() >= span - 30
        if min_frac is not None:
            assert (shell.briefing_frame.height()
                    >= min_frac * panel.height())
    finally:
        window.close()


# -- (c) no clipped Today controls in the strip -----------------

def test_v21_today_buttons_whole_inside_strip_squeezed(
        monkeypatch):
    window, _c = _hosted(monkeypatch, 1366, 740)
    try:
        shell = window.shell
        btns = [b for b in shell.today_frame.findChildren(
            QPushButton) if b.isVisible()]
        assert ({b.text() for b in btns}
                >= {'Open', 'Done', 'Snooze +1h'})
        frame = shell.today_frame
        for b in btns:
            assert b.width() >= b.sizeHint().width()
            need = (b.fontMetrics().horizontalAdvance(b.text())
                    + 36)
            assert b.width() >= need
            origin = b.mapTo(frame, b.rect().topLeft())
            assert origin.x() >= 0
            assert origin.x() + b.width() <= frame.width()
            assert origin.y() >= 0
            assert origin.y() + b.height() <= frame.height()
    finally:
        window.close()


# -- (d) empty Today: no hole, Briefing keeps the space ---------

def test_v21_empty_today_leaves_no_gap(monkeypatch):
    full, _c = _hosted(monkeypatch, 1920, 1003, today='full')
    try:
        full_h = full.shell.briefing_frame.height()
        full_top = _top(full.shell.briefing_frame, full.shell)
    finally:
        full.close()
    empty, _c = _hosted(monkeypatch, 1920, 1003, today='empty')
    try:
        shell = empty.shell
        assert not shell.today_frame.isVisible()
        # Nothing-to-show collapses cleanly: the Briefing
        # keeps exactly the space it has when Today shows
        # (in the rail layout it used to GROW by ~44px when
        # Today hid — proof Today was eating rail space).
        assert abs(shell.briefing_frame.height()
                   - full_h) <= 10
        assert abs(_top(shell.briefing_frame, shell)
                   - full_top) <= 10
    finally:
        empty.close()


# -- (e) V11 action cards keep priority; Briefing yields --------

def test_v21_action_cards_priority_briefing_yields(monkeypatch):
    entry = {'token': 'tok-v21', 'tool': 'run_shell_command',
             'desc': 'git pull origin main', 'age_s': 3}
    base, _c = _hosted(monkeypatch, 1920, 1003)
    try:
        base_h = base.shell.briefing_frame.height()
    finally:
        base.close()
    window, control = _hosted(monkeypatch, 1920, 1003,
                              approval_entries=[entry])
    try:
        shell = window.shell
        shell.refresh_actions()
        QApplication.instance().processEvents()
        assert shell.approval_frame.isVisible()
        # The action card keeps its priority visibility and
        # the Briefing yields exactly the card's space...
        yielded = base_h - shell.briefing_frame.height()
        assert yielded >= shell.approval_frame.height() - 20
        # ...while still holding a real reading area.
        assert shell.briefing_frame.height() >= 250
        # Clear the approval and the Briefing re-expands.
        control['approval_entries'] = []
        shell.refresh_actions()
        QApplication.instance().processEvents()
        assert not shell.approval_frame.isVisible()
        assert abs(shell.briefing_frame.height()
                   - base_h) <= 10
    finally:
        window.close()
