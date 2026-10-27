"""Memory constellation widget (Alek, 2026-10-27).

The star ball: every fact she has stored, placed on a sphere
by meaning (aria/memory_map.py does the layout from her
embedding vectors), drawn as stars — category colours, newest
brightest, faint lines between near-identical facts.

Interaction, his words: "rotate the map around when I click
on it like a ball" — drag to spin it (yaw + pitch, pitch
clamped so the ball never flips inside out); a plain click
(no drag) selects the nearest front star and the parent
hears fact_selected(key | None) for its detail row. Reading
a fact is HOVER (Alek, 2026-10-27): the full text pops up
in a little window by the star — the old bottom caption
couldn't fit a whole memory and is gone. Still when
untouched: no ambient spin.

The same widget class serves both homes he picked: the
Memory tab's Map view and the dashboard centerpiece.
"""
from __future__ import annotations

import hashlib

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QWidget

from aria import memory_map


def _hash01(text: str) -> float:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


def category_color(category: str) -> QColor:
    """One stable colour per category, hashed — never assigned,
    so a new category needs no palette edit."""
    return QColor.fromHsvF(_hash01("cat:" + str(category)), 0.55, 1.0)


class ConstellationWidget(QWidget):
    fact_selected = pyqtSignal(object)   # the fact's key, or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setMouseTracking(True)
        self._rows: list = []
        self._pts = np.zeros((0, 3))
        self._edges: list = []
        self._yaw = 0.6
        self._pitch = -0.35
        self._sel = -1
        self._hover = -1
        self._drag_pos = None
        self._drag_moved = False
        self._accent = QColor("#54d9e8")
        # The hover popup: the FULL fact text by its star,
        # word-wrapped, mouse-transparent so it never steals
        # the hover it is answering.
        self._popup = QLabel(self)
        self._popup.setWordWrap(True)
        self._popup.setMaximumWidth(340)
        self._popup.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._popup.setStyleSheet(
            "background: rgba(8, 13, 20, 238); color: #eaf6ff;"
            "border: 1px solid #54d9e8; border-radius: 6px;"
            "padding: 8px;")
        self._popup.hide()

    # -- data -------------------------------------------------
    def set_facts(self, rows):
        """Replace the ball's facts, keeping the current spin
        and (by key) the current selection."""
        self._hover = -1
        self._popup.hide()
        sel_key = self.selected_key()
        self._rows = list(rows)
        self._pts, self._edges = memory_map.layout_points(self._rows)
        self._sel = -1
        if sel_key is not None:
            for i, r in enumerate(self._rows):
                if r.get("key") == sel_key:
                    self._sel = i
                    break
        self.update()

    def selected_key(self):
        if 0 <= self._sel < len(self._rows):
            return self._rows[self._sel].get("key")
        return None

    def set_accent(self, hex_str):
        col = QColor(str(hex_str or ""))
        if col.isValid():
            self._accent = col
            self.update()

    # -- geometry ---------------------------------------------
    def _view_points(self) -> np.ndarray:
        """The ball's points in view space: x right, y up,
        z toward the viewer (front stars have z near +1)."""
        if not len(self._pts):
            return self._pts
        cy, sy = np.cos(self._yaw), np.sin(self._yaw)
        cp, sp = np.cos(self._pitch), np.sin(self._pitch)
        ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
        rx = np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]])
        return self._pts @ (rx @ ry).T

    def _screen_xy(self, view: np.ndarray):
        w, h = max(self.width(), 1), max(self.height(), 1)
        r = min(w, h) * 0.40
        return (w / 2 + view[:, 0] * r, h / 2 - view[:, 1] * r, r)

    def rotate_by(self, dx_px: float, dy_px: float):
        """Drag rotation: horizontal spins the ball around its
        vertical axis, vertical tilts it (clamped, so the
        poles never swap)."""
        self._yaw += float(dx_px) * 0.008
        self._pitch = max(-1.35, min(1.35,
                                     self._pitch + float(dy_px) * 0.008))
        self.update()

    def _hit(self, x: float, y: float, max_d: float = 14.0) -> int:
        """Index of the front star nearest (x, y) within
        max_d px, or -1. Back-of-the-ball stars never hit:
        you can't point at what you can't see."""
        if not len(self._pts):
            return -1
        view = self._view_points()
        sx, sy, _r = self._screen_xy(view)
        best, best_d = -1, max_d
        for i in range(len(view)):
            if view[i, 2] < -0.05:
                continue
            d = float(np.hypot(sx[i] - x, sy[i] - y))
            if d < best_d:
                best, best_d = i, d
        return best

    def pick(self, x: float, y: float):
        """Select the front star nearest a click; returns its
        key (or None when the click lands on empty space,
        which clears the selection)."""
        self._sel = self._hit(x, y)
        key = self.selected_key() if self._sel >= 0 else None
        if not len(self._pts):
            self._sel = -1
            key = None
        self.fact_selected.emit(key)
        self.update()
        return key

    def hover_at(self, x: float, y: float):
        """Hover reading (Alek, 2026-10-27): over a star, the
        little popup shows that fact's FULL text beside it;
        over empty space, it goes away. Returns the hovered
        index or -1."""
        idx = self._hit(x, y, 16.0)
        if idx < 0:
            self._hover = -1
            self._popup.hide()
            return -1
        self._hover = idx
        row = self._rows[idx]
        self._popup.setText(
            f"{row.get('key', '')} — {row.get('value', '')}\n"
            f"{row.get('category', '')} · {row.get('updated', '')}")
        self._popup.adjustSize()
        view = self._view_points()
        sx, sy, _r = self._screen_xy(view)
        px = int(sx[idx]) + 16
        py = int(sy[idx]) + 16
        if px + self._popup.width() > self.width():
            px = int(sx[idx]) - self._popup.width() - 16
        if py + self._popup.height() > self.height():
            py = int(sy[idx]) - self._popup.height() - 16
        self._popup.move(max(2, px), max(2, py))
        self._popup.show()
        self._popup.raise_()
        return idx

    # -- mouse --------------------------------------------------
    def mousePressEvent(self, event):
        self._popup.hide()
        self._drag_pos = event.position()
        self._drag_moved = False
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._drag_pos is None:
            self.hover_at(pos.x(), pos.y())
            return
        dx = pos.x() - self._drag_pos.x()
        dy = pos.y() - self._drag_pos.y()
        if abs(dx) + abs(dy) > 3:
            self._drag_moved = True
        self._drag_pos = pos
        if self._drag_moved:
            self._popup.hide()
            self.rotate_by(dx, dy)

    def leaveEvent(self, _event):
        self._hover = -1
        self._popup.hide()

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        was_drag = self._drag_moved
        self._drag_pos = None
        self._drag_moved = False
        if not was_drag:
            pos = event.position()
            self.pick(pos.x(), pos.y())

    # -- paint --------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        if not len(self._pts):
            p.setPen(QColor("#829aaf"))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter,
                       "No memories to map yet.")
            p.end()
            return
        view = self._view_points()
        sx, sy, r = self._screen_xy(view)
        n = len(view)
        # Edges first, under the stars; back-facing pairs fade out.
        pen = QPen(QColor(90, 130, 165, 70))
        pen.setWidthF(1.0)
        p.setPen(pen)
        for i, j in self._edges:
            if view[i, 2] > -0.3 and view[j, 2] > -0.3:
                p.drawLine(int(sx[i]), int(sy[i]), int(sx[j]), int(sy[j]))
        # Stars, far to near so the front paints last.
        p.setPen(Qt.PenStyle.NoPen)
        for order in np.argsort(view[:, 2]):
            i = int(order)
            depth = (float(view[i, 2]) + 1.0) / 2.0        # 0 back, 1 front
            recency = 1.0 - (i / max(n, 1)) * 0.45         # rows are newest-first
            col = category_color(self._rows[i].get("category", ""))
            col.setAlphaF(max(0.12, (0.25 + 0.75 * depth) * recency))
            p.setBrush(col)
            rad = 1.6 + 3.2 * depth
            p.drawEllipse(int(sx[i] - rad), int(sy[i] - rad),
                          int(rad * 2), int(rad * 2))
            if i == self._sel:
                ring = QPen(self._accent)
                ring.setWidthF(1.6)
                p.setPen(ring)
                p.setBrush(Qt.BrushStyle.NoBrush)
                rr = rad + 4
                p.drawEllipse(int(sx[i] - rr), int(sy[i] - rr),
                              int(rr * 2), int(rr * 2))
                p.setPen(Qt.PenStyle.NoPen)
        p.end()
