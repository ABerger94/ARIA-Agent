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


def test_orb_name_is_dark_ink_on_the_core():
    # Alek, 2026-10-27: the A.R.I.A. name in the orb's centre
    # is black / dark grey, not white — it sits on the bright
    # core and the white washed out. Render the real orb and
    # measure the glyph band: plenty of near-black pixels where
    # the name is drawn, in the middle of a bright core.
    app = QApplication.instance() or QApplication([])
    orb = ResonanceOrb(); orb.resize(800, 800)
    for _ in range(60):
        orb.advance('idle', 0.0)
    img = orb.grab().toImage()
    cx, cy = 400, 400
    dark = 0
    for x in range(cx - 90, cx + 90, 2):
        for y in range(cy - 16, cy + 16, 2):
            c = img.pixelColor(x, y)
            if c.red() < 45 and c.green() < 45 and c.blue() < 45:
                dark += 1
    assert dark > 40, dark
    orb.close(); app.processEvents()
