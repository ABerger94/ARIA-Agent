"""Memory constellation: layout engine + widget (Alek, 2026-10-27).

Layout (pure numpy): facts with embeddings land by MEANING —
same-cluster stars closer than cross-cluster — facts without
embeddings park on a unit ring, everything sits on the unit
sphere, near-identical facts get an edge. Widget (offscreen
Qt): drag rotates the ball (pitch clamped), a click picks the
front star under it, empty space clears the selection.
"""
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg

import numpy as np  # noqa: E402

from aria import memory_map  # noqa: E402


def _vec(*xs):
    return np.array(xs, dtype=np.float64)


def _rows():
    return [
        {"category": "a", "key": "k1", "value": "one",
         "updated": "2026-10-26", "vec": _vec(1, 0, 0, 0)},
        {"category": "a", "key": "k2", "value": "two",
         "updated": "2026-10-25", "vec": _vec(0.9, 0.1, 0, 0)},
        {"category": "b", "key": "k3", "value": "three",
         "updated": "2026-10-24", "vec": _vec(0, 1, 0, 0)},
        {"category": "b", "key": "k4", "value": "four",
         "updated": "2026-10-23", "vec": None},
    ]


def _ang(p, q):
    return float(np.arccos(np.clip(p @ q, -1.0, 1.0)))


def test_layout_clusters_by_meaning_and_parks_unembedded():
    pts, edges = memory_map.layout_points(_rows())
    assert pts.shape == (4, 3)
    for p in pts:
        assert abs(float(np.linalg.norm(p)) - 1.0) < 1e-6
    # Same-cluster pair closer than the cross-cluster pair.
    assert _ang(pts[0], pts[1]) < _ang(pts[0], pts[2])
    # Near-identical vectors (cos ~0.99) get an edge.
    assert (0, 1) in edges


def test_layout_empty_store():
    pts, edges = memory_map.layout_points([])
    assert pts.shape == (0, 3)
    assert edges == []


def test_widget_rotates_picks_and_clears():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from aria.constellation import ConstellationWidget
    w = ConstellationWidget()
    w.resize(400, 400)
    w.show()
    app.processEvents()
    rows = _rows()
    w.set_facts(rows)
    assert not w.grab().isNull()

    yaw0 = w._yaw
    w.rotate_by(50, 0)
    assert abs(w._yaw - (yaw0 + 0.4)) < 1e-9
    w.rotate_by(0, 100000)
    assert w._pitch == 1.35          # clamped: the ball never flips

    view = w._view_points()
    sx, sy, _r = w._screen_xy(view)
    front = int(np.argmax(view[:, 2]))
    key = w.pick(float(sx[front]), float(sy[front]))
    assert key == rows[front]["key"]
    assert w.selected_key() == key
    # Empty corner clears the selection.
    assert w.pick(3, 3) is None
    assert w.selected_key() is None
    w.close()
    app.processEvents()
