"""Durable missions on Shell Home (2026-10-31, Alek's approved
design): active mission cards ride the V13/V21 Today strip —
goal, status, step progress — with Pause/Resume and Cancel
wired through the panel's actor seam to aria.missions' own
ops.

Measured HOSTED, in the real Dashboard window (the V16
lesson): the panel's missions provider/actor seams are faked
(the missions store itself is covered by tests/test_missions
.py), everything else — window, panel, strip layout, button
wiring — is production code.
"""
import os
import sys
import types

os.environ["QT_QPA_PLATFORM"] = "offscreen"
import pytest
pytest.importorskip("PyQt6")
import numpy as np
from PyQt6.QtWidgets import QApplication, QPushButton

pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(__file__), "..", "aria")]
sys.modules["aria"] = pkg
from aria.dashboard import create_dashboard

_APP = None


def _window(monkeypatch):
    global _APP
    _APP = QApplication.instance() or QApplication([])
    calls = []
    data = dict(cpu=25, mem=40, battery="80%", uptime_s=600,
                active_provider="Ollama",
                subsystems=[("Vision", "ONLINE", True)],
                task_active=0, scheduled=1, voice_enabled=True,
                volume=.5)
    frame = types.ModuleType("aria.frame")
    frame.strip_data = lambda: data
    frame._fmt_uptime = lambda seconds: "10m"
    frame.spotify_data = lambda: {"title": "Test track"}
    frame.media_data = lambda: {}
    frame.chat_entries = lambda n: []
    frame.activity_events = lambda n: []
    frame.toggle_voice = lambda: calls.append("voice")
    frame.receive_file = lambda p: calls.append(p)
    hud = types.ModuleType("aria.hud")
    hud.CURRENT_STATE = "idle"
    hud.SUBTITLE_TEXT = "Ready"
    hud.WHISPER_MODE = False
    hud._dispatch_spotify_action = calls.append
    hud._dispatch_media_action = calls.append
    hud.set_hud_volume = calls.append
    config = types.ModuleType("aria.config")
    config.redact = str
    config.log_silent = lambda *args: None
    config.get_setting = lambda key, default=None: default
    for name, mod in [("aria.frame", frame), ("aria.hud", hud),
                      ("aria.config", config)]:
        monkeypatch.setitem(sys.modules, name, mod)
        monkeypatch.setattr(sys.modules["aria"],
                            name.rsplit(".", 1)[1], mod,
                            raising=False)
    queue = types.ModuleType("aria.agent.taskqueue")
    queue.get_queue = lambda: types.SimpleNamespace(
        get_all_statuses=lambda: [])
    scheduler = types.ModuleType("aria.scheduler")
    scheduler.sched_list = lambda: []
    monkeypatch.setitem(sys.modules, "aria.agent.taskqueue", queue)
    monkeypatch.setitem(sys.modules, "aria.scheduler", scheduler)
    speech = types.ModuleType("aria.speech")
    speech.VOICE_ENABLED = True
    monkeypatch.setitem(sys.modules, "aria.speech", speech)
    monkeypatch.setattr(sys.modules["aria"], "speech", speech,
                        raising=False)
    callbacks = types.SimpleNamespace(
        ptt_press=lambda: calls.append("press"),
        ptt_release=lambda: calls.append("release"),
        interrupt=lambda: calls.append("stop"),
        toggle_whisper=lambda: calls.append("whisper"),
        submit=calls.append,
        request_stop=lambda: calls.append("shutdown"),
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3),
                                      dtype=np.uint8))
    window = create_dashboard(callbacks)
    window.show()
    _APP.processEvents()
    return window


def _today_buttons(shell):
    out = []
    for i in range(shell.today_rows.count()):
        w = shell.today_rows.itemAt(i).widget()
        if w is None:
            continue
        out.extend(w.findChildren(QPushButton))
    return out


def _today_texts(shell):
    from PyQt6.QtWidgets import QLabel
    texts = []
    for i in range(shell.today_rows.count()):
        w = shell.today_rows.itemAt(i).widget()
        if w is None:
            continue
        texts.extend(lbl.text() for lbl in w.findChildren(QLabel))
    return texts


def test_hosted_mission_card_pause_and_cancel(monkeypatch):
    window = _window(monkeypatch)
    try:
        shell = window.shell
        acted = []
        card = {"id": 7, "goal": "sort the downloads folder",
                "status": "running", "done": 1, "total": 3}
        shell._missions_provider = lambda: [card]
        shell._mission_actor = lambda mid, action: acted.append(
            (mid, action))
        window.open_shell_home()
        shell.refresh_today()
        _APP.processEvents()
        assert shell.today_frame.isVisible()
        texts = _today_texts(shell)
        assert any("Mission:" in t and "sort the downloads" in t
                   and "1/3 steps" in t for t in texts), texts
        buttons = {b.text(): b for b in _today_buttons(shell)}
        assert "Pause" in buttons and "Cancel" in buttons
        buttons["Pause"].click()
        assert acted == [(7, "pause")]
        buttons = {b.text(): b for b in _today_buttons(shell)}
        buttons["Cancel"].click()
        assert acted == [(7, "pause"), (7, "cancel")]
    finally:
        window.close()


def test_hosted_paused_mission_shows_resume(monkeypatch):
    window = _window(monkeypatch)
    try:
        shell = window.shell
        card = {"id": 9, "goal": "back up the photo drive",
                "status": "paused", "done": 0, "total": 2}
        shell._missions_provider = lambda: [card]
        shell._mission_actor = lambda mid, action: None
        window.open_shell_home()
        shell.refresh_today()
        _APP.processEvents()
        buttons = {b.text() for b in _today_buttons(shell)}
        assert "Resume" in buttons and "Cancel" in buttons
        assert "Pause" not in buttons
    finally:
        window.close()


def test_hosted_no_missions_no_mission_row(monkeypatch):
    window = _window(monkeypatch)
    try:
        shell = window.shell
        shell._missions_provider = lambda: []
        window.open_shell_home()
        shell.refresh_today()
        _APP.processEvents()
        assert not any("Mission:" in t
                       for t in _today_texts(shell))
    finally:
        window.close()
