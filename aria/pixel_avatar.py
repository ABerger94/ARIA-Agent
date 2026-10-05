"""Pixel-person avatar for the ARIA HUD face.

Procedural 16-bit-style pixel art drawn with cv2 rectangles on a virtual
pixel grid, scaled up with hard edges. One module, no aria imports (safe
to import from hud.py). Toggle with hud.USE_PIXEL_AVATAR.
"""
import math
import os
import time
from datetime import datetime
from typing import Optional

import cv2

# Palette (BGR)
WHITE = (232, 232, 240)
PINK = (150, 80, 255)
PINK_DEEP = (95, 45, 185)
PINK_BRIGHT = (190, 140, 255)
DARK = (12, 12, 18)
VISOR = (28, 28, 44)
CYAN = (255, 255, 0)
ACCENT_DIM = (170, 170, 0)
GREEN = (40, 240, 120)
AMBER = (0, 180, 255)
GRAY = (120, 120, 132)
GRAY_DK = (70, 70, 80)

S = 9            # pixel scale: one virtual pixel = 9 screen px
GW, GH = 16, 34  # virtual grid size (antenna tip at y=-7 .. feet at y=26)

# Color themes: accent (eyes/tips/core/chrome), accent2 (blush/mouth/gloves)
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

_theme_name = "midnight"
ACCENT, ACCENT_DIM = CYAN, ACCENT_DIM
ACCENT2, ACCENT2_BRIGHT, ACCENT2_DEEP = PINK, PINK_BRIGHT, PINK_DEEP


def _apply_theme():
    global ACCENT, ACCENT_DIM, ACCENT2, ACCENT2_BRIGHT, ACCENT2_DEEP
    th = THEMES[_theme_name]
    ACCENT, ACCENT_DIM = th["accent"], th["accent_dim"]
    ACCENT2, ACCENT2_BRIGHT, ACCENT2_DEEP = (th["accent2"], th["accent2_bright"],
                                            th["accent2_deep"])


_apply_theme()


def get_theme() -> str:
    return _theme_name


_last_theme_check = 0.0


def load_theme() -> str:
    global _theme_name
    try:
        with open(_THEME_FILE) as f:
            saved = f.read().strip()
        if saved in THEMES and saved != _theme_name:
            _theme_name = saved
            _apply_theme()
    except OSError:
        pass
    return _theme_name


def theme_colors():
    """(accent, accent2) for HUD chrome theming."""
    global _last_theme_check
    now = time.time()
    if now - _last_theme_check > 0.5:
        _last_theme_check = now
        load_theme()
    return ACCENT, ACCENT2


def set_theme(name: str) -> str:
    global _theme_name
    if name in THEMES:
        _theme_name = name
        _apply_theme()
        try:
            with open(_THEME_FILE, "w") as f:
                f.write(name)
        except OSError:
            pass
    return _theme_name


def cycle_theme() -> str:
    nxt = _THEME_ORDER[(_THEME_ORDER.index(_theme_name) + 1) % len(_THEME_ORDER)]
    return set_theme(nxt)


def _r(c, ox, oy, gx, gy, gw, gh, color):
    """Fill a virtual-pixel rect on canvas."""
    x1, y1 = ox + gx * S, oy + gy * S
    cv2.rectangle(c, (x1, y1), (x1 + gw * S, y1 + gh * S), color, -1)


