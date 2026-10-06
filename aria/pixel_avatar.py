"""Procedural hardware chassis & aperture optics avatar for the ARIA HUD.

Features:
- Sleek matte sculptural chassis with clean mechanical lines and desk presence.
- Glowing phosphor aperture optics that dilate, focus, track gaze, and meet Alek's eyes.
- Fluid cognitive-state expressions (idle, listening, thinking, speaking, coding, working).
- Full backwards compatibility with HUD color themes (midnight, sunset, matrix, ocean).
- Dual-engine architecture: 'chassis' (new hardware form) and 'classic' (retro pixel avatar),
  with instantaneous zero-downtime rollback capability.
"""
import math
import os
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Palettes & Constants
# ---------------------------------------------------------------------------
WHITE = (245, 245, 250)
DARK = (12, 12, 18)
CHASSIS_DARK = (16, 18, 24)
CHASSIS_BODY = (24, 28, 38)
CHASSIS_MID = (38, 44, 58)
CHASSIS_LIGHT = (56, 65, 84)
CHASSIS_BEVEL = (82, 94, 118)
BOLT_COL = (105, 120, 145)

PINK = (150, 80, 255)
PINK_DEEP = (95, 45, 185)
PINK_BRIGHT = (190, 140, 255)
CYAN = (255, 255, 0)       # Electric cyan (BGR)
ACCENT_DIM = (170, 170, 0)
GREEN = (40, 240, 120)     # Emerald coding phosphor
GREEN_DIM = (20, 140, 70)
AMBER = (0, 180, 255)      # Amber working phosphor
AMBER_DIM = (0, 110, 165)
GRAY = (120, 120, 132)
GRAY_DK = (70, 70, 80)
VISOR = (28, 28, 44)

# Grid scale for classic pixel mode
S = 9
GW, GH = 16, 34

# Themes
THEMES = {
    "midnight": {"accent": CYAN, "accent_dim": ACCENT_DIM,
                 "accent2": PINK, "accent2_bright": PINK_BRIGHT, "accent2_deep": PINK_DEEP},
    "sunset": {"accent": (0, 165, 255), "accent_dim": (0, 105, 165),
               "accent2": (200, 70, 255), "accent2_bright": (230, 140, 255),
               "accent2_deep": (130, 35, 170)},
    "matrix": {"accent": (60, 255, 140), "accent_dim": (30, 150, 75),
               "accent2": (180, 255, 190), "accent2_bright": (225, 255, 230),
               "accent2_deep": (90, 150, 100)},
    "ocean": {"accent": (255, 140, 40), "accent_dim": (160, 85, 25),
              "accent2": (255, 255, 150), "accent2_bright": (255, 255, 215),
              "accent2_deep": (170, 170, 90)},
}
_THEME_ORDER = ["midnight", "sunset", "matrix", "ocean"]
_THEME_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "theme.cfg")
_STYLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "avatar_style.cfg")

_theme_name = "midnight"
_avatar_style = "chassis"  # "chassis" or "classic"

ACCENT, ACCENT_DIM = CYAN, ACCENT_DIM
ACCENT2, ACCENT2_BRIGHT, ACCENT2_DEEP = PINK, PINK_BRIGHT, PINK_DEEP


def _apply_theme():
    global ACCENT, ACCENT_DIM, ACCENT2, ACCENT2_BRIGHT, ACCENT2_DEEP
    th = THEMES.get(_theme_name, THEMES["midnight"])
    ACCENT = th["accent"]
    ACCENT_DIM = th["accent_dim"]
    ACCENT2 = th["accent2"]
    ACCENT2_BRIGHT = th["accent2_bright"]
    ACCENT2_DEEP = th["accent2_deep"]


def get_theme() -> str:
    return _theme_name


def load_theme() -> str:
    global _theme_name, _avatar_style
    try:
        if os.path.exists(_THEME_FILE):
            with open(_THEME_FILE, "r", encoding="utf-8") as f:
                saved = f.read().strip().lower()
                if saved in THEMES:
                    _theme_name = saved
        if os.path.exists(_STYLE_FILE):
            with open(_STYLE_FILE, "r", encoding="utf-8") as f:
                saved_style = f.read().strip().lower()
                if saved_style in ("chassis", "classic"):
                    _avatar_style = saved_style
    except Exception:
        pass
    _apply_theme()
    return _theme_name


def theme_colors():
    return ACCENT, ACCENT2


def set_theme(name: str) -> str:
    global _theme_name
    name = (name or "").lower().strip()
    if name in THEMES:
        _theme_name = name
        _apply_theme()
        try:
            with open(_THEME_FILE, "w", encoding="utf-8") as f:
                f.write(name)
        except Exception:
            pass
    return _theme_name


