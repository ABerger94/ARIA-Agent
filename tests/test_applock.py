"""App lock (Alek, 2026-10-27): the password hash, the Settings ->
General lock controls, and the dashboard gray-out overlay.

Scope pinned here: dashboard + local voice lock; the phone bridge
is deliberately NOT part of this suite — it keeps working.
"""
import os
import sys
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
import numpy as np
from PyQt6.QtWidgets import QApplication, QMainWindow
pkg = types.ModuleType('aria'); pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg


def _config_stub(monkeypatch, saved):
    config = types.ModuleType('aria.config'); config._KEYS = {}
    config.redact = str; config.log_silent = lambda *args: None
    config.get_setting = lambda key, default=None: saved.get(key, default)
    config.set_setting = lambda key, value: (saved.__setitem__(key, value), True)[1]
    config.settings_load = lambda: dict(saved)
    def save(values): saved.update(values); return True
    config.settings_save = save; config.key_get = lambda key, fallback='': ('', 'script default')
    config.save_keys = lambda: True; config.is_safe_mode = lambda: False
    monkeypatch.setitem(sys.modules, 'aria.config', config)
    monkeypatch.setattr(pkg, 'config', config, raising=False)
    return config


@pytest.fixture()
def lock_env(monkeypatch):
    saved = {}
    _config_stub(monkeypatch, saved)
    from aria import applock
    applock.set_locked(False)
    yield saved, applock
    applock.set_locked(False)


# ------------------------------------------------------------ password core

def test_password_roundtrip_and_normalization(lock_env):
    saved, applock = lock_env
    assert not applock.password_is_set()
    assert applock.set_password('Open Sesame') is True
    assert applock.password_is_set()
    # Only a salted hash is stored — never the password itself.
    stored = saved['lock_password_hash']
    assert 'Open' not in stored and '$' in stored
    assert applock.verify_password('Open Sesame') is True
    # Spoken passwords have no case and pick up punctuation.
    assert applock.verify_password('open sesame') is True
    assert applock.verify_password('Open Sesame.') is True
    assert applock.verify_password('  open sesame  ') is True


def test_wrong_and_missing_password_fail_closed(lock_env):
    saved, applock = lock_env
    assert applock.verify_password('anything') is False  # none set
    assert applock.unlock_with('anything') is False
    assert applock.set_password('correct horse') is True
    assert applock.verify_password('correct horse battery') is False
    assert applock.verify_password('') is False
    assert applock.set_password('   ') is False  # empty never stored
    assert applock.verify_password('correct horse') is True


def test_unlock_with_clears_only_on_match(lock_env):
    saved, applock = lock_env
    applock.set_password('hunter2')
    applock.lock()
    assert applock.is_locked()
    assert applock.unlock_with('hunter3') is False
    assert applock.is_locked()
    assert applock.unlock_with('hunter2') is True
    assert not applock.is_locked()


def test_same_password_hashes_differ(lock_env):
    saved, applock = lock_env
    applock.set_password('repeatable')
    first = saved['lock_password_hash']
    applock.set_password('repeatable')
    assert saved['lock_password_hash'] != first  # fresh salt each time
    assert applock.verify_password('repeatable') is True


# ------------------------------------------------------------ settings UI

def _settings_stubs(monkeypatch, saved):
    _config_stub(monkeypatch, saved)
    speech = types.ModuleType('aria.speech'); speech.VOICE_ENABLED = True
    hud = types.ModuleType('aria.hud'); hud.WHISPER_MODE = False
    hud.get_hud_volume = lambda: .5; hud.set_hud_volume = lambda v: None
    approval = types.ModuleType('aria.approval'); approval.MODES = ('auto', 'confirm-risky', 'confirm-all')
    approval.get_mode = lambda: 'confirm-risky'; approval.set_mode = lambda m: None
    for name, mod in [('speech', speech), ('hud', hud), ('approval', approval)]:
        monkeypatch.setitem(sys.modules, 'aria.' + name, mod); monkeypatch.setattr(pkg, name, mod, raising=False)


def test_settings_general_lock_controls(monkeypatch):
    app = QApplication.instance() or QApplication(['aria-applock-test'])
    saved = {}
    _settings_stubs(monkeypatch, saved)
    from aria import applock
    applock.set_locked(False)
    from aria.dashboard import STYLE
    from aria.ui_panels import create_settings_dialog
    calls = []
    class Parent(QMainWindow):
        def apply_accent(self, value): calls.append(('accent', value))
        def open_remote(self): calls.append('remote')
        def lock_app(self): calls.append('lock')
    parent = Parent(); parent.setStyleSheet(STYLE)
    callbacks = types.SimpleNamespace(speak=lambda t: None, toggle_whisper=lambda: None)
    dialog = create_settings_dialog(parent, callbacks)
    assert dialog.lock_btn is not None and dialog.lock_password_btn is not None
    assert dialog.lock_password_btn.text() == 'Set lock password'
    # The Lock button cannot fire before a password exists (the
    # password dialog would open instead) — set one, rebuild, and
    # the button locks immediately and closes the dialog.
    assert applock.set_password('hunter2') is True
    dialog2 = create_settings_dialog(parent, callbacks)
    assert dialog2.lock_password_btn.text() == 'Change lock password'
    dialog2.show(); app.processEvents()
    dialog2.lock_btn.click(); app.processEvents()
    assert calls == ['lock']
    assert not dialog2.isVisible()
    dialog.close(); dialog2.close(); parent.close()
    applock.set_locked(False)


# ------------------------------------------------------------ dashboard overlay

def test_dashboard_lock_overlay_roundtrip(monkeypatch):
    app = QApplication.instance() or QApplication(['aria-applock-test'])
    saved = {}
    config = _config_stub(monkeypatch, saved)
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
    speech = types.ModuleType('aria.speech'); speech.VOICE_ENABLED = True
    queue = types.ModuleType('aria.agent.taskqueue')
    queue.get_queue = lambda: types.SimpleNamespace(get_all_statuses=lambda: [])
    scheduler = types.ModuleType('aria.scheduler'); scheduler.sched_list = lambda: []
    for name, mod in [('aria.frame', frame), ('aria.hud', hud), ('aria.speech', speech),
                      ('aria.agent.taskqueue', queue), ('aria.scheduler', scheduler)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(pkg, name.rsplit('.', 1)[1], mod, raising=False)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: calls.append('press'), ptt_release=lambda: calls.append('release'),
        interrupt=lambda: calls.append('stop'), toggle_whisper=lambda: calls.append('whisper'),
        submit=calls.append, request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    from aria import applock
    from aria.dashboard import create_dashboard
    assert applock.set_password('hunter2') is True
    dash = create_dashboard(callbacks)
    dash.show(); app.processEvents()
    assert not dash._lock_overlay.isVisible()
    assert dash._root.isEnabled()
    dash.lock_app(); app.processEvents()
    assert applock.is_locked()
    assert dash._lock_overlay.isVisible()
    assert not dash._root.isEnabled()
    # Wrong password: still locked, overlay up, root still dead.
    dash._lock_field.setText('hunter3')
    dash._try_lock_unlock(); app.processEvents()
    assert applock.is_locked()
    assert dash._lock_overlay.isVisible()
    assert 'Wrong password' in dash._lock_status.text()
    # Right password: unlocked, overlay gone, root live again.
    dash._lock_field.setText('hunter2')
    dash._try_lock_unlock(); app.processEvents()
    assert not applock.is_locked()
    assert not dash._lock_overlay.isVisible()
    assert dash._root.isEnabled()
    dash.close()
