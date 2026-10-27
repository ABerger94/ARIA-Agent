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
    mem = types.ModuleType('aria.memory')
    mem.memory_rows_for_ui = lambda: []
    mem.memory_forget_key = lambda key: False
    mem.memory_map_points = lambda: []
    for name, mod in [('aria.frame', frame), ('aria.hud', hud),
                      ('aria.config', config), ('aria.ui_prefs', prefs),
                      ('aria.memory', mem)]:
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
    # The dashboard's Centerpiece button is gone (Alek,
    # 2026-10-27): the choice lives in Settings → Customize,
    # which calls window.set_centerpiece — the swap and the
    # persistence it drives are what pin here now.
    app = _app()
    callbacks, calls = _dashboard_stubs(monkeypatch, centerpiece='orb')
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    assert window.holo is not None
    assert not hasattr(window, 'centerpiece_btn')
    assert not hasattr(window, 'whisper')
    # The live speech subtitle under the state line is gone too
    # (Alek, 2026-10-27 — the Conversation log carries her words).
    assert not hasattr(window, 'subtitle')
    assert window.face.isVisible() and not window.holo.isVisible()
    window.set_centerpiece('holo')
    assert window.holo.isVisible() and not window.face.isVisible()
    assert ('cp', 'holo') in calls
    window.set_centerpiece('orb')
    assert window.face.isVisible() and not window.holo.isVisible()
    assert ('cp', 'orb') in calls
    window.close(); app.processEvents()


def test_dashboard_memory_tab_lists_and_forgets(monkeypatch):
    # Alek, 2026-10-27 (his pick: a tab, not the LVI overlay):
    # the Workspace gains a Memory tab listing every stored
    # fact from aria.memory's UI rows, and each row's ✕ forgets
    # exactly that fact and re-renders the store as it stands.
    from PyQt6.QtWidgets import QPushButton
    app = _app()
    callbacks, calls = _dashboard_stubs(monkeypatch)
    fake_mem = types.ModuleType('aria.memory')
    store = [
        {'category': 'user', 'key': 'fav_color', 'value': 'blue',
         'updated': '2026-10-20'},
        {'category': 'fact', 'key': 'job', 'value': 'Little Caesars',
         'updated': '2026-10-26'},
    ]
    fake_mem.memory_rows_for_ui = lambda: list(store)

    def _forget(key):
        before = len(store)
        store[:] = [r for r in store if r['key'] != key]
        return len(store) != before
    fake_mem.memory_forget_key = _forget
    monkeypatch.setitem(sys.modules, 'aria.memory', fake_mem)
    monkeypatch.setattr(sys.modules['aria'], 'memory', fake_mem,
                        raising=False)
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    names = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert 'Memory' in names
    assert window.mem_count.text().startswith('2 stored facts')
    rms = [b for b in window.mem_rows.findChildren(QPushButton)
           if not b.icon().isNull()]
    assert len(rms) == 2
    # The × is PAINTED PIXELS, not a font glyph (Alek,
    # 2026-10-27: two glyph attempts rendered as bare
    # outlines on his build — the global button padding left
    # the letter no room). The icon's centre pixel is white.
    px = rms[0].icon().pixmap(28, 28).toImage().pixelColor(14, 14)
    assert px.alpha() > 200 and px.red() > 200 and px.blue() > 200
    assert rms[0].styleSheet() == 'padding: 0;'
    rms[0].click(); app.processEvents()
    assert [r['key'] for r in store] == ['job']   # first row forgotten
    assert window.mem_count.text().startswith('1 stored facts')
    window.close(); app.processEvents()


