"""The 3D waveform orb (aria/orb3d.py) — Alek, 2026-10-27.

Contract under test:
* the ResonanceOrb driving contract: advance(state, level,
  muted) runs and the ball actually paints (non-blank grab);
* drag rotation: rotate_by turns yaw, tilts pitch, and
  pitch clamps at ±1.35 like the memory map;
* the palette is the flat orb's: apply_accent forwards to
  aria.resonance (one palette, two orbs).
"""
import importlib
import sys
import types
from pathlib import Path

from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)


def _app():
    return QApplication.instance() or QApplication([])


def test_orb3d_advances_paints_rotates():
    app = _app()
    orb3d = importlib.import_module('aria.orb3d')
    w = orb3d.Orb3D(transparent_background=True)
    w.resize(520, 520)
    w.show()
    app.processEvents()
    for _ in range(30):
        w.advance('speaking', 0.7, False)
    app.processEvents()
    img = w.grab().toImage()
    lit = 0
    for x in range(40, 480, 9):
        for y in range(40, 480, 9):
            if img.pixelColor(x, y).alpha() > 12:
                lit += 1
    assert lit > 60, lit

    yaw0, pitch0 = w._yaw, w._pitch
    w.rotate_by(120, 0)
    assert w._yaw != yaw0
    w.rotate_by(0, 10000)          # slam the tilt: clamps
    assert w._pitch == 1.35
    w.rotate_by(0, -20000)
    assert w._pitch == -1.35
    assert pitch0 == -0.38
    w.close()
    app.processEvents()


def test_orb3d_accent_is_resonances():
    orb3d = importlib.import_module('aria.orb3d')
    resonance = importlib.import_module('aria.resonance')
    assert orb3d.apply_accent('#ff0000') is True
    assert resonance.C.PRI != '#00d4ff'
    assert orb3d.apply_accent(None) is True
    assert resonance.C.PRI == '#00d4ff'
