"""Resonance waveform orb adapted from Mark-LVI-ARIA's HudCanvas.

Source: https://github.com/ABerger94/Mark-LVI-ARIA/blob/61a47b0/ui.py
Original MARK LVI: Copyright FatihMakes; ARIA fork modifications: Alek Berger.
Adapted rendering methods retain CC BY-NC 4.0 terms. See LICENSES/Mark-LVI.txt.
Changes: isolated widget, ARIA state/audio adapter, bounded history and no face engine.
"""
from __future__ import annotations

import math
import time
from collections import deque
from PyQt6.QtCore import Qt, QPointF, QRectF, QLineF
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QRadialGradient, QFont
from PyQt6.QtWidgets import QWidget, QSizePolicy


class C:
    BG = '#080d14'
    PRI = '#00d4ff'
    PRI_DIM = '#007a99'
    ACC = '#ff6b00'
    ACC2 = '#ffcc00'
    GREEN = '#00ff88'
    MUTED_C = '#ff3366'
    WHITE = '#d8f8ff'


def qcol(value):
    return QColor(value)


class ResonanceOrb(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._assistant_name = 'A.R.I.A.'
        self.state = 'IDLE'
        self.speaking = False
        self.muted = False
        self._amp_disp = 0.0
        self._amp_hist = deque(maxlen=288)
        self._wave_rings = []
        self._wave_prev_amp = 0.0
        self._core_phase = 0.0
        self._last_tick = time.monotonic()

    def advance(self, state='idle', level=0.0, muted=False):
        self.state = {'working': 'PROCESSING', 'coding': 'PROCESSING'}.get(state, state.upper())
        self.speaking = state == 'speaking'
        self.muted = muted
        dt = min(0.1, max(0.0, time.monotonic() - self._last_tick))
        self._last_tick = time.monotonic()
        self._core_phase += dt
        target = max(0.0, min(1.0, float(level))) if not muted else 0.0
        self._amp_disp += (target - self._amp_disp) * 0.45
        self._amp_hist.append(self._amp_disp)
        if self._wave_prev_amp <= 0.72 < self._amp_disp and len(self._wave_rings) < 6:
            self._wave_rings.append(self._core_phase)
        self._wave_prev_amp = self._amp_disp
        self._wave_rings = [stamp for stamp in self._wave_rings if self._core_phase - stamp < 1.6]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillRect(self.rect(), QColor(C.BG))
            self._paint_wave_core(painter, self.width() / 2, self.height() / 2,
                                  min(self.width(), self.height()) * .41,
                                  self.width(), self.height())
        finally:
            painter.end()

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _draw_comet_head(self, p: QPainter, cx: float, cy: float,
                         rad: float, phi: float, trail_dir: int,
                         col: QColor, scale: float, mix) -> None:
        """Draw one comet head on the circle of radius rad around
        (cx, cy), at angle phi in the painter's device param — the
        head sits at (cx + rad·cos phi, cy + rad·sin phi).

        Treatment, shared by every rotating element of the waveform
        core so the heads can never drift apart visually: a soft glow,
        the bright core dot (radius 3.6 at scale 1, mix 0.95 — the dot
        the lone sweep head always was), and a tapering trail of short
        chord segments streaming in the trail_dir rotational direction
        (+1 toward increasing phi, -1 toward decreasing phi). Callers
        pass trail_dir pointing AWAY from the element's direction of
        travel, so the trail always streams behind the head. mix(col,
        a) is _paint_wave_core's blend-onto-background closure, keeping
        colours pre-mixed like the rest of the painter. Cost is
        O(trail segments); nothing beyond a few points is allocated.
        """
        hx = cx + rad * math.cos(phi)
        hy = cy + rad * math.sin(phi)
        steps = 7
        span = math.radians(30.0) * (0.55 + 0.45 * scale)
        for i in range(steps):
            f0 = i / steps
            f1 = (i + 1) / steps
            a0 = phi + trail_dir * span * f0
            a1 = phi + trail_dir * span * f1
            fade = (1.0 - f0) ** 1.4
            pen = QPen(mix(col, 0.50 * fade),
                       0.4 + 2.2 * scale * fade)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(cx + rad * math.cos(a0),
                               cy + rad * math.sin(a0)),
                       QPointF(cx + rad * math.cos(a1),
                               cy + rad * math.sin(a1)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(col, 0.16))
        p.drawEllipse(QPointF(hx, hy), 8.0 * scale, 8.0 * scale)
        p.setBrush(mix(col, 0.95))
        p.drawEllipse(QPointF(hx, hy), 3.6 * scale, 3.6 * scale)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_wave_core(self, p: QPainter, cx: float, cy: float, r: float,
                         W: float = 0.0, H: float = 0.0):
        """Draw the waveform reactor ("Resonance") at (cx, cy), outer
        radius r — the third centrepiece, approved by Alek 2026-10-22
        from a rendered prototype (lvi-build/reactor_wave.py).

        Same skeleton as _paint_core — atmosphere gradients, frame marks,
        crosshair, thin outer circles, sparse graduations, sweeping arcs,
        the name in its ring — with the voice made the centrepiece instead
        of a spike ring at the edge:

        * a polar waveform RIBBON: three layered traces (wide dim echo,
          mid trace, bright voice line) plus a mirrored inner trace,
          wrapped around the name ring, scrolling and slowly rotating;
        * DUAL spike rings — outer spikes grow with each history bin's
          mean level (the low band), inner spikes with the bin's largest
          step (the high band) — counter-rotating against the ribbon;
        * EMISSION rings that burst outward when the level peaks
          (spawned in _step on an upward 0.72 crossing; here they are
          only expanded and faded);
        * comet heads — glow, bright core and a tapering trail —
          at the leading end of every sweeping arc, at the ribbon's
          angular origin, and on both spike rings at their first
          spike's angle. Every head orbits on a FIXED radius — the
          arc's own radius, the ribbon's base radius, the spike
          rings' rest tip circles — so only its angle ever moves:
          the elements bounce with the level, the heads must not
          (Alek, 2026-10-22: "I just want them moving around the
          center").

        DATA, honestly: the canvas has no PCM history, only the smoothed
        level (_amp_disp). _step therefore samples that level into
        _amp_hist every tick, and the ribbon is drawn from the LEVEL
        HISTORY — each point's radius is its history sample's deviation
        from the window mean, so bursts push the trace out and pauses
        pull it in. It is a true picture of the level's shape over the
        last ~5 seconds, not a sample-accurate oscilloscope trace. At
        silence the history is flat and a phase-driven idle ripple (the
        same terms _paint_core uses) takes over, so the ring breathes
        gently and never sits dead or jitters.

        Performance follows _paint_core's discipline: colours pre-mixed
        onto the background, batched drawLines/drawPolyline, no gradients
        beyond the two atmosphere radials, ~180 points per trace.
        """
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue()  + (col.blue()  - bg.blue())  * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. Atmosphere — the same two radial gradients as the reactor.
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0, a1 in ((r * 0.70, 0.30, 0.0), (r * 0.34, 0.34, 0.0)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, a1))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 2. Frame marks at the corners of the whole canvas.
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(main, 0.45), 1.4))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QLineF(x, y, x - sx * arm, y))
                p.drawLine(QLineF(x, y, x, y - sy * arm))

        # 3. Crosshair, broken around the core.
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        if W > 40:
            p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
            p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        if H > 40:
            p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
            p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

        # 4. Two thin outer circles + sparse graduations, as the reactor's.
        for rr, a in ((1.00, 0.34), (0.93, 0.16)):
            rad = r * rr
            p.setPen(QPen(blend(main, a), 1))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 3 == 0:
                major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
            else:
                minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.42), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.18), 1))
        p.drawLines(minor)

        # 5. Sweeping arcs, the reactor's table and state rate — plus a
        #    comet head at the leading end of each arc group's first
        #    segment (the spot the lone head always rode).
        rate = 1.0 + (1.9 if self.state in ("THINKING", "PROCESSING") else 0.0) \
                   + (1.2 if self.speaking else 0.0)
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc,  0.75, 2.0),
                (0.845, 82,  3, -1, main, 0.38, 1.3),
                (0.760, 150, 1, +1, acc,  0.45, 1.6),
                (0.660, 64,  4, -1, main, 0.26, 1.1),
                (0.545, 128, 2, +1, main, 0.30, 1.2))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for sgm in range(count):
                start = base + sgm * (360.0 / count)
                p.drawArc(box, int(start * 16), int(span * 16))
            # Leading end, in the painter's device param (the head sits
            # at (cx + rad·cos phi, cy + rad·sin phi)): a +dirn arc
            # travels toward decreasing phi and leads with its
            # (start + span) end; a -dirn arc travels toward increasing
            # phi and leads with its start end. The trail streams the
            # other way — always behind the motion.
            if dirn > 0:
                phi = -math.radians(base + span)
                tdir = +1
            else:
                phi = -math.radians(base)
                tdir = -1
            self._draw_comet_head(p, cx, cy, rad, phi, tdir, col,
                                  max(0.6, wid / 2.0), blend)

        # 6. Emission rings from voice peaks (spawned in _step).
        for stamp in self._wave_rings:
            age = (t - stamp) / 1.6
            if 0.0 <= age < 1.0:
                rad = r * (0.42 + 0.85 * age)
                p.setPen(QPen(blend(acc, 0.34 * (1.0 - age) ** 1.5), 2.0))
                p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # 7. The waveform ribbon. The history window is sampled by
        #    fractional position with wraparound interpolation; because
        #    _step appends every tick, the pattern travels around the
        #    ring on its own, and a slow phase drift turns the whole
        #    ribbon against the spike rings below.
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
            return (0.014 * math.sin(t * 1.2 + frac * 6.2832 * 3)
                    + 0.009 * math.sin(t * 2.3 + frac * 6.2832 * 7 + 1.7))

        gain = 0.24 if live else 0.10
        idle_w = 0.35 if amp > 0.05 else 1.0
        base_r = r * 0.46
        rot = t * 0.25
        drift = t * 0.02
        alpha_scale = 0.4 + 0.6 * min(1.0, amp * 2.0)
        N = 180

        def _off(frac: float) -> float:
            return (_level(frac + drift) - mean) * gain \
                + _idle(frac) * idle_w

        for col, a, wid in ((acc, 0.30, 5.0), (main, 0.45, 2.4),
                            (acc, 0.95, 1.6)):
            pts = []
            for i in range(N + 1):
                frac = i / N
                ang = frac * 6.2832 + rot
                rad = base_r + _off(frac) * r
                pts.append(QPointF(cx + rad * math.cos(ang),
                                   cy + rad * math.sin(ang)))
            p.setPen(QPen(blend(col, a * alpha_scale), wid))
            p.drawPolyline(pts)
        # Mirrored inner trace — the ribbon's lower edge.
        pts = []
        for i in range(N + 1):
            frac = i / N
            ang = frac * 6.2832 + rot
            rad = base_r - abs(_off(frac)) * r * 0.62 - r * 0.012
            pts.append(QPointF(cx + rad * math.cos(ang),
                               cy + rad * math.sin(ang)))
        p.setPen(QPen(blend(main, 0.35), 1.2))
        p.drawPolyline(pts)
        # The ribbon is a closed loop — it has no leading end — so its
        # comet head sits at the loop's angular origin (frac 0), on
        # the ribbon's FIXED base radius: the radius the trace has at
        # zero offset. Riding _off(0) instead made the head bob in
        # and out with the level, which Alek rejected — the trace
        # undulates, the head only orbits. The traces rotate toward
        # increasing angle (rot grows with t), so the trail streams
        # toward decreasing angle, behind the motion.
        self._draw_comet_head(p, cx, cy, base_r, rot,
                              -1, acc, 0.85, blend)

        # 8. Dual spike rings over the same 72-bin split of the history:
        #    outer from bin MEAN level, inner from bin max STEP, both
        #    standing on the reactor's idle shimmer so silence still
        #    moves, counter-rotating against the ribbon.
        nb = 72
        ring_off = -rot * 0.6
        out_lines, in_lines = [], []
        for i in range(nb):
            if nh:
                lo = int(i * nh / nb)
                hi = max(lo + 1, int((i + 1) * nh / nb))
                seg = hist[lo:min(hi, nh)] or [0.0]
                e_out = sum(seg) / len(seg)
                dmax, prev = 0.0, seg[0]
                for v in seg[1:]:
                    dmax = max(dmax, abs(v - prev))
                    prev = v
                e_in = min(1.0, dmax * 5.0)
            else:
                e_out = e_in = 0.0
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            a = math.radians(i * (360.0 / nb)) + ring_off
            ca, sa = math.cos(a), math.sin(a)
            h_out = r * (idle + 0.30 * e_out)
            r0 = base_r + r * 0.085
            out_lines.append(QLineF(cx + ca * r0, cy + sa * r0,
                                    cx + ca * (r0 + h_out),
                                    cy + sa * (r0 + h_out)))
            h_in = r * (idle * 0.7 + 0.21 * e_in)
            r1 = base_r - r * 0.085
            in_lines.append(QLineF(cx + ca * r1, cy + sa * r1,
                                   cx + ca * (r1 - h_in),
                                   cy + sa * (r1 - h_in)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.7))
        p.drawLines(out_lines)
        p.setPen(QPen(blend(main, 0.5), 1.4))
        p.drawLines(in_lines)
        # One comet head per spike ring, at the first spike's angle on
        # the ring's FIXED rest tip circle: r0 + the height a spike
        # has at zero level (the idle constant 0.018, its phase
        # shimmer averaged out), and r1 − the same rest height at the
        # inner ring's 0.7 scale. Riding bin 0's live h_out/h_in made
        # the heads ride up and down with the level — the spikes
        # still do that; the heads only orbit. The rings rotate
        # toward decreasing angle (ring_off falls as t grows —
        # counter-rotating against the ribbon), so their trails
        # stream toward increasing angle, behind the motion.
        tip_out = base_r + r * (0.085 + 0.018)
        tip_in = base_r - r * (0.085 + 0.018 * 0.7)
        self._draw_comet_head(p, cx, cy, tip_out, ring_off, +1,
                              acc if live else main, 0.7, blend)
        self._draw_comet_head(p, cx, cy, tip_in, ring_off, +1,
                              main, 0.6, blend)

        # 9. The inner ring the name sits in, and the name — sized exactly
        #    as the reactor sizes it.
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))
        name = self._assistant_name or ""
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105,
                                 (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
            f = QFont("Courier New", fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)


# Accent-linked palette (Port 5, Customize): the teal family only —
# ACC/ACC2/GREEN/MUTED_C are signal colours and never move.
_ACCENT_DEFAULTS = {'PRI': '#00d4ff', 'PRI_DIM': '#007a99',
                    'WHITE': '#d8f8ff'}


def apply_accent(accent_hex) -> bool:
    """Hue-shift the orb's accent-linked colours to a new accent
    (aria/hue_wheel.shift_palette). None restores the defaults.
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
