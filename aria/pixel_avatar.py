"""Pixel-person avatar for the ARIA HUD face.

Procedural 16-bit-style pixel art drawn with cv2 rectangles on a virtual
pixel grid, scaled up with hard edges. One module, no aria imports (safe
to import from hud.py). Toggle with hud.USE_PIXEL_AVATAR.
"""
import math
import os
from datetime import datetime

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


def _apply_theme():
    global ACCENT, ACCENT_DIM, ACCENT2, ACCENT2_BRIGHT, ACCENT2_DEEP
    th = THEMES[_theme_name]
    ACCENT, ACCENT_DIM = th["accent"], th["accent_dim"]
    ACCENT2, ACCENT2_BRIGHT, ACCENT2_DEEP = (th["accent2"], th["accent2_bright"],
                                            th["accent2_deep"])


_apply_theme()


def get_theme() -> str:
    return _theme_name


def theme_colors():
    """(accent, accent2) for HUD chrome theming."""
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


def load_theme() -> str:
    try:
        with open(_THEME_FILE) as f:
            saved = f.read().strip()
        if saved in THEMES:
            set_theme(saved)
    except OSError:
        pass
    return _theme_name


def _r(c, ox, oy, gx, gy, gw, gh, color):
    """Fill a virtual-pixel rect on canvas."""
    x1, y1 = ox + gx * S, oy + gy * S
    cv2.rectangle(c, (x1, y1), (x1 + gw * S, y1 + gh * S), color, -1)


def _presence(c, t):
    """Ambient life in the face region: faint orbit ring + drifting motes."""
    cv2.ellipse(c, (640, 290), (135, 135), 0, 0, 360, (40, 60, 70), 1)
    cv2.ellipse(c, (640, 290), (100, 100), 0, 0, 360, (25, 38, 46), 1)
    # slow-drifting motes, deterministic
    for i in range(14):
        mx = 400 + int((i * 173.3 + t * (6 + i % 5)) % 480)
        my = 110 + int((i * 97.7 + t * (4 + i % 3)) % 330)
        s = 2 if i % 3 else 3
        col = ACCENT_DIM if i % 2 else (60, 90, 110)
        cv2.rectangle(c, (mx, my), (mx + s, my + s), col, -1)


def _head(c, ox, oy, t, eye="open", mouth="smile", bob=0, twitch=(0, 0),
          fast_pulse=False):
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
    # visor
    _r(c, ox, oy, 3, 4 + y0, 10, 6, DARK)
    _r(c, ox, oy, 4, 5 + y0, 8, 4, VISOR)
    # eyes
    if eye == "blink":
        _r(c, ox, oy, 5, 6 + y0, 2, 1, ACCENT)
        _r(c, ox, oy, 9, 6 + y0, 2, 1, ACCENT)
    elif eye == "happy":
        _r(c, ox, oy, 5, 5 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 2, ACCENT)
    elif eye == "wide":
        _r(c, ox, oy, 5, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 5, 5 + y0, 2, 1, (255, 255, 255))
        _r(c, ox, oy, 9, 5 + y0, 2, 1, (255, 255, 255))
    elif eye == "up":  # thinking: glancing up
        _r(c, ox, oy, 5, 5 + y0, 2, 3, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 3, ACCENT)
    elif eye == "sleepy":  # late-night half-mast lids
        _r(c, ox, oy, 5, 6 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 9, 6 + y0, 2, 2, ACCENT)
        _r(c, ox, oy, 5, 6 + y0, 2, 1, VISOR)
        _r(c, ox, oy, 9, 6 + y0, 2, 1, VISOR)
    else:  # open
        _r(c, ox, oy, 5, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 9, 5 + y0, 2, 4, ACCENT)
        _r(c, ox, oy, 5, 5 + y0, 1, 1, (255, 255, 255))
        _r(c, ox, oy, 9, 5 + y0, 1, 1, (255, 255, 255))
    # mouth (chin plate)
    if mouth == "open":
        _r(c, ox, oy, 6, 10 + y0, 4, 2, DARK)
    elif mouth == "o":
        _r(c, ox, oy, 7, 10 + y0, 2, 2, DARK)
    elif mouth == "wave":
        # tiny pixel waveform on her mouth while speaking
        _r(c, ox, oy, 4, 9 + y0, 8, 3, DARK)        # cavity
        for i in range(7):
            bh = 1 + int(abs(math.sin(t * 12 + i * 0.9)) * 2)
            _r(c, ox, oy, 5 + i, 9 + y0 + (3 - bh), 1, bh,
               ACCENT if i % 2 == 0 else ACCENT2)
    elif mouth == "talk":
        # smile flashes bright while speaking
        _r(c, ox, oy, 6, 10 + y0, 4, 1,
           ACCENT2_BRIGHT if (t * 6) % 2 < 1 else ACCENT2_DEEP)
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
    _r(c, ox, oy, 1, 19 + bob, 3, 2, ACCENT2)   # gloves
    _r(c, ox, oy, 12, 19 + bob, 3, 2, ACCENT2)


