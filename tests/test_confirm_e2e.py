"""End-to-end approval, NOTHING in the chain faked.

Alek, 2026-10-27: clicked CONFIRM on the banner and "it
didn't do anything" — then: "Test it. Make sure it works."
Every pre-existing confirm test faked one side of the chain:
a fake approval store, a fake dispatch, or
QPushButton.click(), which fires the signal without ever
hit-testing the button a human actually presses.

This test runs the production path whole:
  dispatch.execute_tool (real gate) -> AWAITING_APPROVAL
  -> dashboard poll -> real ConfirmBanner -> QTest.mouseClick
  (a real synthetic mouse press on the button's pixels)
  -> _on_confirm_answered -> approve_token -> execute_tool
  preauthorized -> the real write_file handler writes a file.

The only stubs are the dashboard's unrelated subsystems
(frame/hud/speech/scheduler), copied from test_confirm_banner
per this suite's self-contained-file convention.
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
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg


def _app():
    return QApplication.instance() or QApplication([])


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
    # The real tool chain (dispatch -> sandbox/builtins) imports
    # constants from aria.config (WORKSPACE_DIR, MAX_TOOL_OUTPUT,
    # ...). Seed the stub with the real module's values, then
    # quiet the log functions the dashboard/approval call.
    import importlib
    _real_config = importlib.import_module('aria.config')
    for _k in dir(_real_config):
        if not _k.startswith('__'):
            setattr(config, _k, getattr(_real_config, _k))
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
        ptt_press=lambda: calls.append('press'),
        ptt_release=lambda: calls.append('release'),
        interrupt=lambda: calls.append('stop'),
        toggle_whisper=lambda: calls.append('whisper'),
        submit=calls.append, request_stop=lambda: calls.append('shutdown'),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))


def _run_flow(monkeypatch, approve: bool):
    """The whole chain once. Returns (target_path, approval).

    Each run uses its OWN filename/content: dispatch's per-turn
    duplicate guard records executed (tool, args) pairs for the
    process, and identical args in a second run are blocked as
    a duplicate before they ever reach the gate (found by this
    test's first version, the hard way)."""
    app = _app()
    callbacks = _dashboard_stubs(monkeypatch)
    import importlib
    import tempfile
    # V20: the approval store resolves via aria.data_dir (the
    # install root), not HOME — keep this suite's mode writes
    # hermetic in a temp root.
    _data_dir = importlib.import_module('aria.data_dir')
    monkeypatch.setattr(
        _data_dir, "_ROOT",
        tempfile.mkdtemp(prefix="aria_e2e_data_"))
    approval = importlib.import_module('aria.approval')
    approval._PENDING.clear()
    prior_mode = approval.get_mode()
    approval.set_mode('confirm-risky')
    from aria.tools import builtins, dispatch

    name = 'e2e-approve-test.txt' if approve else 'e2e-cancel-test.txt'
    content = 'approved-by-click' if approve else 'cancelled-by-click'
    target = Path(builtins.WORKSPACE_DIR) / name
    target.unlink(missing_ok=True)
    try:
        # 1. The real gate: an unapproved destructive call is
        #    held, NOT executed.
        result, ok = dispatch.execute_tool(
            'write_file', {'filename': name,
                           'content': content})
        assert ok is False and 'AWAITING_APPROVAL' in str(result), result
        assert not target.exists()
        entries = approval.pending_entries()
        assert len(entries) == 1

        # 2. The dashboard polls and raises the real banner.
        from aria.dashboard import create_dashboard
        window = create_dashboard(callbacks)
        window.show(); app.processEvents()
        window._poll_approvals()
        banner = window._confirm_banner
        assert banner is not None and banner.isVisible()

        # 3. A REAL mouse click on the button's pixels.
        button = banner._confirm_button if approve \
            else banner._cancel_button
        assert button.isVisible() and button.isEnabled()
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        app.processEvents()

        # 4. Approval executes on a worker thread: wait for it.
        deadline = time.time() + 6
        while time.time() < deadline:
            app.processEvents()
            if approve and target.exists():
                break
            if not window._confirm_busy and not approval.pending_entries():
                break
            time.sleep(0.02)
        app.processEvents()
        assert window._confirm_banner is None
        assert approval.pending_entries() == []
        window.close(); app.processEvents()
        return target, approval
    finally:
        approval.set_mode(prior_mode)
        approval._PENDING.clear()


def test_confirm_click_executes_the_held_call(monkeypatch):
    target, _approval = _run_flow(monkeypatch, approve=True)
    try:
        assert target.exists()
        assert target.read_text(encoding='utf-8') == 'approved-by-click'
    finally:
        target.unlink(missing_ok=True)


def test_cancel_click_drops_the_held_call(monkeypatch):
    target, _approval = _run_flow(monkeypatch, approve=False)
    assert not target.exists()
