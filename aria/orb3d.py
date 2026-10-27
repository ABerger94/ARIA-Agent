"""The 3D waveform orb (Alek, 2026-10-27) — a NEW centerpiece.

His ask: the waveform orb as a ball he can spin "like the
memory map". The flat ResonanceOrb stays exactly as it was;
this is the third face beside it (and the Character): the
same voice-driven waveform, wrapped on a sphere.

The ribbon is her level HISTORY, like the flat orb's: 160
samples around the equator, each pushed in or out by its
history sample's deviation from the window mean, so bursts
bulge the ball and pauses pull it in; at silence an idle
ripple keeps it breathing. Around it: an equalizer spike
ring standing in the equatorial plane (spike height from
the same 72-bin history split), two faint latitude rings
and a longitude ring to sell the sphere, three comet heads
riding the ribbon, and a frontal core + name that stay put
while the ball turns around them — a gyroscope read.

Colours and state language are the flat orb's, read live
from aria.resonance's palette so the Customize accent
moves both orbs together. Drag rotates (yaw + pitch,
pitch clamped like the map); untouched, it drifts slowly —
her face is alive in a way the map is not.
"""
from __future__ import annotations

import math
import time
from collections import deque

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QSizePolicy, QWidget

from aria import resonance as _res

_TAU = math.tau
_RIBBON_N = 160
_SPIKES = 72