def _presence(c, t):
    """Ambient life in the face region: faint orbit ring + drifting motes."""
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
    # twin antennae (like ears), tips pulse in sync; twitch offsets one side
    tip = ACCENT if (t * (6 if fast_pulse else 2)) % 2 < 1 else ACCENT2
    _r(c, ox, oy, 3 + twitch[0], -5 + y0, 2, 5, GRAY_DK)
    _r(c, ox, oy, 11 + twitch[1], -5 + y0, 2, 5, GRAY_DK)
    _r(c, ox, oy, 2 + twitch[0], -7 + y0, 3, 2, tip)
    _r(c, ox, oy, 11 + twitch[1], -7 + y0, 3, 2, tip)
    
    # helmet shell
    _r(c, ox, oy, 0, 0 + y0, 16, 12, DARK)
    _r(c, ox, oy, 1, 1 + y0, 14, 10, WHITE)
    # helmet top highlight
    _r(c, ox, oy, 3, 1 + y0, 5, 1, (255, 255, 255))
    # side pods (ears)
    _r(c, ox, oy, 0, 5 + y0, 3, 4, DARK)
    _r(c, ox, oy, 13, 5 + y0, 3, 4, DARK)
    _r(c, ox, oy, 0, 6 + y0, 2, 2, ACCENT2)
    _r(c, ox, oy, 14, 6 + y0, 2, 2, ACCENT2)
    
    # visor background
    _r(c, ox, oy, 3, 4 + y0, 10, 6, DARK)
    _r(c, ox, oy, 4, 5 + y0, 8, 4, VISOR)

    # Eyes rendering
    if eye == "blink":
        _r(c, ox, oy, 5, 6 + y0, 2, 1, ACCENT)
        _r(c, ox, oy, 9, 6 + y0, 2, 1, ACCENT)
    elif eye == "happy":
        _r(c, ox, oy, 5, 5 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 5, 7 + y0, 2, 1, VISOR)
        _r(c, ox, oy, 9, 7 + y0, 2, 1, VISOR)
    elif eye == "wide":
        _r(c, ox, oy, 5, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 5, 5 + y0, 2, 1, (255, 255, 255))
        _r(c, ox, oy, 9, 5 + y0, 2, 1, (255, 255, 255))
    elif eye == "up":  # thinking: glancing up
        _r(c, ox, oy, 5, 5 + y0, 2, 3, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 3, ACCENT)
        _r(c, ox, oy, 5, 5 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 9, 5 + y0, 1, 1, (255, 255, 255))
    elif eye == "sleepy":  # late-night half-mast lids
        _r(c, ox, oy, 5, 6 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 9, 6 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 5, 6 + y0, 2, 1, VISOR)
        _r(c, ox, oy, 9, 6 + y0, 2, 1, VISOR)
    elif eye == "sparkle":  # energetic / inspired star sparkle
        _r(c, ox, oy, 5, 5 + y0, 2, 3, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 3, ACCENT)
        _r(c, ox, oy, 4, 6 + y0, 4, 1, (255, 255, 255))
        _r(c, ox, oy, 8, 6 + y0, 4, 1, (255, 255, 255))
    elif eye == "focus":  # analytical / coding intense concentration slit
        _r(c, ox, oy, 4, 6 + y0, 3, 2, ACCENT)
        _r(c, ox, oy, 9, 6 + y0, 3, 2, ACCENT)
        _r(c, ox, oy, 5, 6 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 10, 6 + y0, 1, 1, (255, 255, 255))
    elif eye == "skeptical":  # one raised brow, blunt direct stare
        _r(c, ox, oy, 5, 7 + y0, 2, 1, ACCENT)      # left eye squinted
        _r(c, ox, oy, 9, 5 + y0, 2, 3, ACCENT)      # right eye raised
        _r(c, ox, oy, 9, 4 + y0, 2, 1, (255, 255, 255))  # raised brow pixel
    elif eye == "glance_left":  # looking left
        _r(c, ox, oy, 4, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 8, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 4, 5 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 8, 5 + y0, 1, 1, (255, 255, 255))
    elif eye == "glance_right":  # looking right
        _r(c, ox, oy, 6, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 10, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 7, 5 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 11, 5 + y0, 1, 1, (255, 255, 255))
    elif eye == "wink":  # left open, right wink
        _r(c, ox, oy, 5, 5 + y0, 2, 3, ACCENT)
        _r(c, ox, oy, 5, 5 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 9, 6 + y0, 3, 1, ACCENT2)
    else:  # open
        _r(c, ox, oy, 5, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 5, 5 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 9, 5 + y0, 1, 1, (255, 255, 255))

    # Optional blush dots
    if blush:
        _r(c, ox, oy, 3, 8 + y0, 2, 1, ACCENT2_BRIGHT)
        _r(c, ox, oy, 11, 8 + y0, 2, 1, ACCENT2_BRIGHT)

    # Mouth rendering
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
        _r(c, ox, oy, 6, 10 + y0, 4, 1,
           ACCENT2_BRIGHT if (t * 6) % 2 < 1 else ACCENT2_DEEP)
    elif mouth == "smirk":
        _r(c, ox, oy, 7, 10 + y0, 3, 1, ACCENT2_DEEP)
        _r(c, ox, oy, 9, 9 + y0, 1, 1, ACCENT2_DEEP)
    elif mouth == "flat":
        _r(c, ox, oy, 6, 10 + y0, 4, 1, GRAY)
    else:  # smile
        _r(c, ox, oy, 6, 10 + y0, 4, 1, ACCENT2_DEEP)


