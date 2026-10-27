"""Confirm banner port: widget fidelity, approval-store wiring, dashboard.

The banner is adapted from Mark-LVI-ARIA's ConfirmBanner (see
aria/confirm_banner.py). These tests pin the properties that make a
confirmation a confirmation: the full text is shown untruncated, CANCEL
is the default, and a click resolves through aria/approval.py's single
execution path (approve_token / deny_token) — never a second one.
"""
import os
import sys
import time
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
import numpy as np
from PyQt6.QtWidgets import QApplication, QWidget

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg


def _app():
    return QApplication.instance() or QApplication([])


# ---------------- widget ----------------

def test_banner_shows_full_text_untruncated():
    app = _app()
    parent = QWidget(); parent.resize(1440, 900); parent.show()
    from aria.confirm_banner import ConfirmBanner
    title = 'Approve run_python_code? ' + 'with a very long title ' * 12
    detail = 'line of detail\n' * 120
    banner = ConfirmBanner(title, detail, parent)
    assert banner._title_label.text() == title
    assert banner._detail_label.text() == detail
    banner.close(); parent.close(); app.processEvents()


def test_banner_cancel_is_default_and_signals_answer():
    app = _app()
    parent = QWidget(); parent.resize(1440, 900); parent.show()
    from aria.confirm_banner import ConfirmBanner
    seen = []
    banner = ConfirmBanner('Approve write_file?', 'write file x', parent)
    banner.answered.connect(seen.append)
    assert banner._cancel_button.isDefault()
    banner._confirm_button.click()
    assert seen == [True]
    banner2 = ConfirmBanner('Approve write_file?', 'write file x', parent)
    banner2.answered.connect(seen.append)
    banner2._cancel_button.click()
    assert seen == [True, False]
    banner.close(); banner2.close(); parent.close(); app.processEvents()


def test_banner_width_follows_parent():
    app = _app()
    from aria.confirm_banner import ConfirmBanner
    small = QWidget(); small.resize(500, 400); small.show()
    assert ConfirmBanner('t', 'd', small).width() == 500 - 32
    big = QWidget(); big.resize(2000, 1000); big.show()
    assert ConfirmBanner('t', 'd', big).width() == 980
    small.close(); big.close(); app.processEvents()


def test_banner_legibility_pass():
    # Alek, 2026-10-27: "so small and cramped. It's illegible."
    # The LVI-inherited 600px cap and 9-10pt type are pinned
    # GONE: on a real window the banner takes a real share of
    # it, and every text element is 11pt or larger with
    # full-size buttons.
    app = _app()
    parent = QWidget(); parent.resize(1600, 900); parent.show()
    from aria.confirm_banner import ConfirmBanner
    banner = ConfirmBanner('Approve run_shell_command?',
                           'rm -rf build && make release', parent)
    assert banner.width() >= 850
    assert banner._title_label.font().pointSize() >= 12
    assert banner._detail_label.font().pointSize() >= 11
    assert banner._confirm_button.height() >= 40
    assert banner._cancel_button.height() >= 40
    banner.close(); parent.close(); app.processEvents()


# ---------------- approval store ----------------

def _approval_with_stub_config(monkeypatch):
    import importlib
    approval = importlib.import_module('aria.approval')
    stub = types.SimpleNamespace(
        redact=str, add_log=lambda *a: None, log_silent=lambda *a: None)
    monkeypatch.setattr(approval, 'config', stub)
    approval._PENDING.clear()
    return approval


def test_pending_entries_roundtrip(monkeypatch):
    approval = _approval_with_stub_config(monkeypatch)
    out = approval.request_approval(
        'write_file', {'filename': 'x.txt', 'content': 'hi'})
    assert out.startswith('[AWAITING_APPROVAL token=')
    entries = approval.pending_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry['tool'] == 'write_file'
    assert 'x.txt' in entry['desc']
    assert entry['age_s'] >= 0
    assert approval.deny_token(entry['token']).startswith('Denied')
    assert approval.pending_entries() == []
    approval._PENDING.clear()


def test_pending_entries_approve_executes_via_dispatch(monkeypatch):
    approval = _approval_with_stub_config(monkeypatch)
    tools_pkg = types.ModuleType('aria.tools'); tools_pkg.__path__ = []
    dispatch = types.ModuleType('aria.tools.dispatch')
    seen = []
    dispatch.execute_tool = lambda tool, args, preauthorized=False: \
        seen.append((tool, preauthorized)) or ('EXECUTED', True)
    monkeypatch.setitem(sys.modules, 'aria.tools', tools_pkg)
    monkeypatch.setitem(sys.modules, 'aria.tools.dispatch', dispatch)
    approval.request_approval('write_file', {'filename': 'y.txt'})
    token = approval.pending_entries()[0]['token']
    assert approval.approve_token(token) == 'EXECUTED'
    assert seen == [('write_file', True)]
    assert approval.pending_entries() == []
    approval._PENDING.clear()


# ---------------- dashboard wiring ----------------

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
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: calls.append('press'),
        ptt_release=lambda: calls.append('release'),
        interrupt=lambda: calls.append('stop'),
        toggle_whisper=lambda: calls.append('whisper'),
        submit=calls.append, request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    return callbacks


def test_dashboard_banner_approves_and_denies(monkeypatch):
    app = _app()
    callbacks = _dashboard_stubs(monkeypatch)
    state = {'entries': [], 'approved': [], 'denied': []}
    fake = types.ModuleType('aria.approval')
    fake.pending_entries = lambda: list(state['entries'])
    fake.approve_token = lambda t: state['approved'].append(t) or 'ok'
    fake.deny_token = lambda t: state['denied'].append(t) or 'ok'
    monkeypatch.setitem(sys.modules, 'aria.approval', fake)
    monkeypatch.setattr(sys.modules['aria'], 'approval', fake, raising=False)

    from aria.dashboard import create_dashboard
    from aria.confirm_banner import ConfirmBanner
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()

    window._poll_approvals()
    assert window._confirm_banner is None

    state['entries'] = [{'token': 'tok1', 'tool': 'write_file',
                         'desc': 'write file a.txt', 'age_s': 3}]
    window._poll_approvals()
    banner = window._confirm_banner
    assert isinstance(banner, ConfirmBanner) and banner.isVisible()
    assert 'write file a.txt' in banner._detail_label.text()
    banner._cancel_button.click()
    assert state['denied'] == ['tok1']
    assert window._confirm_banner is None

    state['entries'] = [{'token': 'tok2', 'tool': 'run_python_code',
                         'desc': 'run Python code', 'age_s': 1}]
    window._poll_approvals()
    window._confirm_banner._confirm_button.click()
    deadline = time.time() + 3
    while not state['approved'] and time.time() < deadline:
        time.sleep(0.02)
    assert state['approved'] == ['tok2']

    # Resolved elsewhere while a banner is up: the next poll hides it.
    state['entries'] = [{'token': 'tok3', 'tool': 'send_email',
                         'desc': 'send email', 'age_s': 0}]
    window._poll_approvals()
    assert window._confirm_banner is not None
    state['entries'] = []
    window._poll_approvals()
    assert window._confirm_banner is None

    window.close(); app.processEvents()