def _arm_scratch(c, ox, oy, bob=0):
    # left arm down, right arm raised to head
    _r(c, ox, oy, 1, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 2, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 1, 19 + bob, 3, 2, ACCENT2)
    _r(c, ox, oy, 12, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 13, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 12, 6 + bob, 3, 7, DARK)   # raised segment
    _r(c, ox, oy, 13, 7 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 12, 4 + bob, 3, 2, ACCENT2)   # hand at head


def _arm_listen(c, ox, oy, bob=0):
    # right arm down, left hand cupped to ear
    _r(c, ox, oy, 12, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 13, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 12, 19 + bob, 3, 2, ACCENT2)
    _r(c, ox, oy, 1, 13 + bob, 3, 7, DARK)
    _r(c, ox, oy, 2, 14 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 1, 6 + bob, 3, 7, DARK)
    _r(c, ox, oy, 1, 7 + bob, 1, 5, WHITE)
    _r(c, ox, oy, 1, 4 + bob, 3, 2, ACCENT2)


def _terminal(c, ox, oy, t, accent):
    # hologram keyboard in front of her + floating holo screen to her right
    _r(c, ox, oy, 0, 22, 16, 2, DARK)
    _r(c, ox, oy, 1, 22, 14, 1, accent)
    for kx in range(2, 14, 2):
        _r(c, ox, oy, kx, 23, 1, 1, GRAY_DK)
    # floating holo screen (outline style) at her right
    sx, sy, sw, sh = 18, 5, 10, 8
    _r(c, ox, oy, sx, sy, sw, sh, accent)
    _r(c, ox, oy, sx + 1, sy + 1, sw - 2, sh - 2, (8, 10, 14))
    for i in range(3):
        on = (t * 3 + i) % 2 < 1
        _r(c, ox, oy, sx + 2, sy + 2 + i * 2, sw - 4 - i, 1,
           accent if on else ACCENT_DIM)
    # data particles rise off the screen
    for i in range(8):
        rise = (t * 8 + i * 5.1) % 12
        px_ = sx + 1 + int((i * 3.7) % (sw - 2))
        py = int(sy + sh - 1 - rise)
        if py > -6:
            _r(c, ox, oy, px_, py, 1, 1, accent)


def _typing_hands(c, ox, oy, t, bob=0):
    # arms reach to keyboard, hands alternate
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
    _r(c, ox, oy, bx + 1, by + 6, 2, 2, DARK)  # tail toward head


def _zzz(c, ox, oy, t):
    for i in range(3):
        zx = 14 + i * 2 + int(t * 2 + i) % 2
        zy = -10 - i * 4 + int((t * 2 + i * 1.7) % 4)
        _r(c, ox, oy, zx, zy, 1, 1, ACCENT_DIM)