def cycle_theme() -> str:
    try:
        idx = _THEME_ORDER.index(_theme_name)
        nxt = _THEME_ORDER[(idx + 1) % len(_THEME_ORDER)]
    except ValueError:
        nxt = _THEME_ORDER[0]
    return set_theme(nxt)


def get_avatar_style() -> str:
    return _avatar_style


def set_avatar_style(style: str) -> str:
    global _avatar_style
    style = (style or "").lower().strip()
    if style in ("chassis", "classic"):
        _avatar_style = style
        try:
            with open(_STYLE_FILE, "w", encoding="utf-8") as f:
                f.write(style)
        except Exception:
            pass
    return _avatar_style


def toggle_avatar_style() -> str:
    new_style = "classic" if _avatar_style == "chassis" else "chassis"
    return set_avatar_style(new_style)


def rollback_to_classic() -> str:
    """Instantly reverts to the previous pixel-person avatar."""
    return set_avatar_style("classic")


# ---------------------------------------------------------------------------
# Sleek Matte Hardware Chassis & Phosphor Aperture Optics Engine
# ---------------------------------------------------------------------------
_last_state = "idle"
_state_change_time = 0.0
_last_theme_check = 0.0


def _draw_chassis_avatar(canvas, state: str, t: float, mood: Optional[str] = None):
    """Render the sleek sculptural hardware chassis with glowing phosphor aperture optics."""
    global _last_state, _state_change_time
    if state != _last_state:
        _last_state = state
        _state_change_time = t

    state_dur = max(0.0, t - _state_change_time)
    cx, cy = 640, 266

    accent = ACCENT
    accent_dim = ACCENT_DIM
    accent2 = ACCENT2

    # Tool call preference: coding gets green optics per user preference
    if state == "coding":
        accent = GREEN
        accent_dim = GREEN_DIM
    elif state == "working":
        accent = AMBER
        accent_dim = AMBER_DIM

    # Base dynamics: subtle floating breath
    bob = int(round(math.sin(t * 1.8) * 2))
    cy += bob

    # 1. Desktop shadow & grounded pedestal base (desk presence)
    base_y = 472
    cv2.ellipse(canvas, (cx, base_y + 12), (138, 18), 0, 0, 360, (8, 9, 12), -1)
    cv2.ellipse(canvas, (cx, base_y + 8), (116, 14), 0, 0, 360, (14, 16, 22), -1)

    pts_base = np.array([
        [cx - 108, base_y + 6],
        [cx + 108, base_y + 6],
        [cx + 82, base_y - 22],
        [cx - 82, base_y - 22]
    ], np.int32)
    cv2.fillPoly(canvas, [pts_base], CHASSIS_DARK)
    cv2.polylines(canvas, [pts_base], True, CHASSIS_MID, 2)
    cv2.line(canvas, (cx - 78, base_y - 12), (cx + 78, base_y - 12), CHASSIS_LIGHT, 1)

    # 2. Articulated neck strut & pivot joint
    strut_top = cy + 102
    strut_bot = base_y - 22
    cv2.rectangle(canvas, (cx - 20, strut_top), (cx + 20, strut_bot), CHASSIS_BODY, -1)
    cv2.rectangle(canvas, (cx - 20, strut_top), (cx + 20, strut_bot), CHASSIS_MID, 2)
    
    # Cable conduit harness with pulsing telemetry blip
    pulse_pos = int((t * 26) % max(1, (strut_bot - strut_top)))
    cv2.line(canvas, (cx - 8, strut_top), (cx - 8, strut_bot), (16, 18, 24), 3)
    cv2.line(canvas, (cx + 8, strut_top), (cx + 8, strut_bot), (16, 18, 24), 3)
    cv2.circle(canvas, (cx - 8, strut_top + pulse_pos), 2, accent_dim, -1)
    cv2.circle(canvas, (cx + 8, strut_bot - pulse_pos), 2, accent_dim, -1)
    
    # Pivot joint mounting bolts
    cv2.circle(canvas, (cx - 14, strut_top + 18), 5, CHASSIS_BEVEL, -1)
    cv2.circle(canvas, (cx + 14, strut_top + 18), 5, CHASSIS_BEVEL, -1)
    cv2.circle(canvas, (cx - 14, strut_top + 18), 2, BOLT_COL, -1)
    cv2.circle(canvas, (cx + 14, strut_top + 18), 2, BOLT_COL, -1)

    # 3. Outer sculpted matte chassis (turret head)
    w_top, w_mid, w_bot = 145, 195, 135
    h_top, h_mid, h_bot = 98, 22, 102

    pts_chassis = np.array([
        [cx - w_top, cy - h_top],
        [cx + w_top, cy - h_top],
        [cx + w_mid, cy + h_mid],
        [cx + w_bot, cy + h_bot],
        [cx - w_bot, cy + h_bot],
        [cx - w_mid, cy + h_mid]
    ], np.int32)

    # Outer drop shadow & primary shell
    cv2.fillPoly(canvas, [pts_chassis + np.array([0, 5])], (10, 11, 15))
    cv2.fillPoly(canvas, [pts_chassis], CHASSIS_BODY)
    cv2.polylines(canvas, [pts_chassis], True, CHASSIS_MID, 2)

    # Mechanical chamfers / bevel highlights
    cv2.line(canvas, (cx - w_top + 12, cy - h_top + 8), (cx + w_top - 12, cy - h_top + 8), CHASSIS_BEVEL, 1)
    cv2.line(canvas, (cx - w_mid + 6, cy + h_mid), (cx - w_top + 8, cy - h_top + 10), CHASSIS_LIGHT, 1)
    cv2.line(canvas, (cx + w_mid - 6, cy + h_mid), (cx + w_top - 8, cy - h_top + 10), CHASSIS_LIGHT, 1)
    cv2.line(canvas, (cx - w_mid + 6, cy + h_mid), (cx - w_bot + 6, cy + h_bot - 8), CHASSIS_LIGHT, 1)
    cv2.line(canvas, (cx + w_mid - 6, cy + h_mid), (cx + w_bot - 6, cy + h_bot - 8), CHASSIS_LIGHT, 1)

    # 4. Streamlined top sensor fins
    fin_l = np.array([[cx - 118, cy - h_top], [cx - 86, cy - h_top - 36], [cx - 72, cy - h_top]], np.int32)
    fin_r = np.array([[cx + 118, cy - h_top], [cx + 86, cy - h_top - 36], [cx + 72, cy - h_top]], np.int32)
    cv2.fillPoly(canvas, [fin_l], CHASSIS_DARK)
    cv2.polylines(canvas, [fin_l], True, CHASSIS_LIGHT, 1)
    cv2.fillPoly(canvas, [fin_r], CHASSIS_DARK)
    cv2.polylines(canvas, [fin_r], True, CHASSIS_LIGHT, 1)
    
    # Fiber-optic edge lighting
    fin_glow = accent if (t * 3) % 2 < 1 else accent_dim
    cv2.line(canvas, (cx - 88, cy - h_top - 34), (cx - 74, cy - h_top - 2), fin_glow, 2)
    cv2.line(canvas, (cx + 88, cy - h_top - 34), (cx + 74, cy - h_top - 2), fin_glow, 2)

    # 5. Temple cooling vents / heat sink baffles
    for i in range(4):
        vy = cy - 25 + i * 14
        # Left vents
        cv2.line(canvas, (cx - w_mid + 8, vy), (cx - w_mid + 28, vy), (14, 16, 22), 3)
        cv2.line(canvas, (cx - w_mid + 10, vy), (cx - w_mid + 26, vy), CHASSIS_BEVEL, 1)
        # Right vents
        cv2.line(canvas, (cx + w_mid - 8, vy), (cx + w_mid - 28, vy), (14, 16, 22), 3)
        cv2.line(canvas, (cx + w_mid - 10, vy), (cx + w_mid - 26, vy), CHASSIS_BEVEL, 1)

    # 6. Inner dark visor aperture recess
    vw, vh_t, vh_b = 152, 62, 42
    pts_visor = np.array([
        [cx - vw + 24, cy - vh_t],
        [cx + vw - 24, cy - vh_t],
        [cx + vw, cy + 12],
        [cx + vw - 36, cy + vh_b],
        [cx - vw + 36, cy + vh_b],
        [cx - vw, cy + 12]
    ], np.int32)
    cv2.fillPoly(canvas, [pts_visor], (6, 7, 10))
    cv2.polylines(canvas, [pts_visor], True, CHASSIS_DARK, 2)
    cv2.polylines(canvas, [pts_visor], True, CHASSIS_MID, 1)

    # Precision laser markings
    cv2.putText(canvas, "A.R.I.A. // APERTURE OPT-01", (cx - 82, cy - vh_t + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (95, 110, 132), 1, cv2.LINE_AA)
    cv2.putText(canvas, "MATTE TITANIUM CHASSIS", (cx - 68, cy - vh_t + 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.24, (55, 65, 80), 1, cv2.LINE_AA)

    # Corner alignment reticle ticks
    for sx, sy in [(-vw + 32, -vh_t + 36), (vw - 32, -vh_t + 36)]:
        cv2.line(canvas, (cx + sx - 5, cy + sy), (cx + sx + 5, cy + sy), (40, 48, 62), 1)
        cv2.line(canvas, (cx + sx, cy + sy - 5), (cx + sx, cy + sy + 5), (40, 48, 62), 1)

    # 7. Optical Aperture Eyes logic
    m = (mood or "").lower().strip()
    eye_radius = 42
    open_pct = 0.72
    gaze_x, gaze_y = 0.0, 0.0
    shutter_close = 0.0

    # Crisp mechanical shutter blink dynamics (every ~4.8s)
    blink_cycle = t % 4.8
    if blink_cycle < 0.16:
        shutter_close = 1.0 - abs(blink_cycle - 0.08) / 0.08

    if state == "listening":
        open_pct = 0.92 + 0.06 * math.sin(t * 8)
        gaze_x, gaze_y = 0.0, 0.0
    elif state == "coding":
        open_pct = 0.58
        gaze_y = 0.35
        gaze_x = 0.15 * math.sin(t * 1.5)
    elif state == "working":
        open_pct = 0.62
        gaze_y = 0.25
        gaze_x = -0.2 * math.sin(t * 1.2)
    elif state == "thinking":
        open_pct = 0.68 + 0.15 * math.sin(t * 4)
        if state_dur < 1.5:
            gaze_x, gaze_y = 0.45, -0.25
        elif state_dur < 4.0:
            gaze_x, gaze_y = -0.45, -0.25
        else:
            gaze_x, gaze_y = 0.0, -0.3
    elif state == "speaking":
        open_pct = 0.75 + 0.18 * abs(math.sin(t * 9))
        gaze_x = 0.1 * math.sin(t * 2)
        gaze_y = 0.05 * math.cos(t * 2)
    elif state == "excited":
        open_pct = 0.96
        gaze_x = 0.1 * math.sin(t * 5)
        gaze_y = -0.1
    else:  # idle
        hour = datetime.now().hour
        is_late = hour >= 23 or hour < 6
        is_sleepy = ("sleepy" in m) or ("tired" in m) or is_late
        is_curious = ("curious" in m) or ("inquisitive" in m)
        is_focused = ("focused" in m) or ("analytical" in m)
        is_skeptical = ("skeptical" in m) or ("blunt" in m) or ("direct" in m)

        if is_sleepy:
            shutter_close = max(shutter_close, 0.68)
            open_pct = 0.45
            gaze_x, gaze_y = 0.0, 0.2
        elif is_skeptical:
            open_pct = 0.55
            gaze_x, gaze_y = 0.0, 0.0
        elif is_curious:
            open_pct = 0.82
            gaze_cycle = int(t // 2.5) % 3
            if gaze_cycle == 0:
                gaze_x, gaze_y = -0.35, -0.1
            elif gaze_cycle == 1:
                gaze_x, gaze_y = 0.35, -0.1
            else:
                gaze_x, gaze_y = 0.0, 0.0
        elif is_focused:
            open_pct = 0.52
            gaze_x, gaze_y = 0.0, 0.0
        else:
            # Natural micro-saccades meeting Alek's gaze
            saccade_cycle = int(t // 3.5) % 5
            if saccade_cycle == 1:
                gaze_x, gaze_y = -0.22, 0.0
            elif saccade_cycle == 3:
                gaze_x, gaze_y = 0.22, -0.08
            else:
                gaze_x, gaze_y = 0.0, 0.0
            open_pct = 0.70 + 0.05 * math.sin(t * 2.2)

    lx = cx - 72
    rx = cx + 72
    ey = cy + 4

    def render_aperture_eye(ex, ey, side_sign):
        # A. Outer mechanical bezel with index marks
        cv2.circle(canvas, (ex, ey), eye_radius + 9, CHASSIS_DARK, 2)
        cv2.circle(canvas, (ex, ey), eye_radius + 6, CHASSIS_MID, 1)
        for deg in range(0, 360, 20):
            rad = math.radians(deg)
            r1 = eye_radius + 4
            r2 = eye_radius + 7 if deg % 60 == 0 else eye_radius + 5
            x1 = int(ex + r1 * math.cos(rad))
            y1 = int(ey + r1 * math.sin(rad))
            x2 = int(ex + r2 * math.cos(rad))
            y2 = int(ey + r2 * math.sin(rad))
            cv2.line(canvas, (x1, y1), (x2, y2), CHASSIS_LIGHT if deg % 60 == 0 else CHASSIS_MID, 1)

        # B. Concentric focus track ring
        cv2.circle(canvas, (ex, ey), eye_radius, (14, 16, 24), -1)
        cv2.circle(canvas, (ex, ey), eye_radius, CHASSIS_MID, 1)

        # C. Phosphor Glow Core
        eff_radius = int(eye_radius * max(0.2, min(1.0, open_pct)))
        px = int(ex + gaze_x * 9)
        py = int(ey + gaze_y * 7)

        # Saturated outer phosphor halo
        cv2.circle(canvas, (px, py), min(eye_radius - 2, eff_radius + 8), accent_dim, -1)
        # Intense mid phosphor
        cv2.circle(canvas, (px, py), eff_radius, accent, -1)
        # Bright core
        core_highlight = (255, 255, 180) if accent == CYAN else WHITE
        cv2.circle(canvas, (px, py), max(4, int(eff_radius * 0.58)), core_highlight, -1)
        # White center pupil
        cv2.circle(canvas, (px, py), max(2, int(eff_radius * 0.28)), (255, 255, 255), -1)
        # Specular glint
        cv2.circle(canvas, (px - int(eff_radius * 0.32), py - int(eff_radius * 0.32)), max(2, int(eff_radius * 0.14)), (255, 255, 255), -1)

        # D. Procedural Aperture Blades (8-blade mechanical iris)
        num_blades = 8
        blade_rot = (1.0 - open_pct) * 0.9 + (t * 0.6 if state == "thinking" else 0.0)
        for b in range(num_blades):
            ang = b * (2 * math.pi / num_blades) + blade_rot
            bx1 = int(ex + eye_radius * math.cos(ang))
            by1 = int(ey + eye_radius * math.sin(ang))
            tangent_ang = ang + 0.42
            bx2 = int(px + (eff_radius + 2) * math.cos(tangent_ang))
            by2 = int(py + (eff_radius + 2) * math.sin(tangent_ang))
            cv2.line(canvas, (bx1, by1), (bx2, by2), (20, 22, 30), 2)
            cv2.line(canvas, (bx1, by1), (bx2, by2), CHASSIS_MID, 1)

        # E. HUD Optical Reticle overlay in Coding / Working states
        if state in ("coding", "working"):
            reticle_col = accent
            cv2.line(canvas, (px - eff_radius - 6, py), (px - eff_radius - 1, py), reticle_col, 1)
            cv2.line(canvas, (px + eff_radius + 1, py), (px + eff_radius + 6, py), reticle_col, 1)
            cv2.line(canvas, (px, py - eff_radius - 6), (px, py - eff_radius - 1), reticle_col, 1)
            cv2.line(canvas, (px, py + eff_radius + 1), (px, py + eff_radius + 6), reticle_col, 1)

        # F. Mechanical Shutters / Eyelids (louvers that slide over optics)
        if shutter_close > 0.01:
            shutter_h = int(eye_radius * 2 * shutter_close)
            # Top shutter
            top_y = ey - eye_radius
            top_h = int(shutter_h * 0.6)
            cv2.rectangle(canvas, (ex - eye_radius - 6, top_y), (ex + eye_radius + 6, top_y + top_h), CHASSIS_DARK, -1)
            cv2.line(canvas, (ex - eye_radius - 6, top_y + top_h), (ex + eye_radius + 6, top_y + top_h), CHASSIS_BEVEL, 2)
            # Bottom shutter
            bot_y = ey + eye_radius
            bot_h = int(shutter_h * 0.4)
            cv2.rectangle(canvas, (ex - eye_radius - 6, bot_y - bot_h), (ex + eye_radius + 6, bot_y), CHASSIS_DARK, -1)
            cv2.line(canvas, (ex - eye_radius - 6, bot_y - bot_h), (ex + eye_radius + 6, bot_y - bot_h), CHASSIS_BEVEL, 2)

    render_aperture_eye(lx, ey, -1)
    render_aperture_eye(rx, ey, 1)

    # 8. Status LED Telemetry Rail (Power, Neural Bus, Memory, Audio, Exec)
    led_x_start = cx - 36
    led_y = cy - vh_t + 44
    for i in range(5):
        lx_pos = led_x_start + i * 18
        is_lit = True
        if state == "thinking":
            is_lit = ((int(t * 12) + i) % 5) < 3
        elif state == "listening":
            is_lit = ((int(t * 8) + i) % 2) == 0
        led_col = accent if is_lit else (30, 35, 45)
        cv2.circle(canvas, (lx_pos, led_y), 3, (12, 14, 20), -1)
        cv2.circle(canvas, (lx_pos, led_y), 2, led_col, -1)

    # 9. Acoustic speaker grille & Voice visualizer (lower chin)
    grille_y = cy + 58
    num_slots = 15
    slot_spacing = 8
    start_gx = cx - (num_slots * slot_spacing) // 2
    for i in range(num_slots):
        sx = start_gx + i * slot_spacing
        base_h = 6
        if state == "speaking":
            dyn = int(abs(math.sin(t * 14 + i * 0.8)) * 12)
            cur_h = base_h + dyn
            slot_col = accent if dyn > 4 else CHASSIS_BEVEL
        elif state == "listening":
            cur_h = base_h + int(abs(math.sin(t * 8 + i * 0.5)) * 5)
            slot_col = accent_dim if i % 2 == 0 else CHASSIS_LIGHT
        else:
            cur_h = base_h
            slot_col = CHASSIS_MID

        cv2.line(canvas, (sx, grille_y - cur_h // 2), (sx, grille_y + cur_h // 2), (10, 12, 16), 3)
        cv2.line(canvas, (sx, grille_y - cur_h // 2), (sx, grille_y + cur_h // 2), slot_col, 1)

    # 10. Peripheral ambient motes & life ring
    phase = (t * 0.35) % (2 * math.pi)
    cv2.ellipse(canvas, (cx, cy), (210, 160), 0, 0, 360, (16, 20, 26), 1)
    cv2.ellipse(canvas, (cx, cy), (210, 160), 0,
                int(math.degrees(phase)), int(math.degrees(phase) + 32),
                accent_dim, 2)


# ---------------------------------------------------------------------------
# Classic Pixel-Person Avatar Engine (Preserved for Instant Rollback)
# ---------------------------------------------------------------------------
def _r(c, ox, oy, gx, gy, gw, gh, color):
    x1 = ox + gx * S
    y1 = oy + gy * S
    cv2.rectangle(c, (x1, y1), (x1 + gw * S, y1 + gh * S), color, -1)


def _presence(c, t):
    cv2.ellipse(c, (640, 270), (145, 145), 0, 0, 360, (20, 24, 32), 1)
    phase = (t * 0.4) % (2 * math.pi)
    cv2.ellipse(c, (640, 270), (145, 145), 0,
                int(math.degrees(phase)), int(math.degrees(phase) + 38),
                ACCENT_DIM, 2)
    for i in range(12):
        mx = 400 + int((i * 173.3 + t * (6 + i % 5)) % 480)
        my = 110 + int((i * 97.7 + t * (4 + i % 3)) % 330)
        s = 2 if i % 3 else 3
        col = ACCENT_DIM if i % 2 else (60, 90, 110)
        cv2.rectangle(c, (mx, my), (mx + s, my + s), col, -1)


def _head(c, ox, oy, t, eye="open", mouth="smile", bob=0, twitch=(0, 0),
          fast_pulse=False, blush=False):
    y0 = bob
    tip = ACCENT if (t * (6 if fast_pulse else 2)) % 2 < 1 else ACCENT2
    _r(c, ox, oy, 3 + twitch[0], -5 + y0, 2, 5, GRAY_DK)
    _r(c, ox, oy, 11 + twitch[1], -5 + y0, 2, 5, GRAY_DK)
    _r(c, ox, oy, 2 + twitch[0], -7 + y0, 3, 2, tip)
    _r(c, ox, oy, 11 + twitch[1], -7 + y0, 3, 2, tip)

    _r(c, ox, oy, 0, 0 + y0, 16, 12, DARK)
    _r(c, ox, oy, 1, 1 + y0, 14, 10, WHITE)
    _r(c, ox, oy, 3, 1 + y0, 5, 1, (255, 255, 255))
    _r(c, ox, oy, 0, 5 + y0, 3, 4, DARK)
    _r(c, ox, oy, 13, 5 + y0, 3, 4, DARK)
    _r(c, ox, oy, 0, 6 + y0, 2, 2, ACCENT2)
    _r(c, ox, oy, 14, 6 + y0, 2, 2, ACCENT2)

    _r(c, ox, oy, 3, 4 + y0, 10, 6, DARK)
    _r(c, ox, oy, 4, 5 + y0, 8, 4, VISOR)

    if eye == "blink":
        _r(c, ox, oy, 5, 6 + y0, 2, 1, ACCENT)
        _r(c, ox, oy, 9, 6 + y0, 2, 1, ACCENT)
    elif eye == "happy":
        _r(c, ox, oy, 5, 5 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 5, 7 + y0, 2, 1, VISOR)
        _r(c, ox, oy, 9, 7 + y0, 2, 1, VISOR)
    elif eye == "focus":
        _r(c, ox, oy, 5, 6 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 9, 6 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 5, 6 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 9, 6 + y0, 1, 1, (255, 255, 255))
    elif eye == "sleepy":
        _r(c, ox, oy, 5, 7 + y0, 2, 1, ACCENT)
        _r(c, ox, oy, 9, 7 + y0, 2, 1, ACCENT)
    elif eye == "wide":
        _r(c, ox, oy, 4, 5 + y0, 3, 4, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 3, 4, ACCENT)
        _r(c, ox, oy, 5, 6 + y0, 1, 2, (255, 255, 255))
        _r(c, ox, oy, 10, 6 + y0, 1, 2, (255, 255, 255))
    elif eye == "sparkle":
        _r(c, ox, oy, 5, 5 + y0, 2, 3, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 3, ACCENT)
        _r(c, ox, oy, 4, 6 + y0, 4, 1, ACCENT2)
        _r(c, ox, oy, 8, 6 + y0, 4, 1, ACCENT2)
        _r(c, ox, oy, 5, 6 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 10, 6 + y0, 1, 1, (255, 255, 255))
    elif eye == "skeptical":
        _r(c, ox, oy, 5, 7 + y0, 2, 1, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 3, ACCENT)
        _r(c, ox, oy, 9, 4 + y0, 2, 1, (255, 255, 255))
    elif eye == "glance_left":
        _r(c, ox, oy, 4, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 8, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 4, 5 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 8, 5 + y0, 1, 1, (255, 255, 255))
    elif eye == "glance_right":
        _r(c, ox, oy, 6, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 10, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 7, 5 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 11, 5 + y0, 1, 1, (255, 255, 255))
    else:
        _r(c, ox, oy, 5, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 5, 5 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 9, 5 + y0, 1, 1, (255, 255, 255))

    if blush:
        _r(c, ox, oy, 3, 8 + y0, 2, 1, ACCENT2_BRIGHT)
        _r(c, ox, oy, 11, 8 + y0, 2, 1, ACCENT2_BRIGHT)

    if mouth == "open":
        _r(c, ox, oy, 6, 10 + y0, 4, 2, DARK)
    elif mouth == "o":
        _r(c, ox, oy, 7, 10 + y0, 2, 2, DARK)
    elif mouth == "wave":
        _r(c, ox, oy, 4, 9 + y0, 8, 3, DARK)
        for i in range(7):
            bh = 1 + int(abs(math.sin(t * 12 + i * 0.9)) * 2)
            _r(c, ox, oy, 5 + i, 9 + y0 + (3 - bh), 1, bh,
               ACCENT if i % 2 == 0 else ACCENT2)
    elif mouth == "talk":
        _r(c, ox, oy, 6, 10 + y0, 4, 1, ACCENT2_DEEP)
    else:
        _r(c, ox, oy, 6, 10 + y0, 4, 1, ACCENT2_DEEP)


def _torso(c, ox, oy, t, bob=0, accent=None):
    y0 = bob
    accent = accent or ACCENT
    _r(c, ox, oy, 3, 12 + y0, 10, 8, DARK)
    _r(c, ox, oy, 4, 13 + y0, 8, 6, WHITE)
    glow = accent if (t * 1.5) % 2 < 1 else ACCENT_DIM
    _r(c, ox, oy, 7, 14 + y0, 2, 2, glow)
    _r(c, ox, oy, 4, 18 + y0, 8, 1, ACCENT2_DEEP)


def _legs(c, ox, oy, bob=0, stepping=0):
    lo = bob + (1 if stepping else 0)
    _r(c, ox, oy, 4, 20 + lo, 3, 5, DARK)
    _r(c, ox, oy, 9, 20 + bob - (1 if stepping else 0), 3, 5, DARK)
    _r(c, ox, oy, 5, 21 + lo, 1, 3, GRAY)
    _r(c, ox, oy, 10, 21 + bob - (1 if stepping else 0), 1, 3, GRAY)
    _r(c, ox, oy, 3, 24 + lo, 5, 2, DARK)
    _r(c, ox, oy, 8, 24 + bob - (1 if stepping else 0), 5, 2, DARK)


def _arms_down(c, ox, oy, bob=0):
    _r(c, ox, oy, 1, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 12, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 2, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 13, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 1, 19 + bob, 3, 2, ACCENT2)
    _r(c, ox, oy, 12, 19 + bob, 3, 2, ACCENT2)


def _arm_scratch(c, ox, oy, bob=0):
    _r(c, ox, oy, 1, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 2, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 1, 19 + bob, 3, 2, ACCENT2)
    _r(c, ox, oy, 12, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 13, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 12, 6 + bob, 3, 7, DARK)
    _r(c, ox, oy, 13, 7 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 12, 4 + bob, 3, 2, ACCENT2)


def _arm_listen(c, ox, oy, bob=0):
    _r(c, ox, oy, 12, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 13, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 12, 19 + bob, 3, 2, ACCENT2)
    _r(c, ox, oy, 1, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 2, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 1, 6 + bob, 3, 7, DARK)
    _r(c, ox, oy, 1, 7 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 1, 4 + bob, 3, 2, ACCENT2)


def _terminal(c, ox, oy, t, accent):
    _r(c, ox, oy, 0, 15, 16, 11, DARK)
    _r(c, ox, oy, 1, 16, 14, 9, (22, 24, 30))
    _r(c, ox, oy, 2, 17, 3, 1, accent)
    _r(c, ox, oy, 6, 17, 2, 1, ACCENT2)
    _r(c, ox, oy, 9, 17, 4, 1, GRAY)
    _r(c, ox, oy, 2, 19, 5, 1, GRAY)
    _r(c, ox, oy, 8, 19, 4, 1, accent)
    _r(c, ox, oy, 2, 21, 8, 1, accent)
    if (t * 4) % 2 < 1:
        _r(c, ox, oy, 11, 21, 2, 1, (255, 255, 255))
    _r(c, ox, oy, 2, 23, 6, 1, GRAY)


def _typing_hands(c, ox, oy, t, bob=0):
    shift = 1 if (t * 6) % 2 < 1 else 0
    _r(c, ox, oy, 2, 22 + shift, 3, 2, ACCENT2)
    _r(c, ox, oy, 11, 22 + (1 - shift), 3, 2, ACCENT2)


def _think_bubble(c, ox, oy, t, bob=0):
    _r(c, ox, oy, 13, -3 + bob, 2, 2, ACCENT_DIM)
    _r(c, ox, oy, 15, -7 + bob, 3, 3, ACCENT_DIM)
    _r(c, ox, oy, 17, -13 + bob, 8, 6, DARK)
    _r(c, ox, oy, 18, -12 + bob, 6, 4, VISOR)
    step = int(t * 3) % 4
    for i in range(step):
        _r(c, ox, oy, 19 + i * 2, -10 + bob, 1, 1, ACCENT)


def _sleep_zzz(c, ox, oy, t):
    for idx, (dx, dy, sc) in enumerate([(14, -4, 2), (18, -9, 3), (22, -15, 4)]):
        alpha = ((t * 0.8 + idx * 0.4) % 1.2)
        if alpha < 1.0:
            drift = int(alpha * 4)
            _r(c, ox, oy + drift, dx, dy, sc, 1, ACCENT_DIM)
            _r(c, ox, oy + drift, dx + sc - 1, dy + 1, 1, sc - 2, ACCENT_DIM)
            _r(c, ox, oy + drift, dx, dy + sc - 1, sc, 1, ACCENT_DIM)


def _draw_classic_avatar(canvas, state: str, t: float, mood: Optional[str] = None):
    _presence(canvas, t)
    ox = 640 - (GW * S) // 2
    oy = 452 - GH * S
    bob = int(round(math.sin(t * 2.2)))
    accent = ACCENT

    cv2.ellipse(canvas, (640, 458), (72, 12), 0, 0, 360, (28, 30, 38), -1)

    m = (mood or "").lower().strip()

    if state == "coding":
        accent = GREEN
        _legs(canvas, ox, oy, bob=0)
        _torso(canvas, ox, oy, t, bob=0, accent=accent)
        _head(canvas, ox, oy, t, eye="focus", mouth="smile", bob=0)
        _terminal(canvas, ox, oy, t, accent)
        _typing_hands(canvas, ox, oy, t, bob=0)
    elif state == "working":
        accent = AMBER
        _legs(canvas, ox, oy, bob=0)
        _torso(canvas, ox, oy, t, bob=0, accent=accent)
        _head(canvas, ox, oy, t, eye="focus", mouth="smile", bob=0)
        _terminal(canvas, ox, oy, t, accent)
        _typing_hands(canvas, ox, oy, t, bob=0)
    elif state == "thinking":
        _legs(canvas, ox, oy, bob=bob)
        _torso(canvas, ox, oy, t, bob=bob, accent=accent)
        _arm_scratch(canvas, ox, oy, bob=bob)
        _head(canvas, ox, oy, t, eye="sparkle", mouth="smile", bob=bob)
        _think_bubble(canvas, ox, oy, t, bob=bob)
    elif state == "speaking":
        bounce = int(round(abs(math.sin(t * 5.5))))
        _legs(canvas, ox, oy, bob=bounce)
        _torso(canvas, ox, oy, t, bob=bounce, accent=accent)
        _arms_down(canvas, ox, oy, bob=bounce)
        mouth_frame = "wave" if int(t * 8) % 3 == 0 else "talk"
        _head(canvas, ox, oy, t, eye="open", mouth=mouth_frame, bob=bounce)
    elif state == "listening":
        _legs(canvas, ox, oy, bob=0)
        _torso(canvas, ox, oy, t, bob=0, accent=accent)
        _arm_listen(canvas, ox, oy, bob=0)
        _head(canvas, ox, oy, t, eye="wide", mouth="o", bob=0)
    elif state == "excited":
        bounce = int(round(abs(math.sin(t * 10)))) * 2
        _legs(canvas, ox, oy, bob=bounce)
        _torso(canvas, ox, oy, t, bob=bounce, accent=accent)
        _arms_down(canvas, ox, oy, bob=bounce)
        _head(canvas, ox, oy, t, eye="sparkle", mouth="smile", bob=bounce, fast_pulse=True)
    else:
        _legs(canvas, ox, oy, bob=bob)
        _torso(canvas, ox, oy, t, bob=bob, accent=accent)
        _arms_down(canvas, ox, oy, bob=bob)
        _head(canvas, ox, oy, t, eye="open", mouth="smile", bob=bob)


# ---------------------------------------------------------------------------
# Primary Dispatcher
# ---------------------------------------------------------------------------
def draw_pixel_aria(canvas, state, t, mood: Optional[str] = None):
    """Draw the ARIA avatar based on selected style ('chassis' or 'classic')."""
    global _last_theme_check
    if t - _last_theme_check > 0.5:
        _last_theme_check = t
        load_theme()

    if _avatar_style == "classic":
        _draw_classic_avatar(canvas, state, t, mood=mood)
    else:
        _draw_chassis_avatar(canvas, state, t, mood=mood)
