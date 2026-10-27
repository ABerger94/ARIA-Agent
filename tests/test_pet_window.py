"""Port 7 pet mode: window behaviour, drag/click split, forms, prefs.

Pinned behaviours (from the Mark-LVI PetWindow): frameless + on-top +
Tool + no-focus + never-quit-on-close; the animation timer runs only
while visible; a plain click raises the main window (pet stays); a
drag parks the window and persists the spot; a double-click exits pet
mode; the form swaps between the two centerpieces.
"""
import os
import sys
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg


def _app():
    return QApplication.instance() or QApplication([])


def _mouse(win, etype, x, y, button=Qt.MouseButton.LeftButton):
    ev = QMouseEvent(etype, QPointF(x, y), QPointF(x, y), button,
                     button if etype.name != 'MouseButtonRelease'
                     else Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier)
    return ev


def _pet(monkeypatch, tmp_path, form='orb'):
    import importlib
    prefs = importlib.import_module('aria.ui_prefs')
    monkeypatch.setattr(prefs, '_ui_file',
                        lambda: str(tmp_path / 'ui.json'))
    calls = {'raise': 0, 'exit': 0, 'states': 0}

    def provider():
        calls['states'] += 1
        return ('speaking', 0.7, False)
    pet_mod = importlib.import_module('aria.pet_window')
    pet = pet_mod.PetWindow(
        None, provider=provider,
        on_raise=lambda: calls.__setitem__('raise', calls['raise'] + 1),
        on_exit=lambda: calls.__setitem__('exit', calls['exit'] + 1),
        form=form)
    return pet, calls, prefs


def test_pet_window_flags_and_size(monkeypatch, tmp_path):
    app = _app()
    pet, _calls, _prefs = _pet(monkeypatch, tmp_path)
    flags = pet.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool
    assert pet.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert not pet.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose)
    assert (pet.width(), pet.height()) == (288, 288)
    pet.close(); app.processEvents()


def test_pet_translucent_and_face_fully_inside(monkeypatch, tmp_path):
    # The two witnessed defects (2026-10-27): an opaque background,
    # and the orb's right side cut off because the face's 280px
    # minimum overflowed the old 240x260 window (anchored
    # top-left). Pin both shut: translucency attributes on the
    # window AND the face, and the face geometry fully inside.
    app = _app()
    pet, _calls, _prefs = _pet(monkeypatch, tmp_path)
    assert pet.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not pet.autoFillBackground()
    pet.show(); app.processEvents()
    for form in ('orb', 'holo'):
        pet.set_form(form)
        app.processEvents()
        face = pet.face
        assert face.testAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground)
        assert pet.rect().contains(face.geometry()), (
            form, face.geometry(), pet.rect())
        assert face.geometry() == pet.rect().adjusted(4, 4, -4, -4)
    # The rendered corners are actually transparent pixels.
    img = pet.grab().toImage()
    assert img.hasAlphaChannel()
    assert img.pixelColor(0, 0).alpha() == 0
    assert img.pixelColor(img.width() - 1, 0).alpha() == 0
    pet.close(); app.processEvents()


def test_pet_timer_only_while_visible_and_form_swap(monkeypatch,
                                                    tmp_path):
    app = _app()
    pet, calls, _prefs = _pet(monkeypatch, tmp_path)
    from aria.resonance import ResonanceOrb
    from aria.holo_face import HoloFace
    assert isinstance(pet.face, ResonanceOrb)
    assert not pet._timer.isActive()
    pet.show(); app.processEvents()
    assert pet._timer.isActive()
    pet._tick()
    assert calls['states'] >= 1
    pet.set_form('holo')
    assert isinstance(pet.face, HoloFace)
    pet.set_form('orb')
    assert isinstance(pet.face, ResonanceOrb)
    pet.hide(); app.processEvents()
    assert not pet._timer.isActive()
    pet.close(); app.processEvents()


def test_pet_click_raises_drag_parks_doubleclick_exits(monkeypatch,
                                                       tmp_path):
    app = _app()
    pet, calls, prefs = _pet(monkeypatch, tmp_path)
    pet.move(100, 100); pet.show(); app.processEvents()
    # Plain click: press + release, no movement -> raise, pet stays.
    pet.mousePressEvent(_mouse(pet, QMouseEvent.Type.MouseButtonPress,
                               10, 10))
    pet.mouseReleaseEvent(_mouse(pet, QMouseEvent.Type.MouseButtonRelease,
                                 10, 10))
    assert calls['raise'] == 1 and pet.isVisible()
    # Drag: press, move well past threshold, release -> parked + saved.
    pet.mousePressEvent(_mouse(pet, QMouseEvent.Type.MouseButtonPress,
                               10, 10))
    ev = _mouse(pet, QMouseEvent.Type.MouseMove, 60, 40)
    # globalPosition drives the drag math; feed global coords directly.
    pet._press_pos = QPoint(110, 110)
    pet.mouseMoveEvent(QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(60, 40), QPointF(160, 140),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    pet.mouseReleaseEvent(_mouse(pet, QMouseEvent.Type.MouseButtonRelease,
                                 60, 40))
    assert pet.pos() != QPoint(100, 100)
    assert prefs.get_pet_pos() == [pet.x(), pet.y()]
    assert calls['raise'] == 1               # a drag is not a click
    # Double-click exits pet mode.
    pet.mouseDoubleClickEvent(_mouse(
        pet, QMouseEvent.Type.MouseButtonDblClick, 10, 10))
    assert calls['exit'] == 1 and not pet.isVisible()
    pet.close(); app.processEvents()


def test_pet_menu_readable_and_window_style_scoped(monkeypatch, tmp_path):
    # Regression (Alek, 2026-10-27): after the translucency fix the
    # right-click menu rendered all black — text invisible until
    # hover. Cause: the window's UNSCOPED 'background: transparent'
    # stylesheet cascaded into the QMenu child. Pins: the window
    # style is ID-scoped to the window itself, and the menu carries
    # its own explicit light-text-on-dark colours.
    app = _app()
    pet, _calls, _prefs = _pet(monkeypatch, tmp_path)
    assert pet.styleSheet().startswith('#petWindow'), pet.styleSheet()
    menu = pet._build_menu()
    assert [a.text() for a in menu.actions()] == [
        'Open ARIA', 'Pet form: Character', 'Pet form: Waveform orb',
        'Hide pet']
    qss = menu.styleSheet()
    assert 'background-color: #232733' in qss     # menu background
    assert 'color: #eef1f8' in qss                # menu text
    assert 'QMenu::item:selected' in qss         # hover stays visible
    pet.close(); app.processEvents()


def test_clamp_pet_pos():
    from aria.pet_window import clamp_pet_pos
    assert clamp_pet_pos(-50, 5000, 240, 260, 1920, 1080) == (0, 820)
    assert clamp_pet_pos(100, 100, 240, 260, 1920, 1080) == (100, 100)