def _torso(c, ox, oy, t, bob=0, accent=None):
    y0 = bob
    accent = accent or ACCENT
    _r(c, ox, oy, 3, 12 + y0, 10, 8, DARK)
    _r(c, ox, oy, 4, 13 + y0, 8, 6, WHITE)
    # chest core light (breathes)
    glow = accent if (t * 1.5) % 2 < 1 else ACCENT_DIM
    _r(c, ox, oy, 7, 14 + y0, 2, 2, glow)
    # belt
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
    _r(c, ox, oy, 0, 22, 16, 2, DARK)
    _r(c, ox, oy, 1, 22, 14, 1, accent)
    for kx in range(2, 14, 2):
        _r(c, ox, oy, kx, 23, 1, 1, GRAY_DK)
    sx, sy, sw, sh = 18, 5, 10, 8
    _r(c, ox, oy, sx, sy, sw, sh, accent)
    _r(c, ox, oy, sx + 1, sy + 1, sw - 2, sh - 2, (8, 10, 14))
    for i in range(3):
        on = (t * 3 + i) % 2 < 1
        _r(c, ox, oy, sx + 2, sy + 2 + i * 2, sw - 4 - i, 1,
           accent if on else ACCENT_DIM)
    for i in range(8):
        rise = (t * 8 + i * 5.1) % 12
        px_ = sx + 1 + int((i * 3.7) % (sw - 2))
        py = int(sy + sh - 1 - rise)
        if py > -6:
            _r(c, ox, oy, px_, py, 1, 1, accent)


def _typing_hands(c, ox, oy, t, bob=0):
    _r(c, ox, oy, 2, 15 + bob, 3, 6, DARK)
    _r(c, ox, oy, 11, 15 + bob, 3, 6, DARK)
    _r(c, ox, oy, 3, 16 + bob, 1, 4, WHITE)
    _r(c, ox, oy, 12, 16 + bob, 1, 4, WHITE)
    alt = int(t * 9) % 2
    _r(c, ox, oy, 5, 20 + bob - alt, 2, 2, ACCENT2)
    _r(c, ox, oy, 9, 20 + bob - (1 - alt), 2, 2, ACCENT2)


def _think_bubble(c, ox, oy, t, bob=0):
    bx, by = 14, -8 + bob
    _r(c, ox, oy, bx, by, 9, 6, DARK)
    _r(c, ox, oy, bx + 1, by + 1, 7, 4, WHITE)
    for i in range(3):
        lift = int(abs(math.sin(t * 4 + i * 1.1)) * 1)
        _r(c, ox, oy, bx + 2 + i * 2, by + 3 - lift, 1, 1, DARK)
    _r(c, ox, oy, bx + 1, by + 6, 2, 2, DARK)


def _sleep_zzz(c, ox, oy, t):
    for i in range(2):
        rise = (t * 2.5 + i * 5) % 5
        zx, zy = 15 + i * 8, -4 - int(rise)
        _r(c, ox, oy, zx, zy, 4, 1, ACCENT_DIM)
        _r(c, ox, oy, zx + 3, zy + 1, 1, 1, ACCENT_DIM)
        _r(c, ox, oy, zx + 2, zy + 2, 1, 1, ACCENT_DIM)
        _r(c, ox, oy, zx + 1, zy + 3, 1, 1, ACCENT_DIM)
        _r(c, ox, oy, zx, zy + 4, 4, 1, ACCENT_DIM)


_last_state = "idle"
_state_change_time = 0.0


