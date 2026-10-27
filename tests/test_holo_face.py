"""Port 6 HoloFace: mesh build, paint, accent, dashboard centerpiece swap.

The renderer is adapted wholesale from Mark-LVI-ARIA's core/avatar.py +
core/avatar_mesh.py (see aria/holo_avatar.py / aria/holo_mesh.py), so
these tests pin the port's seams, not the renderer's internals: the
mesh builds from the bundled face_model.obj, a stepped + painted frame
is not blank, advance() honours the orb's contract, and the dashboard
swaps orb <-> holo and persists the choice.
"""
import os
import sys
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
import numpy as np
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg


def _app():
    return QApplication.instance() or QApplication([])


def test_mesh_builds_from_bundled_obj():
    from aria.holo_mesh import get_head_mesh
    mesh = get_head_mesh()
    assert mesh['verts'].shape[1] == 3
    assert mesh['verts'].shape[0] > 468        # face + cranium + neck
    assert get_head_mesh() is mesh            # cached, process-wide


def test_avatar_paints_a_nonblank_frame():
    app = _app()
    from aria.holo_avatar import HoloAvatar
    av = HoloAvatar()
    for _ in range(10):
        av.step(0.03, 0.6, speaking=True)
    img = QImage(400, 400, QImage.Format.Format_RGB32)
    img.fill(0xFF080D14)
    p = QPainter(img)
    from PyQt6.QtGui import QColor
    av.paint(p, 200, 210, 130, QColor('#00d4ff'), QColor('#ff6b00'),
             QColor('#080d14'))
    p.end()
    ptr = img.bits(); ptr.setsize(img.sizeInBytes())
    pixels = np.frombuffer(ptr, dtype=np.uint8).reshape(-1, 4)
    bg = np.array([0x14, 0x0D, 0x08, 0xFF], dtype=np.uint8)
    assert (pixels != bg).any(axis=1).sum() > 500   # aura + head, not blank
    app.processEvents()


def test_holo_face_advance_and_accent():
    app = _app()
    from aria import holo_face
    face = holo_face.HoloFace(); face.resize(360, 360); face.show()
    for state in ('idle', 'listening', 'speaking', 'thinking'):
        face.advance(state, 0.5, muted=False)
    assert not face.grab().isNull()
    assert holo_face.apply_accent('#ff0000')
    assert holo_face.HoloFace.PRIMARY == '#ff0000'
    assert not holo_face.apply_accent('bogus')
    assert holo_face.HoloFace.PRIMARY == '#ff0000'
    assert holo_face.apply_accent(None)
    assert holo_face.HoloFace.PRIMARY == '#00d4ff'
    face.close(); app.processEvents()


def _dashboard_stubs(monkeypatch, centerpiece='orb'):
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
    prefs = types.ModuleType('aria.ui_prefs')
    prefs.get_accent = lambda: None
    prefs.get_centerpiece = lambda: centerpiece
    prefs.set_centerpiece = lambda name: calls.append(('cp', name))
    prefs.set_accent = lambda a: None
    for name, mod in [('aria.frame', frame), ('aria.hud', hud),
                      ('aria.config', config), ('aria.ui_prefs', prefs)]:
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
        ptt_press=lambda: None, ptt_release=lambda: None,
        interrupt=lambda: None, toggle_whisper=lambda: None,
        submit=calls.append, request_stop=lambda: None,
        is_running=lambda: True, housekeeping=lambda: None,
        render_frame=lambda: np.zeros((800, 1280, 3), dtype=np.uint8))
    return callbacks, calls


def test_dashboard_centerpiece_swap_and_persist(monkeypatch):
    app = _app()
    callbacks, calls = _dashboard_stubs(monkeypatch, centerpiece='orb')
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    assert window.holo is not None
    assert window.face.isVisible() and not window.holo.isVisible()
    window.centerpiece_btn.click()
    assert window.holo.isVisible() and not window.face.isVisible()
    assert ('cp', 'holo') in calls
    window.centerpiece_btn.click()
    assert window.face.isVisible() and not window.holo.isVisible()
    assert ('cp', 'orb') in calls
    window.close(); app.processEvents()


def test_dashboard_starts_on_saved_holo(monkeypatch):
    app = _app()
    callbacks, _calls = _dashboard_stubs(monkeypatch, centerpiece='holo')
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    assert window.holo.isVisible() and not window.face.isVisible()
    window.close(); app.processEvents()
