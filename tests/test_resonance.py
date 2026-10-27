"""Render the actual adapted orb across states and measured levels."""
import os
import sys
import types
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication
pkg = types.ModuleType('aria'); pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules['aria'] = pkg
from aria.resonance import ResonanceOrb
from aria import audio_levels


@pytest.mark.parametrize('state', ['idle', 'listening', 'speaking', 'thinking', 'coding'])
def test_orb_renders_without_pixel_renderer(state):
    app = QApplication.instance() or QApplication([])
    orb = ResonanceOrb(); orb.resize(600, 500)
    for i in range(400): orb.advance(state, (i % 30) / 29)
    image = orb.grab().toImage()
    assert not image.isNull() and len(orb._amp_hist) == 288
    assert len(orb._wave_rings) <= 6
    orb.advance(state, 1, muted=True)
    assert not orb.grab().isNull()
    orb.close(); app.processEvents()


def test_measured_level_expires(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(audio_levels.time, 'monotonic', lambda: now[0])
    audio_levels.publish(.8); assert audio_levels.current() == .8
    now[0] += .3; assert audio_levels.current() == 0
    assert audio_levels.mp3_envelope(b'bad data', None) == []


def _name_band_dark_count(orb):
    img = orb.grab().toImage()
    dark = 0
    for x in range(400 - 90, 400 + 90, 2):
        for y in range(400 - 16, 400 + 16, 2):
            c = img.pixelColor(x, y)
            if c.red() < 45 and c.green() < 45 and c.blue() < 45:
                dark += 1
    return dark


def test_orb_name_ink_is_per_surface():
    # Alek, 2026-10-27 (corrected same morning): the PET's orb
    # name is dark ink; the DASHBOARD's orb name stays white.
    # dark_name is the pet-only switch — render both and
    # measure the glyph band over the bright core.
    app = QApplication.instance() or QApplication([])
    dash = ResonanceOrb(); dash.resize(800, 800)
    pet = ResonanceOrb(dark_name=True); pet.resize(800, 800)
    for _ in range(60):
        dash.advance('idle', 0.0)
        pet.advance('idle', 0.0)
    assert _name_band_dark_count(pet) > 40
    assert _name_band_dark_count(dash) < 15
    dash.close(); pet.close(); app.processEvents()
