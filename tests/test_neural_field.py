"""NeuralField: Mark-LVI's neuron layer behind the centrepiece.
Pins the field's contract: a seeded fixed graph (same network
every frame), nodes clear of the central disc, pulses that travel
an edge and die at the far node, and a paint that survives every
state colour."""
import os
import sys
import types

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication

pkg = types.ModuleType('aria')
pkg.__path__ = [os.path.join(os.path.dirname(__file__), '..', 'aria')]
sys.modules.setdefault('aria', pkg)

from aria.neural_field import NeuralField
import math
import random


_APP = None


def _field():
    global _APP
    # The QApplication wrapper must outlive the widget: drop the
    # last reference and sip deletes the app — and every widget
    # with it — mid-test.
    _APP = QApplication.instance() or QApplication([])
    w = NeuralField()
    w.resize(640, 480)
    w.show()
    _APP.processEvents()
    w.grab()                       # forces a paint → builds the cache
    return w


def test_graph_is_fixed_and_clears_the_centre():
    w = _field()
    nodes, edges, lines = w._cache
    assert len(nodes) >= 40 and len(edges) >= 40 and len(lines) == len(edges)
    cx, cy = 320, 240
    r_out = min(640, 480) * 0.34
    for x, y in nodes:
        assert math.hypot(x - cx, y - cy) >= r_out - 1e-6
    again = NeuralField._make(640, 480, cx, cy, r_out)
    assert again[0] == nodes and again[1] == edges   # seeded: same brain


def test_pulses_travel_and_die(monkeypatch):
    w = _field()
    random.seed(7)
    for _ in range(200):           # spawning is stochastic; give it room
        w._step(0.05)
        if w._pulses:
            break
    assert w._pulses, 'no pulse ever spawned'
    w._pulses = [[0, 0.0, 1.0]]
    for _ in range(15):            # 15 × 0.1 of travel ≥ the whole edge
        w._step(1.0)
    assert w._pulses == [] or all(p[0] != 0 or p[1] < 1.0 for p in w._pulses)


def test_advance_and_paint_every_state():
    w = _field()
    for state in ('idle', 'speaking', 'listening', 'thinking', 'processing'):
        w.advance(state, False)
        assert not w.grab().isNull()
    w.advance('speaking', True)    # muted recolors, never crashes
    assert not w.grab().isNull()
