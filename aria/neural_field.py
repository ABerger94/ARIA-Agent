"""NeuralField — the neuron layer from Mark-LVI-ARIA's HudCanvas,
as a standalone background widget for this dashboard's centre card.

Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
(HudCanvas._make_neurons / _step_neurons / _paint_neurons).
Original MARK LVI: Copyright FatihMakes; ARIA fork modifications:
Alek Berger. Adapted widget retains CC BY-NC 4.0 terms. See
LICENSES/Mark-LVI.txt.

Alek's original ask (2026-10-22, for LVI): moving lines behind the
reactor core / face, like neurons firing. A fixed node-and-
connection graph fills the canvas around the centrepiece; signal
pulses spawn at random, travel an edge as a bright head with a
fading trail, and die at the far node. The graph is precomputed
per size (seeded — the SAME network every frame, so firing reads
as activity in a fixed brain, not noise), nodes keep clear of the
central disc the orb / holo head occupies, and pulses step on the
dashboard's existing render tick via advance().
"""
from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import QLineF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

# This dashboard's palette (LVI's canvas used its own cyan family;
# the state semantics are LVI's _core_colours, unchanged).
PRI = '#54d9e8'
PRI_DIM = '#007a99'
ACC = '#ff6b00'
ACC2 = '#ffcc00'
GREEN = '#00ff88'
MUTED_C = '#ff3366'
BG = '#0d1520'          # the centre card's background


def _blend(col: QColor, a: float) -> QColor:
    """Pre-mix a colour onto the card background at strength a —
    LVI mixes onto its canvas BG the same way, which is what makes
    the faint wiring read as part of the surface, not ink on it."""
    bg = QColor(BG)
    k = max(0.0, min(1.0, a))
    return QColor(int(bg.red() + (col.red() - bg.red()) * k),
                  int(bg.green() + (col.green() - bg.green()) * k),
                  int(bg.blue() + (col.blue() - bg.blue()) * k))


class NeuralField(QWidget):
    """Paints only the field; the centrepiece widgets stack over
    it with transparent backgrounds. advance() is the tick."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._cache = None          # (nodes, edges, lines)
        self._key = None            # (W, H) the cache was built for
        self._pulses: list = []     # [edge_index, position, speed]
        self._state = 'idle'
        self._muted = False
        self._last = time.monotonic()

    # -- tick ------------------------------------------------------
    def advance(self, state: str = 'idle', muted: bool = False):
        now = time.monotonic()
        dt = now - self._last
        self._last = now
        self._state = str(state or 'idle')
        self._muted = bool(muted)
        self._step(dt)
        self.update()

    def _colours(self):
        if self._muted:
            return QColor(MUTED_C), QColor(MUTED_C)
        state = self._state.lower()
        if state == 'speaking':
            return QColor(PRI), QColor(ACC)
        if state in ('thinking', 'processing'):
            return QColor(PRI), QColor(ACC2)
        if state == 'listening':
            return QColor(PRI), QColor(GREEN)
        return QColor(PRI), QColor(PRI_DIM)

    # -- the field (LVI's geometry, verbatim logic) ----------------
    @staticmethod
    def _make(W: int, H: int, cx: float, cy: float, r_out: float):
        """Precompute the field for one size: (nodes, edges, lines).
        Nodes keep clear of the central disc the centrepiece
        occupies — the field lives around and behind it."""
        rng = random.Random(20261022)
        nodes: list = []
        min_d = min(W, H) * 0.075
        tries = 0
        while len(nodes) < 56 and tries < 1200:
            tries += 1
            x = rng.uniform(W * 0.03, W * 0.97)
            y = rng.uniform(H * 0.03, H * 0.97)
            if math.hypot(x - cx, y - cy) < r_out:
                continue
            if any(math.hypot(x - nx, y - ny) < min_d for nx, ny in nodes):
                continue
            nodes.append((x, y))
        max_len = min(W, H) * 0.30
        edges, seen = [], set()
        for i, (x, y) in enumerate(nodes):
            near = sorted(
                (math.hypot(x - ox, y - oy), j)
                for j, (ox, oy) in enumerate(nodes) if j != i)
            for d, j in near[:2]:
                if d > max_len:
                    continue
                key = (min(i, j), max(i, j))
                if key not in seen:
                    seen.add(key)
                    edges.append((key[0], key[1], d))
        lines = [QLineF(nodes[i][0], nodes[i][1], nodes[j][0], nodes[j][1])
                 for i, j, _d in edges]
        return nodes, edges, lines

    def _step(self, dt: float) -> None:
        """Advance pulses. Sparse spawning and a hard cap keep the
        per-frame cost at a few short lines."""
        field = self._cache
        if not field:
            return
        edges = field[1]
        if not edges:
            return
        step = min(0.10, max(0.0, dt))
        alive = []
        for pulse in self._pulses:
            pulse[1] += pulse[2] * step
            if pulse[1] < 1.0:
                alive.append(pulse)
        self._pulses = alive
        cap = 3 if self._muted else 7
        if len(self._pulses) < cap and random.random() < 0.14:
            self._pulses.append([random.randrange(len(edges)), 0.0,
                                 random.uniform(0.45, 1.05)])

    # -- paint ------------------------------------------------------
    def paintEvent(self, _event):
        W, H = self.width(), self.height()
        if W < 40 or H < 40:
            return
        cx, cy = W / 2.0, H / 2.0
        r_out = min(W, H) * 0.34
        key = (W, H)
        if self._cache is None or self._key != key:
            self._cache = self._make(int(W), int(H), cx, cy, r_out)
            self._key = key
            n_edges = len(self._cache[1])
            self._pulses = [pl for pl in self._pulses if pl[0] < n_edges]
        nodes, edges, lines = self._cache
        if not lines:
            return
        main, acc = self._colours()
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        p.setPen(QPen(_blend(main, 0.10), 1))
        p.drawLines(lines)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_blend(main, 0.30)))
        for (x, y) in nodes:
            p.drawEllipse(QRectF(x - 1.5, y - 1.5, 3.0, 3.0))

        for eidx, t, _spd in self._pulses:
            if eidx >= len(edges):
                continue
            i, j, _d = edges[eidx]
            ax, ay = nodes[i]
            bx, by = nodes[j]
            hx, hy = ax + (bx - ax) * t, ay + (by - ay) * t
            for k in range(5):
                t0 = t - k * 0.06
                t1 = t - (k + 1) * 0.06
                if t0 <= 0.0:
                    break
                p.setPen(QPen(_blend(acc, 0.42 - k * 0.08), 1.6))
                p.drawLine(QLineF(ax + (bx - ax) * max(t1, 0.0),
                                  ay + (by - ay) * max(t1, 0.0),
                                  ax + (bx - ax) * t0,
                                  ay + (by - ay) * t0))
            p.setBrush(QBrush(_blend(acc, 0.90)))
            p.drawEllipse(QRectF(hx - 2.1, hy - 2.1, 4.2, 4.2))
        p.end()
