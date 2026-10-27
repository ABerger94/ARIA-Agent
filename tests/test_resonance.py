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
