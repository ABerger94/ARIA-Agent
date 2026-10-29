"""Media visualizer centerpiece widget (Alek, 2026-10-29).

Equalizer bars driven by aria/media_audio.py's live playback
spectrum, with the track title/artist under them. A plain QWidget
in the dashboard's center stack, the same slot the memory map
uses: the dashboard decides when it shows (aria/viz_policy.py);
this widget only draws. Showing it starts the audio feed, hiding
it stops the feed, so capture costs nothing while it's away.
"""
from __future__ import annotations

try:
    from PyQt6.QtCore import Qt, QRectF, QTimer
    from PyQt6.QtGui import QColor, QFont, QPainter
    from PyQt6.QtWidgets import QWidget
    _HAVE_QT = True
except Exception:  # pragma: no cover - Qt is a hard dep of the UI
    _HAVE_QT = False

from aria import media_audio

_DEFAULT_ACCENT = '#2ee6a8'
_MIN_BAR = 0.02


class BarAnimator:
    """Per-bar smoothing: fast attack, slow decay (pure, testable)."""

    def __init__(self, count, attack=0.55, decay=0.80):
        self.values = [0.0] * count
        self._attack = attack
        self._decay = decay

    def step(self, targets):
        out = []
        for i, now in enumerate(self.values):
            t = float(targets[i]) if i < len(targets) else 0.0
            if t > now:
                now = now + (t - now) * self._attack
            else:
                now = now * self._decay + t * (1.0 - self._decay) * 0.35
            out.append(min(1.0, max(0.0, now)))
        self.values = out
        return out


if _HAVE_QT:

    class MediaVisualizer(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setStyleSheet('background: transparent;')
            self._anim = BarAnimator(media_audio.BANDS)
            self._accent = QColor(_DEFAULT_ACCENT)
            self._title = ''
            self._artist = ''
            self._timer = QTimer(self)
            self._timer.setInterval(33)
            self._timer.timeout.connect(self._tick)

        # -- dashboard-facing API --------------------------------
        def set_accent(self, hex_color):
            try:
                color = QColor(hex_color)
                if color.isValid():
                    self._accent = color
            except Exception:
                pass

        def set_track(self, title, artist=''):
            self._title = str(title or '')
            self._artist = str(artist or '')

        # -- lifecycle --------------------------------------------
        def showEvent(self, event):
            super().showEvent(event)
            try:
                media_audio.start()
            except Exception:
                pass
            self._timer.start()

        def hideEvent(self, event):
            self._timer.stop()
            try:
                media_audio.stop()
            except Exception:
                pass
            super().hideEvent(event)

        def _tick(self):
            self._anim.step(media_audio.current())
            self.update()

        # -- drawing ----------------------------------------------
        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            w = float(self.width())
            h = float(self.height())
            if w < 40 or h < 40:
                painter.end()
                return
            bars = self._anim.values
            n = len(bars)
            gap = max(3.0, w * 0.006)
            bar_w = (w * 0.86 - gap * (n - 1)) / n
            x0 = (w - (bar_w * n + gap * (n - 1))) / 2.0
            baseline = h * 0.70
            max_h = h * 0.52
            for i, v in enumerate(bars):
                bh = max(2.0, (_MIN_BAR + v * (1.0 - _MIN_BAR)) * max_h)
                x = x0 + i * (bar_w + gap)
                color = QColor(self._accent)
                color.setAlpha(150 + int(105 * v))
                painter.setBrush(color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(
                    QRectF(x, baseline - bh, bar_w, bh),
                    min(4.0, bar_w / 2.0), min(4.0, bar_w / 2.0))
                # Faint reflection under the baseline.
                faint = QColor(self._accent)
                faint.setAlpha(36)
                painter.setBrush(faint)
                painter.drawRoundedRect(
                    QRectF(x, baseline + 4.0, bar_w, bh * 0.28),
                    min(4.0, bar_w / 2.0), min(4.0, bar_w / 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            line = QColor(self._accent)
            line.setAlpha(70)
            painter.setPen(line)
            painter.drawLine(int(x0), int(baseline + 1),
                             int(x0 + bar_w * n + gap * (n - 1)),
                             int(baseline + 1))
            # Track text under the bars.
            painter.setPen(QColor('#dfe9f2'))
            title_font = QFont()
            title_font.setPointSize(13)
            title_font.setBold(True)
            painter.setFont(title_font)
            painter.drawText(
                QRectF(0, baseline + h * 0.10, w, 26),
                Qt.AlignmentFlag.AlignHCenter, self._title)
            if self._artist:
                painter.setPen(QColor('#8fa3b5'))
                artist_font = QFont()
                artist_font.setPointSize(10)
                painter.setFont(artist_font)
                painter.drawText(
                    QRectF(0, baseline + h * 0.10 + 26, w, 20),
                    Qt.AlignmentFlag.AlignHCenter, self._artist)
            painter.end()

else:  # pragma: no cover
    MediaVisualizer = None
