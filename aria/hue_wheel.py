"""HueWheel + palette hue-shift adapted from Mark-LVI-ARIA's ui.py.

Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
Original MARK LVI: Copyright FatihMakes; ARIA fork modifications: Alek Berger.
Adapted code retains CC BY-NC 4.0 terms. See LICENSES/Mark-LVI.txt.
Changes: the source's apply_ui_accent() mutates one global palette
class; here the same math is a pure function (shift_palette) so each
ARIA-AGENT surface — dashboard stylesheet, ResonanceOrb, MetricBar —
shifts its OWN palette defaults by the same hue delta, and the result
is testable without a window. The wheel is the source's, with the
Command Center's border colour.
"""
from __future__ import annotations

import colorsys
import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QConicalGradient, QPainter, QPen
from PyQt6.QtWidgets import QWidget

DEFAULT_ACCENT = '#00d4ff'
_WHEEL_BORDER = '#54d9e8'


def _hsv(hex_str: str):
    r = int(hex_str[1:3], 16) / 255
    g = int(hex_str[3:5], 16) / 255
    b = int(hex_str[5:7], 16) / 255
    return colorsys.rgb_to_hsv(r, g, b)


def _valid_accent(accent_hex) -> bool:
    if not isinstance(accent_hex, str):
        return False
    a = accent_hex.strip().lower()
    if not (a.startswith('#') and len(a) == 7):
        return False
    try:
        int(a[1:], 16)
    except ValueError:
        return False
    return True


def shift_palette(defaults: dict, base_hex: str, accent_hex: str):
    """Hue-shift every colour in `defaults` by the hue delta between
    `base_hex` and `accent_hex`, preserving each colour's saturation
    and value ratios — the design stays intact, only the family hue
    moves. A near-grey accent desaturates the whole palette instead
    (the source's rule). Returns a new dict, or None for an invalid
    accent. Keys are preserved exactly."""
    if not _valid_accent(accent_hex) or not _valid_accent(base_hex):
        return None
    accent_hex = accent_hex.strip().lower()
    base_h = _hsv(base_hex.strip().lower())[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh = acc_h - base_h
    grey = acc_s < 0.08
    shifted = {}
    for key, hex0 in defaults.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        shifted[key] = '#{:02x}{:02x}{:02x}'.format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5))
    return shifted


class HueWheel(QWidget):
    """Circular colour picker. The user drags the handle (small white
    circle) around the wheel to choose from ALL hues. The filled circle
    in the centre is a live preview of the selected colour."""

    hue_picked = pyqtSignal(str)     # while dragging (live)
    hue_committed = pyqtSignal(str)  # when the handle is released

    _RING = 16  # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_ACCENT, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue = 0.53
        self._drag = False
        self.set_color(initial_hex)

    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or '').strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-pi, pi], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0,
                            QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(QColor(_WHEEL_BORDER), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        r = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx = center.x() + r * math.cos(ang)
        hy = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor('#00060a'), 2))
        p.setBrush(QBrush(QColor('#ffffff')))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    def mousePressEvent(self, e):
        self._drag = True
        self._hue = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())