def draw_pixel_aria(canvas, state, t, mood: Optional[str] = None):
    """Draw the pixel-person avatar with fluid cognitive-state expressions."""
    global _last_theme_check, _last_state, _state_change_time
    if t - _last_theme_check > 0.5:
        _last_theme_check = t
        load_theme()

    if state != _last_state:
        _last_state = state
        _state_change_time = t

    state_dur = max(0.0, t - _state_change_time)

    _presence(canvas, t)
    ox = 640 - (GW * S) // 2
    oy = 452 - GH * S
    bob = int(round(math.sin(t * 2.2)))
    accent = ACCENT

    # Ground shadow
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
        # Fluid cognitive phases during thinking
        if state_dur < 1.4:
            eye_mode = "up"
        elif state_dur < 4.2:
            eye_mode = "glance_left" if int(t * 1.8) % 2 == 0 else "glance_right"
        else:
            eye_mode = "sparkle"

        _legs(canvas, ox, oy, bob=bob)
        _torso(canvas, ox, oy, t, bob=bob, accent=accent)
        _arm_scratch(canvas, ox, oy, bob=bob)
        _head(canvas, ox, oy, t, eye=eye_mode, mouth="smile", bob=bob)
        _think_bubble(canvas, ox, oy, t, bob=bob)
    elif state == "speaking":
        # Conversational speech cadence
        bounce = int(round(abs(math.sin(t * 5.5))))
        mouth_mode = "wave" if (int(t * 8) % 4 != 0) else "talk"
        blink = (t % 3.2) < 0.12
        eye_mode = "blink" if blink else "happy"

        _legs(canvas, ox, oy, bob=bounce)
        _torso(canvas, ox, oy, t, bob=bounce, accent=accent)
        _arms_down(canvas, ox, oy, bob=bounce)
        _head(canvas, ox, oy, t, eye=eye_mode, mouth=mouth_mode, bob=bounce)
    elif state == "listening":
        lean = int(round(math.sin(t * 1.4)))
        cyc = t % 4.0
        tw = (0, 0)
        if cyc < 0.35:
            tw = (-2, 0) if int(t // 4) % 2 == 0 else (0, 2)
        _legs(canvas, ox, oy, bob=0)
        _torso(canvas, ox, oy, t, bob=0, accent=accent)
        _arm_listen(canvas, ox, oy, bob=0)
        _head(canvas, ox, oy, t + lean, eye="wide", mouth="o", bob=0, twitch=tw)
    elif state == "excited":
        bounce = int(round(abs(math.sin(t * 10)))) * 2
        _legs(canvas, ox, oy, bob=bounce)
        _torso(canvas, ox, oy, t, bob=bounce, accent=accent)
        _arms_down(canvas, ox, oy, bob=bounce)
        _head(canvas, ox, oy, t, eye="sparkle", mouth="smile", bob=bounce,
              fast_pulse=True)
    else:  # idle - deeply responsive to cognitive mood and time
        hour = datetime.now().hour
        is_late = hour >= 23 or hour < 6
        is_sleepy = ("sleepy" in m) or ("tired" in m) or is_late
        is_curious = ("curious" in m) or ("inquisitive" in m)
        is_playful = ("playful" in m) or ("cheerful" in m)
        is_focused = ("focused" in m) or ("analytical" in m)
        is_skeptical = ("skeptical" in m) or ("blunt" in m) or ("direct" in m)

        twitch = (0, 0)
        blush = False

        if is_sleepy:
            slow_bob = int(round(math.sin(t * 1.2)))
            blink = (t % 6.0) < 0.35
            eye_mode = "blink" if blink else "sleepy"
            mouth_mode = "smile"
            _legs(canvas, ox, oy, bob=slow_bob)
            _torso(canvas, ox, oy, t, bob=slow_bob, accent=accent)
            _arms_down(canvas, ox, oy, bob=slow_bob)
            _head(canvas, ox, oy, t, eye=eye_mode, mouth=mouth_mode, bob=slow_bob)
            _sleep_zzz(canvas, ox, oy, t)
        elif is_curious:
            glance_cycle = int(t // 2.5) % 3
            if glance_cycle == 0:
                eye_mode = "glance_left"
                twitch = (-1, 0)
            elif glance_cycle == 1:
                eye_mode = "glance_right"
                twitch = (0, 1)
            else:
                eye_mode = "wide"
            blink = (t % 3.6) < 0.12
            if blink:
                eye_mode = "blink"
            _legs(canvas, ox, oy, bob=bob)
            _torso(canvas, ox, oy, t, bob=bob, accent=accent)
            _arms_down(canvas, ox, oy, bob=bob)
            _head(canvas, ox, oy, t, eye=eye_mode, mouth="o", bob=bob, twitch=twitch)
        elif is_playful:
            is_wink = (int(t // 4) % 3 == 0) and ((t % 4.0) < 1.0)
            eye_mode = "wink" if is_wink else "happy"
            mouth_mode = "smirk"
            blush = True
            play_bob = int(round(abs(math.sin(t * 3.5))))
            _legs(canvas, ox, oy, bob=play_bob)
            _torso(canvas, ox, oy, t, bob=play_bob, accent=accent)
            _arms_down(canvas, ox, oy, bob=play_bob)
            _head(canvas, ox, oy, t, eye=eye_mode, mouth=mouth_mode, bob=play_bob, blush=blush)
        elif is_skeptical:
            blink = (t % 4.2) < 0.12
            eye_mode = "blink" if blink else "skeptical"
            mouth_mode = "flat"
            _legs(canvas, ox, oy, bob=bob)
            _torso(canvas, ox, oy, t, bob=bob, accent=accent)
            _arms_down(canvas, ox, oy, bob=bob)
            _head(canvas, ox, oy, t, eye=eye_mode, mouth=mouth_mode, bob=bob)
        elif is_focused:
            blink = (t % 5.0) < 0.1
            eye_mode = "blink" if blink else "focus"
            mouth_mode = "flat"
            _legs(canvas, ox, oy, bob=0)
            _torso(canvas, ox, oy, t, bob=0, accent=accent)
            _arms_down(canvas, ox, oy, bob=0)
            _head(canvas, ox, oy, t, eye=eye_mode, mouth=mouth_mode, bob=0)
        else:  # calm
            blink = (t % 3.8) < 0.12
            eye_mode = "blink" if blink else "open"
            mouth_mode = "smile"
            _legs(canvas, ox, oy, bob=bob)
            _torso(canvas, ox, oy, t, bob=bob, accent=accent)
            _arms_down(canvas, ox, oy, bob=bob)
            _head(canvas, ox, oy, t, eye=eye_mode, mouth=mouth_mode, bob=bob)