class Orb3D(QWidget):
    """Same driving contract as ResonanceOrb:
    advance(state, level, muted) per render tick."""

    def __init__(self, parent=None, *, transparent_background=False):
        super().__init__(parent)
        self._transparent_background = bool(transparent_background)
        if self._transparent_background:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,
                              True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground,
                              True)
            self.setAutoFillBackground(False)
            self.setStyleSheet('background: transparent;')
        self.setMinimumSize(280, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._assistant_name = 'A.R.I.A.'
        self.state = 'IDLE'
        self.speaking = False
        self.muted = False
        self._amp_disp = 0.0
        self._amp_hist = deque(maxlen=288)
        self._core_phase = 0.0
        self._last_tick = time.monotonic()
        self._yaw = 0.6
        self._pitch = -0.38
        self._drag_pos = None
        self._drag_moved = False

    # -- driving (ResonanceOrb's smoothing, verbatim values) --
    def advance(self, state='idle', level=0.0, muted=False, viseme=None):
        self.state = {'working': 'PROCESSING',
                      'coding': 'PROCESSING'}.get(state, state.upper())
        self.speaking = state == 'speaking'
        self.muted = muted
        dt = min(0.1, max(0.0, time.monotonic() - self._last_tick))
        self._last_tick = time.monotonic()
        self._core_phase += dt
        if self._drag_pos is None:
            self._yaw += dt * 0.10          # the slow idle drift
        target = max(0.0, min(1.0, float(level))) if not muted else 0.0
        self._amp_disp += (target - self._amp_disp) * 0.45
        self._amp_hist.append(self._amp_disp)
        self.update()

    def _core_colours(self):
        C = _res.C
        if self.muted:
            return _res.qcol(C.MUTED_C), _res.qcol(C.MUTED_C)
        if self.speaking:
            return _res.qcol(C.PRI), _res.qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return _res.qcol(C.PRI), _res.qcol(C.ACC2)
        if self.state == "LISTENING":
            return _res.qcol(C.PRI), _res.qcol(C.GREEN)
        return _res.qcol(C.PRI), _res.qcol(C.PRI_DIM)

    # -- rotation ------------------------------------------------
    def rotate_by(self, dx_px: float, dy_px: float):
        """Drag rotation, the memory map's numbers: horizontal
        spins the ball, vertical tilts it (clamped)."""
        self._yaw += float(dx_px) * 0.008
        self._pitch = max(-1.35, min(1.35,
                                     self._pitch + float(dy_px) * 0.008))
        self.update()

    def _rot(self) -> np.ndarray:
        cy, sy = math.cos(self._yaw), math.sin(self._yaw)
        cp, sp = math.cos(self._pitch), math.sin(self._pitch)
        ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
        rx = np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]])
        return rx @ ry

    def mousePressEvent(self, event):
        self._drag_pos = event.position()
        self._drag_moved = False
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_pos is None:
            return
        pos = event.position()
        dx = pos.x() - self._drag_pos.x()
        dy = pos.y() - self._drag_pos.y()
        if abs(dx) + abs(dy) > 3:
            self._drag_moved = True
        self._drag_pos = pos
        if self._drag_moved:
            self.rotate_by(dx, dy)

    def mouseReleaseEvent(self, _event):
        self._drag_pos = None
        self._drag_moved = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # -- paint -----------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if not self._transparent_background:
                p.fillRect(self.rect(), QColor(_res.C.BG))
            self._paint_ball(p, self.width() / 2, self.height() / 2,
                             min(self.width(), self.height()) * 0.41)
        finally:
            p.end()

    def _paint_ball(self, p: QPainter, cx: float, cy: float, R: float):
        main, acc = self._core_colours()
        bg = QColor(_res.C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted
        rot = self._rot()

        def blend(col: QColor, a: float) -> QColor:
            k = max(0.0, min(1.0, a))
            if self._transparent_background:
                out = QColor(col)
                out.setAlphaF(k)
                return out
            return QColor(int(bg.red() + (col.red() - bg.red()) * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue() + (col.blue() - bg.blue()) * k))

        def project(pts3: np.ndarray) -> np.ndarray:
            """(n,3) orb-local -> (n,3) screen: x, y, depth 0..1."""
            v = pts3 @ rot.T
            out = np.empty_like(v)
            out[:, 0] = cx + v[:, 0] * R
            out[:, 1] = cy - v[:, 1] * R
            out[:, 2] = (v[:, 2] + 1.0) / 2.0
            return out

        # Atmosphere (rotation-invariant, under everything):
        # a wide halo, then the ball's own body — a soft sphere
        # silhouette the wires and ribbons live on (without it
        # the first render's elements floated past a dark disc
        # smaller than the ball itself).
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        gr = R * 1.62
        g = QRadialGradient(cx, cy, gr)
        g.setColorAt(0.00, blend(main, min(0.95, 0.30 * lift)))
        g.setColorAt(0.45, blend(main, min(0.95, 0.30 * lift * 0.52)))
        g.setColorAt(0.78, blend(main, min(0.95, 0.30 * lift * 0.18)))
        g.setColorAt(1.00, blend(main, 0.0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        body = R * 1.14
        g = QRadialGradient(cx - body * 0.25, cy - body * 0.30, body * 1.35)
        g.setColorAt(0.0, blend(main, 0.20 * lift))
        g.setColorAt(0.55, blend(main, 0.10 * lift))
        g.setColorAt(0.85, blend(main, 0.05))
        g.setColorAt(1.0, blend(main, 0.0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(cx - body, cy - body, body * 2, body * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(blend(main, 0.10), 1))
        p.drawEllipse(QRectF(cx - R, cy - R, R * 2, R * 2))

        # Sphere wire: latitude rings + one longitude ring, each
        # segment shaded by its depth — the ball's roundness.
        def ring(pts3: np.ndarray, col: QColor, alpha: float, wid: float):
            scr = project(pts3)
            for i in range(len(scr) - 1):
                d = (scr[i, 2] + scr[i + 1, 2]) / 2.0
                pen = QPen(blend(col, alpha * (0.25 + 0.75 * d)), wid)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(scr[i, 0], scr[i, 1]),
                           QPointF(scr[i + 1, 0], scr[i + 1, 1]))

        seg = 72
        th = np.linspace(0, _TAU, seg + 1)
        for lat_deg, al in ((-52.0, 0.16), (52.0, 0.16)):
            la = math.radians(lat_deg)
            ring(np.stack([np.cos(la) * np.cos(th),
                           np.full_like(th, math.sin(la)),
                           np.cos(la) * np.sin(th)], axis=1),
                 main, al, 1.0)
        ring(np.stack([np.zeros_like(th), np.cos(th), np.sin(th)], axis=1),
             main, 0.13, 1.0)

        # Level history -> ribbon offsets (the flat orb's math).
        hist = list(self._amp_hist)
        nh = len(hist)
        mean = (sum(hist) / nh) if nh else 0.0

        def _level(frac: float) -> float:
            if nh == 0:
                return 0.0
            pos = (frac % 1.0) * nh
            i0 = int(pos) % nh
            i1 = (i0 + 1) % nh
            fr = pos - int(pos)
            return hist[i0] * (1.0 - fr) + hist[i1] * fr

        def _idle(frac: float) -> float:
            return (0.014 * math.sin(t * 1.2 + frac * _TAU * 3)
                    + 0.009 * math.sin(t * 2.3 + frac * _TAU * 7 + 1.7))

        gain = 0.30 if live else 0.13
        idle_w = 0.35 if amp > 0.05 else 1.0
        drift = t * 0.02

        def _off(frac: float) -> float:
            return ((_level(frac + drift) - mean) * gain
                    + _idle(frac) * idle_w) * 2.2

        # EQ spike ring in the equatorial plane: base on the
        # ribbon sphere, tips pushed out by bin energy.
        base_r = 0.86
        spikes = []
        for i in range(_SPIKES):
            if nh:
                lo = int(i * nh / _SPIKES)
                hi = max(lo + 1, int((i + 1) * nh / _SPIKES))
                chunk = hist[lo:min(hi, nh)] or [0.0]
                energy = sum(chunk) / len(chunk)
            else:
                energy = 0.0
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            h = idle + 0.30 * energy
            a = i * _TAU / _SPIKES
            d = np.array([math.cos(a), 0.0, math.sin(a)])
            spikes.append((d * base_r, d * (base_r + h)))
        scr_b = project(np.stack([s[0] for s in spikes]))
        scr_t = project(np.stack([s[1] for s in spikes]))
        for i in range(_SPIKES):
            d = (scr_b[i, 2] + scr_t[i, 2]) / 2.0
            pen = QPen(blend(acc if live else main,
                             (0.28 + 0.60 * amp) * (0.35 + 0.65 * d)), 1.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(scr_b[i, 0], scr_b[i, 1]),
                       QPointF(scr_t[i, 0], scr_t[i, 1]))

        # The waveform ribbons: equator (the voice) + a quieter
        # northern echo. Drawn as segments in three passes —
        # dim wide echo, mid, bright — each depth-shaded.
        alpha_scale = 0.4 + 0.6 * min(1.0, amp * 2.0)

        def ribbon(lat: float, r_base: float, amp_scale: float):
            fr = np.linspace(0, 1, _RIBBON_N + 1)
            offs = np.array([_off(float(f)) for f in fr]) * amp_scale
            rr = r_base + offs
            cl = math.cos(lat)
            pts3 = np.stack([rr * cl * np.cos(fr * _TAU),
                             np.full_like(fr, math.sin(lat) * r_base),
                             rr * cl * np.sin(fr * _TAU)], axis=1)
            scr = project(pts3)
            for col, al, wid in ((main, 0.22, 5.0), (main, 0.50, 2.6),
                                 (acc, 0.95, 1.8)):
                for i in range(_RIBBON_N):
                    d = (scr[i, 2] + scr[i + 1, 2]) / 2.0
                    pen = QPen(blend(col, al * alpha_scale
                                     * (0.30 + 0.70 * d)), wid)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    p.drawLine(QPointF(scr[i, 0], scr[i, 1]),
                               QPointF(scr[i + 1, 0], scr[i + 1, 1]))
            return scr

        ribbon(0.0, base_r, 1.0)
        ribbon(math.radians(34.0), base_r * 0.94, 0.55)

        # Comet heads riding the equator ribbon at fixed
        # fractions (they orbit; they never bob — the flat
        # orb's rule, kept).
        for frac in (0.08, 0.44, 0.75):
            a = (frac + t * 0.05) % 1.0 * _TAU
            rr = base_r + _off((frac + t * 0.05) % 1.0)
            pt = project(np.array([[rr * math.cos(a), 0.0,
                                    rr * math.sin(a)]]))[0]
            d = pt[2]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(blend(acc, 0.16 * (0.3 + 0.7 * d)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 8.0, 8.0)
            p.setBrush(blend(acc, 0.95 * (0.3 + 0.7 * d)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 3.4, 3.4)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # The frontal core assembly: gradient sphere + ring +
        # name. It does not rotate — the ball turns around it.
        core_r = R * 0.34 * (1.0 + 0.10 * amp)
        g = QRadialGradient(cx - core_r * 0.35, cy - core_r * 0.35,
                            core_r * 1.5)
        g.setColorAt(0.0, blend(_res.qcol(_res.C.WHITE), 0.95))
        g.setColorAt(0.35, blend(main, 0.85))
        g.setColorAt(1.0, blend(main, 0.05))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(cx - core_r, cy - core_r,
                             core_r * 2, core_r * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        inner = R * 0.46
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))
        name = self._assistant_name or ""
        if name:
            fsz = max(8, int(R * 0.13))
            f = QFont("Courier New", fsz, QFont.Weight.Bold)
            p.setFont(f)
            p.setPen(QPen(blend(_res.qcol(_res.C.WHITE),
                                0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - R, cy + inner + R * 0.04,
                              R * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)


def apply_accent(accent_hex) -> bool:
    """The 3D orb shares the flat orb's palette (read live
    from aria.resonance), so an accent change is resonance's
    own — forwarded, never forked."""
    return _res.apply_accent(accent_hex)