def _sleep_zzz(c, ox, oy, t):
    """Two little Z's drifting up beside her head while she's sleepy."""
    for i in range(2):
        rise = (t * 2.5 + i * 5) % 5
        zx, zy = 15 + i * 6, -2 - int(rise)
        _r(c, ox, oy, zx, zy, 3, 1, ACCENT_DIM)
        _r(c, ox, oy, zx + 1, zy + 1, 1, 1, ACCENT_DIM)
        _r(c, ox, oy, zx, zy + 2, 3, 1, ACCENT_DIM)


def draw_pixel_aria(canvas, state, t):
    """Draw the pixel-person avatar centered on the HUD face region."""
    _presence(canvas, t)
    ox = 640 - (GW * S) // 2
    oy = 452 - GH * S  # feet land ~y=452
    bob = int(round(math.sin(t * 2.2)))
    accent = ACCENT

    # ground shadow
    cv2.ellipse(canvas, (640, 458), (72, 12), 0, 0, 360, (28, 30, 38), -1)

    if state == "coding":
        accent = GREEN
        _legs(canvas, ox, oy, bob=0)
        _torso(canvas, ox, oy, t, bob=0, accent=accent)
        _head(canvas, ox, oy, t, eye="open", mouth="smile", bob=0)
        _terminal(canvas, ox, oy, t, accent)
        _typing_hands(canvas, ox, oy, t, bob=0)
    elif state == "working":
        accent = AMBER
        _legs(canvas, ox, oy, bob=0)
        _torso(canvas, ox, oy, t, bob=0, accent=accent)
        _head(canvas, ox, oy, t, eye="open", mouth="smile", bob=0)
        _terminal(canvas, ox, oy, t, accent)
        _typing_hands(canvas, ox, oy, t, bob=0)
    elif state == "thinking":
        _legs(canvas, ox, oy, bob=bob)
        _torso(canvas, ox, oy, t, bob=bob, accent=accent)
        _arm_scratch(canvas, ox, oy, bob=bob)
        _head(canvas, ox, oy, t, eye="up", mouth="smile", bob=bob)
        _think_bubble(canvas, ox, oy, t, bob=bob)
    elif state == "speaking":
        bounce = int(round(abs(math.sin(t * 6))))
        _legs(canvas, ox, oy, bob=bounce)
        _torso(canvas, ox, oy, t, bob=bounce, accent=accent)
        _arms_down(canvas, ox, oy, bob=bounce)
        _head(canvas, ox, oy, t, eye="happy", mouth="talk", bob=bounce)
    elif state == "listening":
        lean = int(round(math.sin(t * 1.4)))
        # ear twitch: every 4s one antenna flicks for a beat, alternating sides
        cyc = t % 4.0
        tw = (0, 0)
        if cyc < 0.35:
            tw = (-2, 0) if int(t // 4) % 2 == 0 else (0, 2)
        _legs(canvas, ox, oy, bob=0)
        _torso(canvas, ox, oy, t, bob=0, accent=accent)
        _arm_listen(canvas, ox, oy, bob=0)
        _head(canvas, ox, oy, t + lean, eye="wide", mouth="o", bob=0, twitch=tw)
    elif state == "excited":
        # wake-up burst: big fast bounce, happy eyes, antennae pulse fast
        bounce = int(round(abs(math.sin(t * 10)))) * 2
        _legs(canvas, ox, oy, bob=bounce)
        _torso(canvas, ox, oy, t, bob=bounce, accent=accent)
        _arms_down(canvas, ox, oy, bob=bounce)
        _head(canvas, ox, oy, t, eye="happy", mouth="smile", bob=bounce,
              fast_pulse=True)
    else:  # idle
        hour = datetime.now().hour
        sleepy = hour >= 23 or hour < 6
        blink = (t % (6.0 if sleepy else 3.7)) < (0.15 if sleepy else 0.13)
        eye = "blink" if blink else ("sleepy" if sleepy else "open")
        _legs(canvas, ox, oy, bob=bob)
        _torso(canvas, ox, oy, t, bob=bob, accent=accent)
        _arms_down(canvas, ox, oy, bob=bob)
        _head(canvas, ox, oy, t, eye=eye, mouth="smile", bob=bob)
        if sleepy:
            _sleep_zzz(canvas, ox, oy, t)
