"""MediaVisualizer widget + BarAnimator (offscreen Qt)."""
import os
import sys
import types
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import media_audio
from aria.visualizer import BarAnimator, MediaVisualizer


_APP = None


def _app():
    # Keep a module-level reference: a QApplication whose Python
    # wrapper is garbage-collected takes the C++ app down with it,
    # and the next QWidget aborts the process.
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_animator_attacks_fast_and_decays_slow():
    a = BarAnimator(4)
    a.step([1.0, 1.0, 1.0, 1.0])
    first = a.values[0]
    assert 0.3 < first < 1.0
    a.step([1.0, 1.0, 1.0, 1.0])
    assert a.values[0] > first
    before = a.values[0]
    a.step([0.0, 0.0, 0.0, 0.0])
    assert 0.0 < a.values[0] < before


def test_animator_clamps_and_pads():
    a = BarAnimator(3)
    out = a.step([5.0])
    assert len(out) == 3
    assert all(0.0 <= v <= 1.0 for v in out)


def test_widget_paints_with_bands_and_track():
    _app()
    w = MediaVisualizer()
    w.resize(900, 500)
    w.set_track('Test Song', 'Test Artist')
    w.set_accent('#54d9e8')
    media_audio.publish(
        tuple(0.2 + 0.6 * ((i % 7) / 6) for i in range(media_audio.BANDS)),
        live=True)
    w._tick()
    w.show()
    assert not w.grab().isNull()
    w.hide()


def test_widget_show_starts_feed_and_hide_stops():
    _app()
    w = MediaVisualizer()
    w.resize(640, 360)
    w.show()
    assert w._timer.isActive()
    w.hide()
    assert not w._timer.isActive()
    media_audio.stop()
