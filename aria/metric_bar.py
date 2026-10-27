"""MetricBar + StatusPlaque adapted from Mark-LVI-ARIA's ui.py.

Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
Original MARK LVI: Copyright FatihMakes; ARIA fork modifications: Alek Berger.
Adapted widgets retain CC BY-NC 4.0 terms. See LICENSES/Mark-LVI.txt.
Changes: isolated widgets, ARIA-AGENT Command Center palette/fonts, and
the plaque (a two-line QLabel pattern in the source) becomes a small
widget with the source's 16-character value cap built in. Data comes
from aria/frame.py's strip_data() via aria/dashboard.py — CPU, memory
and network only, because those are the metrics ARIA-AGENT's telemetry
actually produces; the source's GPU/temperature bars have no honest
data source here and are not faked.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


class C:
    PANEL2 = '#0d1520'
    BORDER_A = '#1b3042'
    PRI = '#54d9e8'
    ACC = '#ff6b00'
    RED = '#ff3355'
    GREEN = '#00ff88'
    TEXT = '#dce6ef'
    TEXT_DIM = '#5ab8cc'
    BAR_BG = '#011520'
    FONT = 'Consolas'


def qcol(value):
    return QColor(value)


class MetricBar(QWidget):
    """One painted metric: label left, value right, threshold-coloured
    bar under both. Colour is a warning, not decoration: over 65% the
    bar turns orange, over 85% red, whatever the metric's own colour."""

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0-100
        self._text = '--'
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, float(pct)))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text = text
        self.update()

    def _bar_color(self) -> str:
        if self._text == '--':
            return self._color
        if self._value > 85:
            return C.RED
        if self._value > 65:
            return C.ACC
        return self._color

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h = 4
        bar_y = H - bar_h - 5
        bar_w = W - 12
        bar_x = 6
        fill_w = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        bar_col = qcol(self._bar_color())
        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont(C.FONT, 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._label)

        p.setFont(QFont(C.FONT, 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != '--' else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self._text)

        p.end()


class StatusPlaque(QWidget):
    """A boxed two-line status readout: small title, bold value.

    The value is capped at 16 characters — a plaque is a glance, not a
    paragraph; longer values belong in a panel. Colour lives in the
    stylesheet, so a state-driven colour change is a stylesheet rewrite,
    cheap on the dashboard's refresh tick.
    """

    MAX_VALUE = 16

    def __init__(self, title: str, color: str = C.TEXT, parent=None):
        super().__init__(parent)
        self._title = title
        self._value = '—'
        self._color = color
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(0)
        self._label = QLabel()
        self._label.setFont(QFont(C.FONT, 9))
        lay.addWidget(self._label)
        self._apply()

    def set_value(self, value: str, color: str = None):
        v = str(value)[:self.MAX_VALUE]
        c = color or self._color
        if v == self._value and c == self._color:
            return
        self._value = v
        self._color = c
        self._apply()

    def _apply(self):
        self._label.setText(f'{self._title}\n{self._value}')
        self.setStyleSheet(
            f'background: {C.PANEL2}; border: 1px solid {C.BORDER_A}; '
            f'border-radius: 3px;')
        self._label.setStyleSheet(
            f'color: {self._color}; background: transparent; border: none;')


# Accent-linked palette (Port 5, Customize): the cyan family only —
# ACC/RED/GREEN thresholds are signal colours and never move.
_ACCENT_DEFAULTS = {'PRI': '#54d9e8', 'BORDER_A': '#1b3042',
                    'TEXT_DIM': '#5ab8cc'}


def apply_accent(accent_hex) -> bool:
    """Hue-shift the bars'/plaques' accent-linked colours to a new
    accent (aria/hue_wheel.shift_palette). None restores the defaults.
    Returns False for an invalid accent, palette untouched."""
    if accent_hex is None:
        for key, value in _ACCENT_DEFAULTS.items():
            setattr(C, key, value)
        return True
    from aria.hue_wheel import shift_palette
    shifted = shift_palette(dict(_ACCENT_DEFAULTS),
                            _ACCENT_DEFAULTS['PRI'], accent_hex)
    if shifted is None:
        return False
    for key, value in shifted.items():
        setattr(C, key, value)
    return True
