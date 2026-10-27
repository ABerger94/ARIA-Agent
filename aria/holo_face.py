"""HoloFace: the Mark-LVI holographic head as a Command Center widget.

Wraps aria/holo_avatar.py's HoloAvatar (adapted from Mark-LVI-ARIA's
core/avatar.py, CC BY-NC 4.0 — see LICENSES/NOTICE.md) behind the same
advance(state, level, muted) shape the ResonanceOrb already speaks,
so the dashboard can swap centerpieces without the render loop caring
which one is on screen. Colours are arguments to the renderer by its
own design; the primary follows the Port 5 accent, the accent colour
stays signal orange.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from aria.holo_avatar import HoloAvatar

_BG = '#080d14'
_PRIMARY_DEFAULT = '#00d4ff'
_ACCENT = '#ff6b00'


class HoloFace(QWidget):
    """Animated holographic head, drop-in alternate centerpiece."""

    PRIMARY = _PRIMARY_DEFAULT

    def __init__(self, parent=None):
        super().__init__(parent)
        self._avatar = HoloAvatar()
        self._last = time.monotonic()
        self.setMinimumSize(280, 280)

    def advance(self, state, level, muted=False):
        """One animation tick — same contract as ResonanceOrb.advance."""
        now = time.monotonic()
        dt = now - self._last
        self._last = now
        self._avatar.step(dt, float(level or 0.0),
                          speaking=(state == 'speaking'),
                          muted=bool(muted), state=str(state))
        self.update()

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        self._avatar.glance(dx, dy, hold)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(QRectF(0, 0, w, h), QColor(_BG))
        r = min(w, h) * 0.36
        self._avatar.paint(p, w / 2, h / 2 + r * 0.12, r,
                           QColor(self.PRIMARY), QColor(_ACCENT),
                           QColor(_BG))
        p.end()


def apply_accent(accent_hex) -> bool:
    """Hue-shift the face's primary to a new accent (Port 5). The
    renderer takes colours per paint, so this is one class attribute.
    None restores the default. False = invalid accent, untouched."""
    if accent_hex is None:
        HoloFace.PRIMARY = _PRIMARY_DEFAULT
        return True
    from aria.hue_wheel import shift_palette
    shifted = shift_palette({'PRI': _PRIMARY_DEFAULT},
                            _PRIMARY_DEFAULT, accent_hex)
    if shifted is None:
        return False
    HoloFace.PRIMARY = shifted['PRI']
    return True