def test_dashboard_centerpiece_memory_map(monkeypatch):
    # Alek, 2026-10-27: the constellation is a third
    # centerpiece — set_centerpiece('map') shows the ball in
    # the center stack (orb hidden), and the Memory tab's Map
    # mode swaps the fact list for the same widget.
    app = _app()
    callbacks, calls = _dashboard_stubs(monkeypatch)
    fake_mem = types.ModuleType('aria.memory')
    fake_mem.memory_rows_for_ui = lambda: []
    fake_mem.memory_forget_key = lambda key: False
    fake_mem.memory_map_points = lambda: [
        {'category': 'fact', 'key': 'job', 'value': 'Little Caesars',
         'updated': '2026-10-26',
         'vec': np.array([1.0, 0.0, 0.0], dtype=np.float32)}]
    monkeypatch.setitem(sys.modules, 'aria.memory', fake_mem)
    monkeypatch.setattr(sys.modules['aria'], 'memory', fake_mem,
                        raising=False)
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    window.set_centerpiece('map')
    app.processEvents()
    assert window._centerpiece == 'map'
    assert window.mem_map_center.isVisible()
    assert not window.face.isVisible()
    assert ('cp', 'map') in calls
    # The map's ✕ (Alek, 2026-10-27): visible only while the
    # map owns the center, and clicking it hands the center
    # back to the normal centerpiece — the orb.
    assert window.map_close.isVisible()
    window.map_close.click()
    app.processEvents()
    assert window._centerpiece == 'orb'
    assert window.face.isVisible()
    assert not window.mem_map_center.isVisible()
    assert not window.map_close.isVisible()
    window.set_centerpiece('orb')
    app.processEvents()
    assert window.face.isVisible()
    assert not window.mem_map_center.isVisible()
    # Tab Map mode (select the Memory tab first — a background
    # tab's children report not-visible regardless).
    idx = [window.tabs.tabText(i) for i in range(window.tabs.count())].index('Memory')
    window.tabs.setCurrentIndex(idx); app.processEvents()
    window._set_memory_mode('map'); app.processEvents()
    assert window.mem_map_box.isVisible()
    assert not window.mem_scroll.isVisible()
    window._set_memory_mode('list'); app.processEvents()
    assert window.mem_scroll.isVisible()
    assert not window.mem_map_box.isVisible()
    window.close(); app.processEvents()


def test_dashboard_f11_fullscreen_toggle(monkeypatch):
    # Alek, 2026-10-27, Mark-LVI style: F11 is a QShortcut on
    # the window — first press true full screen, second press
    # back to maximized (her normal full-window state, never a
    # floating window).
    app = _app()
    callbacks, _calls = _dashboard_stubs(monkeypatch, centerpiece='orb')
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.showMaximized(); app.processEvents()
    assert window.isMaximized() and not window.isFullScreen()
    assert window._fullscreen_shortcut.key().toString() == 'F11'
    window._toggle_fullscreen()
    assert window.isFullScreen()
    window._toggle_fullscreen()
    assert not window.isFullScreen() and window.isMaximized()
    window.close(); app.processEvents()


def test_dashboard_starts_on_saved_holo(monkeypatch):
    app = _app()
    callbacks, _calls = _dashboard_stubs(monkeypatch, centerpiece='holo')
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    assert window.holo.isVisible() and not window.face.isVisible()
    window.close(); app.processEvents()


def test_dashboard_vision_pip_on_demand(monkeypatch):
    # Alek, 2026-10-27: ask her what she sees and a PiP shows the
    # frame, Mark-LVI style (VISUAL INPUT header, 10s auto-hide).
    # Hidden until a vision tool publishes a frame; a poll with
    # nothing new leaves it hidden.
    app = _app()
    callbacks, _calls = _dashboard_stubs(monkeypatch, centerpiece='orb')
    from aria.dashboard import create_dashboard
    window = create_dashboard(callbacks)
    window.show(); app.processEvents()
    assert hasattr(window, '_pip') and not window._pip.isVisible()
    window._poll_vision_pip()
    assert not window._pip.isVisible()
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage
    img = QImage(16, 12, QImage.Format.Format_RGB888)
    img.fill(QColor('#ff8800'))
    buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, 'JPG')
    fake_vision = types.ModuleType('aria.vision')
    fake_vision.vision_preview = lambda: {
        'jpeg': bytes(buf.data()), 'label': 'CAMERA', 'ts': 123.0}
    monkeypatch.setitem(sys.modules, 'aria.vision', fake_vision)
    monkeypatch.setattr(sys.modules['aria'], 'vision', fake_vision,
                        raising=False)
    window._poll_vision_pip()
    assert window._pip.isVisible()
    assert not window._pip.img.pixmap().isNull()
    assert 'CAMERA' in window._pip.title.text()
    window._pip._timer.timeout.emit()
    assert not window._pip.isVisible()
    window.close(); app.processEvents()
