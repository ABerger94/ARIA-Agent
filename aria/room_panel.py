"""ARIA's Room — the dashboard centerpiece for her life (2026-10-29).

Paints the side-view room from life_state.json (written by the
life subprocess, aria/life_process.py): bed, plant, bookshelf +
chair, window, desk, workbench, music corner — and her, a small
pixel robot (Alek's call: a robot, not a person) with a visor and
one aperture eye, an antenna, a chest core, and a single wheel.

Everything is procedural QPainter work — no image assets, like
the constellation and the visualizer. The widget polls the state
file only while visible; a missing or stale file paints the room
quiet with her powered down on the bed, never an error.

Sleep pose rule (Alek, 2026-10-29): on the bed her head is at the
TOP of the bed — the headboard/pillow end, the left end in this
layout — never at the foot.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime

try:
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QColor, QPainter
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget
except Exception:  # pragma: no cover - Qt absent (headless tools)
    QWidget = object

LOGICAL_W, LOGICAL_H, FLOOR_Y = 1140, 500, 408

WALL = QColor("#182635")
FLOOR_C = QColor("#3a2c22")
FLOOR_L = QColor("#2e2119")
WOOD = QColor("#5a4232")
WOOD_D = QColor("#463325")
SKIN_TIN = QColor("#54687c")      # her chassis light metal
CHASSIS = QColor("#46586a")
CHASSIS_D = QColor("#35434f")
VISOR = QColor("#0d1522")
DEFAULT_ACCENT = QColor("#57c7d4")
PAPER = QColor("#e8e0c8")
PLANT_G = QColor("#4da35c")
PLANT_D = QColor("#35703f")
POT = QColor("#a35c3d")

# object anchor x positions, normalized — mirrors life_sim.OBJECTS
ANCHORS = {"bed": 0.06, "plant": 0.23, "shelf": 0.345, "chair": 0.40,
           "window": 0.52, "desk": 0.675, "bench": 0.84, "music": 0.93}


def default_state_path():
    # Install root via aria.data_dir (V20); the first resolve
    # migrates a real legacy ~/ARIA/life_state.json, so the
    # room paints her current state on first open after the
    # move even before the life subprocess writes anew.
    from aria import data_dir
    return data_dir.path("life_state.json")


def _hour_of(state):
    try:
        return datetime.fromisoformat(state["updated_at"]).hour
    except Exception:
        return datetime.now().hour


class _Canvas(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self.setMinimumSize(380, 180)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        try:
            self._panel._paint(p, self.width(), self.height())
        finally:
            p.end()


class RoomWidget(QWidget):
    """Composite centerpiece: the painted room + caption lines."""

    def __init__(self, parent=None, state_path=None):
        super().__init__(parent)
        self._state_path = state_path or default_state_path()
        self._state = None
        self._accent = DEFAULT_ACCENT
        self._tick = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.canvas = _Canvas(self)
        lay.addWidget(self.canvas, 1)
        self.caption = QLabel("Her room is quiet")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setStyleSheet("color:#e8f4fa; font-size:14px;")
        lay.addWidget(self.caption)
        self.thought = QLabel("")
        self.thought.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thought.setStyleSheet(
            "color:#8fb0c4; font-size:12px; font-style:italic;")
        lay.addWidget(self.thought)
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._on_tick)

    # -- public API (mirrors the visualizer's) ----------------------

    def set_accent(self, hex_color):
        try:
            color = QColor(hex_color)
            if color.isValid():
                self._accent = color
        except Exception:
            pass

    def set_state(self, state):
        """Feed a state dict directly (tests, previews)."""
        self._state = state
        self._sync_caption()
        self.canvas.update()

    def state(self):
        return self._state

    # -- lifecycle ----------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._poll()
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _on_tick(self):
        self._tick += 1
        if self._tick % 4 == 0:
            self._poll()
        self.canvas.update()          # walk/bob/antenna animation

    def _poll(self):
        try:
            with open(self._state_path, encoding="utf-8") as f:
                state = json.load(f)
            if isinstance(state, dict) and state.get("version") == 1:
                self._state = state
            else:
                self._state = None
        except Exception:
            self._state = None
        self._sync_caption()

    def _sync_caption(self):
        s = self._state
        if s is None:
            self.caption.setText(
                "Her room is quiet — her life isn't running")
            self.thought.setText("")
            return
        act = s.get("activity") or {}
        self.caption.setText(act.get("label") or "In her room")
        thought = s.get("current_thought") or ""
        self.thought.setText(f"“{thought}”" if thought else "")

    # -- painting -------------------------------------------------------

    def _paint(self, p, w, h):
        scale = min(w / LOGICAL_W, h / LOGICAL_H)
        p.save()
        p.translate((w - LOGICAL_W * scale) / 2,
                    (h - LOGICAL_H * scale) / 2)
        p.scale(scale, scale)
        s = self._state
        hour = _hour_of(s) if s else datetime.now().hour
        self._paint_room(p, hour, s)
        self._paint_her(p, s)
        p.restore()

    def _paint_room(self, p, hour, s):
        p.fillRect(0, 0, LOGICAL_W, FLOOR_Y, WALL)
        p.fillRect(0, FLOOR_Y, LOGICAL_W, LOGICAL_H - FLOOR_Y, FLOOR_C)
        p.setPen(FLOOR_L)
        for x in range(0, LOGICAL_W, 76):
            p.drawLine(x, FLOOR_Y, x, LOGICAL_H)
        p.setPen(Qt.PenStyle.NoPen)
        X = lambda k: int(ANCHORS[k] * LOGICAL_W)

        # window: sky follows the hour in her state
        wx = X("window") - 52
        night = hour >= 20 or hour < 6
        dusk = hour in (6, 7, 18, 19)
        sky = QColor("#1d2f47") if night else (
            QColor("#5a4632") if dusk else QColor("#7fa8c9"))
        p.fillRect(wx, 84, 118, 168, sky)
        if night:
            p.fillRect(wx + 69, 115, 26, 26, QColor("#e8c87a"))
            p.setBrush(QColor("#cfe3ee"))
            for sx, sy in ((22, 110), (40, 150), (95, 180),
                           (26, 205), (66, 224)):
                p.drawRect(wx + sx, sy, 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)
        else:
            p.fillRect(wx + 66, 108, 30, 30, QColor("#f2d98c"))
        p.setPen(QColor("#31435a"))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(wx, 84, 118, 168)
        p.drawLine(wx + 59, 84, wx + 59, 252)
        p.drawLine(wx, 168, wx + 118, 168)
        p.setPen(Qt.PenStyle.NoPen)

        # bed (headboard + pillow at the LEFT end)
        bx = X("bed") - 70
        p.fillRect(bx, 352, 160, 18, WOOD_D)
        p.fillRect(bx, 318, 14, 52, WOOD)
        p.fillRect(bx + 146, 336, 14, 34, WOOD)
        p.fillRect(bx + 8, 330, 52, 24, QColor("#dfe7ee"))
        p.fillRect(bx + 58, 336, 100, 20, QColor("#37506b"))
        p.fillRect(bx + 58, 336, 100, 6, QColor("#43617f"))

        # plant (growth + thirst from her state)
        plant = (s or {}).get("plant") or {}
        growth = int(plant.get("growth", 2))
        thirst = float(plant.get("thirst", 30))
        px = X("plant")
        p.fillRect(px - 16, 368, 32, 26, POT)
        p.fillRect(px - 20, 362, 40, 8, POT)
        stem_h = 30 + growth * 4
        p.fillRect(px - 2, 368 - stem_h, 4, stem_h, PLANT_D)
        leaf = PLANT_G if thirst < 70 else PLANT_D
        droop = 6 if thirst >= 70 else 0
        for lx, ly, lw, lh in ((-16, -14, 18, 12), (-2, -24, 20, 13),
                               (8, -10, 16, 11), (-10, -2, 14, 10),
                               (2, 2, 15, 10)):
            p.fillRect(px + lx, 368 - stem_h + ly + droop, lw, lh, leaf)

        # bookshelf + chair
        sx = X("shelf") - 64
        p.fillRect(sx, 196, 104, 176, WOOD_D)
        cols = ("#a34d4d", "#4d7aa3", "#57a35c", "#c2a24d",
                "#7a4b8f", "#a35c3d", "#4da39a")
        for r in range(3):
            y = 212 + r * 56
            p.fillRect(sx + 6, y + 34, 92, 6, WOOD)
            cx = sx + 10
            for i in range(6):
                bh = 20 + ((i * 7 + r * 3) % 10)
                p.fillRect(cx, y + 34 - bh, 9, bh,
                           QColor(cols[(i + r) % 7]))
                cx += 15
        chx = X("chair") + 30
        p.fillRect(chx, 320, 10, 52, WOOD)
        p.fillRect(chx, 352, 44, 10, WOOD_D)
        p.fillRect(chx + 36, 362, 8, 34, WOOD_D)
        p.fillRect(chx + 4, 362, 8, 34, WOOD_D)
        p.fillRect(chx - 2, 344, 50, 12, QColor("#37506b"))

        # desk with journal + terminal
        dx = X("desk") - 66
        p.fillRect(dx, 318, 140, 10, WOOD)
        p.fillRect(dx + 6, 328, 8, 68, WOOD_D)
        p.fillRect(dx + 126, 328, 8, 68, WOOD_D)
        p.fillRect(dx + 70, 272, 52, 36, VISOR)
        p.fillRect(dx + 74, 276, 44, 26, QColor("#17424d"))
        p.fillRect(dx + 78, 282, 26, 3, self._accent)
        p.fillRect(dx + 78, 290, 18, 3, self._accent)
        p.fillRect(dx + 14, 306, 34, 8, PAPER)
        p.fillRect(dx + 16, 302, 30, 5, QColor("#cfc5a5"))

        # workbench
        wx2 = X("bench") - 56
        p.fillRect(wx2, 326, 120, 10, WOOD)
        p.fillRect(wx2 + 6, 336, 8, 60, WOOD_D)
        p.fillRect(wx2 + 106, 336, 8, 60, WOOD_D)
        p.fillRect(wx2 + 16, 306, 22, 20, QColor("#6b7684"))
        p.fillRect(wx2 + 22, 298, 10, 8, QColor("#3d97a5"))
        p.fillRect(wx2 + 52, 312, 16, 14, POT)
        p.fillRect(wx2 + 78, 300, 8, 26, QColor("#8a93a0"))
        p.fillRect(wx2 + 80, 292, 4, 8, QColor("#e8c87a"))

        # music corner speaker
        mx = X("music") + 18
        p.fillRect(mx, 306, 36, 90, QColor("#222d3a"))
        p.fillRect(mx + 6, 316, 24, 24, QColor("#31435a"))
        p.fillRect(mx + 11, 321, 14, 14, VISOR)
        p.fillRect(mx + 6, 352, 24, 30, QColor("#31435a"))
        p.fillRect(mx + 11, 359, 14, 16, VISOR)

    # -- her ------------------------------------------------------------

    def _paint_her(self, p, s):
        t = time.monotonic()
        if s is None or s.get("mode") in ("asleep", "paused") \
                or (s.get("anim") == "sleep"):
            self._paint_sleeping(p, t)
            return
        anim = s.get("anim", "idle")
        act = (s.get("activity") or {}).get("name")
        fx = float(s.get("pos_x", ANCHORS["desk"])) * LOGICAL_W
        seated = act in ("read", "sit_think")
        p.save()
        p.translate(fx, FLOOR_Y)
        p.scale(1.75, 1.75)
        self._robot(p, anim, t, seated)
        p.restore()

    def _paint_sleeping(self, p, t):
        # On the bed, powered down, HEAD AT THE PILLOW (left) end.
        bx = ANCHORS["bed"] * LOGICAL_W - 70
        p.save()
        p.translate(bx + 84, FLOOR_Y)
        p.scale(1.75, 1.75)
        p.fillRect(-32, -48, 70, 16, CHASSIS)
        p.fillRect(-32, -48, 70, 4, SKIN_TIN)
        p.fillRect(-54, -52, 22, 20, SKIN_TIN)       # head, pillow end
        p.fillRect(-50, -46, 14, 7, VISOR)
        p.fillRect(-47, -43, 8, 2, QColor("#2b6d77"))
        p.fillRect(-24, -34, 8, 10, QColor("#26313d"))
        p.fillRect(26, -34, 8, 10, QColor("#26313d"))
        p.setPen(QColor("#8fb0c4"))
        zz = int(math.sin(t * 1.4) * 3)
        p.drawText(-56, -60 + zz, "z")
        p.drawText(-46, -72 + zz, "z")
        p.setPen(Qt.PenStyle.NoPen)
        p.restore()

    def _robot(self, p, anim, t, seated):
        bob = 0.0
        if anim == "walk":
            bob = abs(math.sin(t * 10)) * 2
        elif anim == "listen":
            bob = math.sin(t * 4) * 3
        p.save()
        if seated:
            p.translate(0, -21)
        else:
            p.translate(0, -bob)
        # shadow
        p.setBrush(QColor(0, 0, 0, 60))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(-16, -2, 32, 5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if seated:
            p.fillRect(-8, -16, 16, 4, QColor("#26313d"))
        else:
            # single wheel; spokes turn while walking
            p.setBrush(QColor("#26313d"))
            p.drawEllipse(-9, -18, 18, 18)
            p.setBrush(Qt.BrushStyle.NoBrush)
            a = t * 9 if anim == "walk" else 0.6
            p.setPen(QColor("#7d99ad"))
            for k in range(2):
                ang = a + k * (math.pi / 2)
                p.drawLine(int(math.cos(ang) * -6), int(-9 + math.sin(ang) * -6),
                           int(math.cos(ang) * 6), int(-9 + math.sin(ang) * 6))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._accent)
            p.drawEllipse(-2, -11, 5, 5)
            p.setBrush(Qt.BrushStyle.NoBrush)
        # chassis + chest core
        p.fillRect(-13, -36, 26, 20, CHASSIS)
        p.fillRect(-13, -36, 26, 4, SKIN_TIN)
        p.fillRect(-13, -20, 26, 3, CHASSIS_D)
        pulse = 3.4 + math.sin(t * 2.2) * 0.7
        p.setBrush(self._accent)
        p.drawEllipse(int(-pulse), int(-27 - pulse),
                      int(pulse * 2), int(pulse * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # arms
        p.fillRect(-18, -34, 5, 16, QColor("#3a4a59"))
        p.fillRect(-19, -18, 7, 4, SKIN_TIN)
        if anim in ("read", "write"):
            p.fillRect(13, -32, 14, 5, QColor("#3a4a59"))
        elif anim == "water":
            p.fillRect(13, -34, 16, 5, QColor("#3a4a59"))
        elif anim == "tinker":
            p.fillRect(13, -30, 14, 5, QColor("#3a4a59"))
        elif anim == "listen":
            p.fillRect(13, -38, 5, 14, QColor("#3a4a59"))
        else:
            p.fillRect(13, -34, 5, 16, QColor("#3a4a59"))
            p.fillRect(12, -18, 7, 4, SKIN_TIN)
        # head: boxy, visor, one aperture eye, antenna
        p.fillRect(-12, -58, 24, 20, SKIN_TIN)
        p.fillRect(-12, -58, 24, 4, QColor("#63788c"))
        p.fillRect(-15, -50, 3, 10, CHASSIS_D)
        p.fillRect(12, -50, 3, 10, CHASSIS_D)
        p.fillRect(-9, -52, 18, 11, VISOR)
        gaze = 3 if anim == "walk" else (
            1 if anim in ("read", "write") else int(math.sin(t * 0.7) * 2))
        blink = math.sin(t * 0.9) > 0.985
        if blink:
            p.fillRect(gaze - 4, -48, 9, 2, self._accent)
        else:
            p.setBrush(self._accent)
            p.drawEllipse(gaze - 4, -50, 9, 7)
            p.setBrush(VISOR)
            p.drawEllipse(gaze - 1, -48, 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.fillRect(-1, -66, 2, 8, QColor("#63788c"))
        tip = self._accent if math.sin(t * 2.6) > 0 else QColor("#3d97a5")
        p.setBrush(tip)
        p.drawEllipse(-3, -71, 6, 6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # props
        if anim == "read":
            p.fillRect(24, -38, 16, 12, PAPER)
            p.fillRect(25, -37, 6, 10, QColor("#a34d4d"))
            p.fillRect(32, -37, 6, 10, QColor("#4d7aa3"))
        elif anim == "write":
            p.fillRect(24, -34, 18, 4, PAPER)
        elif anim == "water":
            p.fillRect(27, -40, 12, 10, QColor("#6b7684"))
            d = int((t * 40) % 16)
            p.fillRect(33, -28 + d, 2, 4, self._accent)
            p.fillRect(29, -22 + d, 2, 4, self._accent)
        elif anim == "tinker":
            p.fillRect(25, -26, 10, 4, QColor("#c2a24d"))
        elif anim == "listen":
            p.setPen(self._accent)
            n = int(math.sin(t * 2) * 2)
            p.drawText(20, -56 + n, "♪")
            p.drawText(-30, -48 - n, "♪")
            p.setPen(Qt.PenStyle.NoPen)
        p.restore()
