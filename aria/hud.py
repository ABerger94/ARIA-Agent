"""
ARIA HUD (Heads-Up Display) and Visual Interface.
Renders the 1280x720 cybernetic interface, animated cyber-girl face,
waveform mouth, system telemetry, optical PIP, and commands overlay.
"""

from __future__ import annotations

import math
import os
import random
import socket
import textwrap
import threading
import time
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any

import cv2
import numpy as np
import psutil
from aria.pixel_avatar import draw_pixel_aria

from aria.config import PHONE_BRIDGE_PORT, GITHUB_USERNAME, GITHUB_TOKEN
from aria.vision import publish_face_frame, LATEST_CAMERA_FRAME
from aria.tools.schemas import COMMAND_GUIDE

_HUD_SUBS = {
    "•": "-", "—": "-", "–": "-", "°": "deg",
    "→": "->", "←": "<-", "“": '"', "”": '"',
    "‘": "'", "’": "'", "…": "..."
}

def hud_ascii(s: Any) -> str:
    """Map Unicode characters to ASCII lookalikes so OpenCV Hershey fonts never render '?'."""
    res = str(s)
    for k, v in _HUD_SUBS.items():
        res = res.replace(k, v)
    return res.encode("ascii", "replace").decode("ascii")


def lan_ip() -> str:
    """Find local network IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


# Visual Palette (BGR)
CYAN = (255, 220, 30)
GLOW = (120, 90, 10)
AMBER = (30, 160, 255)
GREEN = (40, 240, 120)
BORDER = (45, 50, 60)
PANEL_BG = (15, 17, 22)
WHITE_TEXT = (235, 242, 255)
PINK = (170, 90, 255)
MOUTH_YELLOW = (0, 220, 255)
PINK_DEEP = (110, 45, 190)
LINER = (70, 25, 120)
DIM = (150, 160, 170)

# Buttons
_WHISPER_BTN = (580, 52, 105, 22)
_INPUT_BAR = (35, 646, 1210, 28)
_COMMANDS_PREV_BTN = (560, 656, 110, 28)
_COMMANDS_NEXT_BTN = (810, 656, 110, 28)
_COMMANDS_CLOSE_BTN = (1110, 656, 105, 28)
_COMMANDS_X_BTN = (1205, 60, 28, 24)

# Directive Input Bar & Action Buttons
_INPUT_PASTE_BTN = (965, 648, 70, 24)
_INPUT_SEND_BTN = (1041, 648, 66, 24)
_INPUT_CLEAR_BTN = (1113, 648, 54, 24)
_INPUT_ESC_BTN = (1173, 648, 66, 24)


def get_clipboard_text() -> str:
    """Safely fetch plain text from Windows clipboard via win32 or tkinter."""
    try:
        import win32clipboard
        import win32con
        for _ in range(3):
            try:
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                        data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                        return data if isinstance(data, str) else ""
                    return ""
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                time.sleep(0.02)
    except Exception:
        pass
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        try:
            return r.clipboard_get()
        except Exception:
            return ""
        finally:
            r.destroy()
    except Exception:
        pass
    return ""


# State tracking
CURRENT_STATE = "idle"
HUD_MODE = "visor"  # "visor" or "chat_log"
CHAT_SCROLL = 0
USE_PIXEL_AVATAR = True  # pixel-person face; set False to restore v9.34 eyes
SHOW_COMMANDS = False
TYPING_ACTIVE: bool = False
TYPING_BUFFER: str = ""
COMMANDS_PAGE = 0
COMMANDS_PAGES: List[Any] = []
SUBTITLE_TEXT = ""
LOG_STREAM: List[str] = []
DISPLAY_CHAT_LOG: List[Tuple[str, str, str]] = []

WHISPER_MODE = False

_FACE = {
    "eye_dx": 0.0, "eye_dy": 0.0,
    "eye_tdx": 0.0, "eye_tdy": 0.0,
    "next_glance": 0.0,
    "blink_until": 0.0,
    "next_blink": 0.0,
}

_MOOD_CALLBACK = None
_SUBSYSTEMS_CALLBACK = None


def set_mood_callback(fn):
    global _MOOD_CALLBACK
    _MOOD_CALLBACK = fn


def set_subsystems_callback(fn):
    global _SUBSYSTEMS_CALLBACK
    _SUBSYSTEMS_CALLBACK = fn


def add_hud_log(msg: str):
    msg = hud_ascii(msg)
    LOG_STREAM.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    if len(LOG_STREAM) > 8:
        LOG_STREAM.pop(0)


def set_hud_state(state: str):
    global CURRENT_STATE
    prev = CURRENT_STATE
    CURRENT_STATE = state
    return prev


def set_hud_subtitle(text: str):
    global SUBTITLE_TEXT
    SUBTITLE_TEXT = hud_ascii(text)


def apply_led_scanlines(canvas: np.ndarray, x1: int, y1: int, x2: int, y2: int):
    for y in range(max(0, y1), min(canvas.shape[0], y2), 4):
        canvas[y, max(0, x1):min(canvas.shape[1], x2)] =             canvas[y, max(0, x1):min(canvas.shape[1], x2)] // 2


def _update_idle_face(now: float):
    """Advance idle-face animation state. Pure timing/state -- no drawing."""
    f = _FACE
    if f["next_glance"] == 0.0:  # first call: stagger the timers
        f["next_glance"] = now + 0.5
        f["next_blink"] = now + random.uniform(2.5, 4.0)
    # Mood biases idle tempo only — never expression meaning. Sleepy drifts
    # narrower and blinks slower; bright/playful glances wider, blinks faster.
    _mw = (_MOOD_CALLBACK().lower() if _MOOD_CALLBACK else "calm")
    _gx = 12 if _mw == "sleepy" else 26 if _mw in ("bright", "playful") else 22
    _br = ((4.5, 9.0) if _mw == "sleepy" else (2.5, 6.0)
           if _mw in ("bright", "playful") else (3.0, 7.0))
    if now >= f["next_glance"]:
        f["eye_tdx"] = random.uniform(-_gx, _gx)
        f["eye_tdy"] = random.uniform(-16, 16)
        f["next_glance"] = now + random.uniform(2.0, 5.0)
    f["eye_dx"] += (f["eye_tdx"] - f["eye_dx"]) * 0.18  # ease, ~10 fps
    f["eye_dy"] += (f["eye_tdy"] - f["eye_dy"]) * 0.18
    if now >= f["next_blink"]:
        f["blink_until"] = now + 0.18
        f["next_blink"] = now + random.uniform(*_br)


def _blink_squash(now: float) -> float:
    """Vertical eye scale: 1.0 normally, dips toward 0.08 mid-blink."""
    if now < _FACE["blink_until"]:
        t = 1.0 - (_FACE["blink_until"] - now) / 0.18
        return max(0.08, abs(math.cos(t * math.pi)))
    return 1.0


def _draw_lashes(canvas: np.ndarray, ex: int, cy: int, ew: int, eh: int, side: int):
    """Three lash flicks fanning from the upper-outer quadrant of an eye."""
    angs = (200, 220, 240) if side < 0 else (340, 320, 300)
    for a in angs:
        r = math.radians(a)
        x0 = int(ex + ew * math.cos(r))
        y0 = int(cy + eh * math.sin(r))
        x1 = int(ex + (ew + 16) * math.cos(r))
        y1 = int(cy + (eh + 16) * math.sin(r))
        cv2.line(canvas, (x0, y0), (x1, y1), PINK, 2)


def _draw_waveform_mouth(canvas: np.ndarray, color: Tuple[int, int, int], t: float,
                         cx: int = 640, my: int = 388, bars: int = 19, spacing: int = 14,
                         amp: int = 10, thick: int = 2):
    """Soft idle-style waveform ripple: calm amplitude so it reads as a resting
    mouth, not speech. Animated with time t."""
    for i in range(-(bars // 2), bars // 2 + 1):
        bar_x = cx + (i * spacing)
        bar_h = int(abs(np.sin(t * 2.0 + i * 0.5)) * amp) + 2
        cv2.line(canvas, (bar_x, my - bar_h), (bar_x, my + bar_h), color, thick)


def _build_commands_pages():
    rows = []
    last_cat = None
    for cat, tool, ex in COMMAND_GUIDE:
        if cat != last_cat:
            rows.append(("cat", cat, ""))
            last_cat = cat
        rows.append(("cmd", tool, ex))
    ROWS_PER_COL = 24
    pages = []
    for i in range(0, len(rows), ROWS_PER_COL * 2):
        chunk = rows[i:i + ROWS_PER_COL * 2]
        pages.append((chunk[:ROWS_PER_COL], chunk[ROWS_PER_COL:]))
    return pages


def _draw_commands_overlay(canvas: np.ndarray):
    global COMMANDS_PAGES, COMMANDS_PAGE
    if not COMMANDS_PAGES:
        COMMANDS_PAGES = _build_commands_pages()
    dim = canvas.copy()
    cv2.rectangle(dim, (0, 0), (1280, 720), (8, 10, 14), -1)
    cv2.addWeighted(dim, 0.88, canvas, 0.12, 0, canvas)
    cv2.rectangle(canvas, (36, 52), (1244, 700), (15, 17, 22), -1)
    cv2.rectangle(canvas, (36, 52), (1244, 700), (45, 50, 60), 1)

    # Top-right close [X]
    xx, xy, xw, xh = _COMMANDS_X_BTN
    cv2.rectangle(canvas, (xx, xy), (xx + xw, xy + xh), (25, 28, 36), -1)
    cv2.rectangle(canvas, (xx, xy), (xx + xw, xy + xh), BORDER, 1)
    cv2.putText(canvas, "X", (xx + 8, xy + 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, DIM, 1, cv2.LINE_AA)

    cv2.putText(canvas, "COMMANDS - say what you see", (60, 88),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, CYAN, 2, cv2.LINE_AA)

    total_pages = max(1, len(COMMANDS_PAGES))
    cur_page_idx = COMMANDS_PAGE % total_pages
    page = COMMANDS_PAGES[cur_page_idx]
    for col, rows in enumerate(page):
        x = 70 + col * 590
        y = 126
        for kind, a, b in rows:
            if kind == "cat":
                y += 6
                cv2.putText(canvas, a.upper(), (x, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, PINK, 1, cv2.LINE_AA)
                y += 22
            else:
                cv2.putText(canvas, a, (x, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, CYAN, 1, cv2.LINE_AA)
                tw = cv2.getTextSize(a + " ", cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)[0][0]
                cv2.putText(canvas, '- "' + b[:48] + '"', (x + tw, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, WHITE_TEXT, 1, cv2.LINE_AA)
                y += 21

    # Bottom navigation bar
    cv2.putText(canvas, "LEFT/RIGHT arrows or click buttons to flip  |  Press H or ESC to close",
                (60, 675), cv2.FONT_HERSHEY_SIMPLEX, 0.38, DIM, 1, cv2.LINE_AA)

    # Prev button [< PREV]
    px, py, pw, ph = _COMMANDS_PREV_BTN
    cv2.rectangle(canvas, (px, py), (px + pw, py + ph), (28, 32, 42), -1)
    cv2.rectangle(canvas, (px, py), (px + pw, py + ph), CYAN, 1)
    cv2.putText(canvas, "< PREV", (px + 22, py + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.45, CYAN, 1, cv2.LINE_AA)

    # Page indicator
    page_text = f"PAGE {cur_page_idx + 1} / {total_pages}"
    tw = cv2.getTextSize(page_text, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)[0][0]
    mid_x = (px + pw) + (810 - (px + pw) - tw) // 2
    cv2.putText(canvas, page_text, (mid_x, 675), cv2.FONT_HERSHEY_SIMPLEX, 0.42, WHITE_TEXT, 1, cv2.LINE_AA)

    # Next button [NEXT >]
    nx, ny, nw, nh = _COMMANDS_NEXT_BTN
    cv2.rectangle(canvas, (nx, ny), (nx + nw, ny + nh), (28, 32, 42), -1)
    cv2.rectangle(canvas, (nx, ny), (nx + nw, ny + nh), CYAN, 1)
    cv2.putText(canvas, "NEXT >", (nx + 24, ny + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.45, CYAN, 1, cv2.LINE_AA)

    # Close button [CLOSE [H]]
    cx, cy, cw, ch = _COMMANDS_CLOSE_BTN
    cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), (35, 22, 30), -1)
    cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), PINK, 1)
    cv2.putText(canvas, "CLOSE [H]", (cx + 15, cy + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.42, PINK, 1, cv2.LINE_AA)


def commands_next_page() -> int:
    global COMMANDS_PAGES, COMMANDS_PAGE
    if not COMMANDS_PAGES:
        COMMANDS_PAGES = _build_commands_pages()
    if COMMANDS_PAGES:
        COMMANDS_PAGE = (COMMANDS_PAGE + 1) % len(COMMANDS_PAGES)
    return COMMANDS_PAGE


def commands_prev_page() -> int:
    global COMMANDS_PAGES, COMMANDS_PAGE
    if not COMMANDS_PAGES:
        COMMANDS_PAGES = _build_commands_pages()
    if COMMANDS_PAGES:
        COMMANDS_PAGE = (COMMANDS_PAGE - 1) % len(COMMANDS_PAGES)
    return COMMANDS_PAGE


def tool_show_commands() -> str:
    global SHOW_COMMANDS, COMMANDS_PAGE
    SHOW_COMMANDS = True
    COMMANDS_PAGE = 0
    return "Commands panel shown (press H or say 'hide commands' to close)."


def tool_hide_commands() -> str:
    global SHOW_COMMANDS
    SHOW_COMMANDS = False
    return "Commands panel hidden."


def draw_hud() -> np.ndarray:
    """Render full 1280x720 HUD frame and publish face frame for phone bridge."""
    global CURRENT_STATE, HUD_MODE, CHAT_SCROLL
    w, h = 1280, 720
    canvas = np.zeros((h, w, 3), dtype=np.uint8)

    # Grid background
    for x in range(0, w, 80):
        cv2.line(canvas, (x, 0), (x, h), (18, 20, 24), 1)
    for y in range(0, h, 80):
        cv2.line(canvas, (0, y), (w, y), (18, 20, 24), 1)

    # Panels
    cv2.rectangle(canvas, (20, 70), (280, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 70), (280, 460), BORDER, 1)
    cv2.rectangle(canvas, (1000, 70), (1260, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (1000, 70), (1260, 460), BORDER, 1)
    cv2.rectangle(canvas, (20, 480), (1260, 705), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 480), (1260, 705), BORDER, 1)

    now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cpu_usage = psutil.cpu_percent()
    mem_usage = psutil.virtual_memory().percent
    battery = psutil.sensors_battery()
    bat_str = f"{battery.percent}%" if battery else "AC"

    cv2.putText(canvas, "A.R.I.A. // AUTONOMOUS ROBOTIC INTELLIGENCE AGENT", (30, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, CYAN, 2, cv2.LINE_AA)
    sys_stats = f"TIME: {now_str}  |  CPU: {cpu_usage}%  |  MEM: {mem_usage}%  |  PWR: {bat_str}"
    cv2.putText(canvas, sys_stats, (650, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 170, 180), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"PHONE BRIDGE: https://{lan_ip()}:{PHONE_BRIDGE_PORT}  (LAN only)",
                (30, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (120, 170, 200), 1, cv2.LINE_AA)

    # Whisper Toggle
    bx, by, bw, bh = _WHISPER_BTN
    _wcol = GREEN if WHISPER_MODE else (110, 120, 135)
    cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), PANEL_BG, -1)
    cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), _wcol, 1)
    cv2.putText(canvas, "WHISPER " + ("ON" if WHISPER_MODE else "OFF"),
                (bx + 9, by + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.42, _wcol, 1, cv2.LINE_AA)

    # Mood String
    mood_str = "MOOD: " + (_MOOD_CALLBACK().upper() if _MOOD_CALLBACK else "CALM")
    (mw, _), _ = cv2.getTextSize(mood_str, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
    cv2.putText(canvas, mood_str, (bx - 20 - mw, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (170, 190, 200), 1, cv2.LINE_AA)
    cv2.line(canvas, (20, 72), (1260, 72), CYAN, 1)

    # Subsystems
    cv2.putText(canvas, "[ SUBSYSTEMS ]", (35, 102), cv2.FONT_HERSHEY_SIMPLEX, 0.5, CYAN, 1, cv2.LINE_AA)
    modules = _SUBSYSTEMS_CALLBACK() if _SUBSYSTEMS_CALLBACK else []
    for i, (mod, stat, ok) in enumerate(modules):
        dot = GREEN if ok else (160, 160, 160)
        cv2.circle(canvas, (43, 131 + i * 32), 4, dot, -1)
        cv2.putText(canvas, mod, (55, 136 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
        (tw, _), _ = cv2.getTextSize(stat, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
        cv2.putText(canvas, stat, (275 - tw, 136 + i * 32), cv2.FONT_HERSHEY_SIMPLEX, 0.4, dot, 1, cv2.LINE_AA)

    # Optic PIP
    pip_x, pip_y, pip_w, pip_h = 35, 340, 230, 115
    cv2.rectangle(canvas, (pip_x, pip_y), (pip_x + pip_w, pip_y + pip_h), (30, 35, 45), -1)
    if LATEST_CAMERA_FRAME is not None:
        try:
            thumb = cv2.resize(LATEST_CAMERA_FRAME, (pip_w, pip_h))
            canvas[pip_y:pip_y + pip_h, pip_x:pip_x + pip_w] = thumb
        except Exception:
            pass
    # Optic PIP crosshairs (80% transparent / 20% opacity)
    pip_roi = canvas[pip_y:pip_y + pip_h, pip_x:pip_x + pip_w]
    overlay = pip_roi.copy()
    cx, cy = pip_w // 2, pip_h // 2
    cv2.circle(overlay, (cx, cy), 15, CYAN, 1)
    cv2.line(overlay, (cx - 25, cy), (cx + 25, cy), CYAN, 1)
    cv2.line(overlay, (cx, cy - 25), (cx, cy + 25), CYAN, 1)
    canvas[pip_y:pip_y + pip_h, pip_x:pip_x + pip_w] = cv2.addWeighted(overlay, 0.2, pip_roi, 0.8, 0)
    cv2.putText(canvas, "CAM_01 // OPTIC PIP", (pip_x + 5, pip_y + pip_h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, CYAN, 1, cv2.LINE_AA)

    # Action Stream
    cv2.putText(canvas, "[ ACTION STREAM ]", (1015, 102), cv2.FONT_HERSHEY_SIMPLEX, 0.5, CYAN, 1, cv2.LINE_AA)
    stream_y = 132
    for log in LOG_STREAM[-6:]:
        for line in textwrap.wrap(hud_ascii(log), width=32)[:2]:
            if stream_y > 440:
                break
            cv2.putText(canvas, line, (1012, stream_y), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (170, 190, 200), 1, cv2.LINE_AA)
            stream_y += 20
        stream_y += 6

    # Face or Chat Log
    if HUD_MODE == "chat_log":
        cv2.rectangle(canvas, (300, 76), (980, 460), (12, 14, 18), -1)
        cv2.rectangle(canvas, (300, 76), (980, 460), CYAN, 1)
        cv2.putText(canvas, "[ TACTICAL CHAT LOG // RECENT TRANSCRIPT ]", (320, 104),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, CYAN, 1, cv2.LINE_AA)
        rendered_lines = []
        for ts, sender, text in DISPLAY_CHAT_LOG:
            header_color = CYAN if sender.lower() == "user" else GREEN
            header = hud_ascii(f"[{ts}] {sender.upper()}:")
            msg_lines = textwrap.wrap(hud_ascii(text), width=62)
            if msg_lines:
                rendered_lines.append([(320, header, header_color), (465, msg_lines[0], WHITE_TEXT)])
                for sub_line in msg_lines[1:]:
                    rendered_lines.append([(465, sub_line, WHITE_TEXT)])
        start_idx = max(0, min(max(0, len(rendered_lines) - 13), len(rendered_lines) - CHAT_SCROLL - 13))
        view_lines = rendered_lines[start_idx:start_idx + 13]
        log_y = 132
        for line_segs in view_lines:
            for x, text, col in line_segs:
                cv2.putText(canvas, text, (x, log_y), cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1, cv2.LINE_AA)
            log_y += 22
    else:
        # Visor mode (v9.33 Cyber-girl Face)
        lx, rx, cy = 520, 760, 235
        if USE_PIXEL_AVATAR:
            draw_pixel_aria(canvas, CURRENT_STATE, time.time())
        elif CURRENT_STATE == "idle":
            now = time.time()
            _update_idle_face(now)
            squash = _blink_squash(now)
            f = _FACE
            for ex, side in ((lx, -1), (rx, 1)):
                cv2.ellipse(canvas, (ex, cy), (58, 82), 0, 0, 360, PINK_DEEP, -1)
                eh = max(4, int(74 * squash))
                cv2.ellipse(canvas, (ex, cy), (52, eh), 0, 0, 360, CYAN, -1)
                cv2.ellipse(canvas, (ex, cy), (52, eh), 0, 0, 360, LINER, 2)
                _draw_lashes(canvas, ex, cy, 52, eh, side)
                px, py = int(ex + f["eye_dx"]), int(cy + f["eye_dy"])
                ph = max(3, int(26 * squash))
                cv2.ellipse(canvas, (px, py), (23, ph + 6), 0, 0, 360, PINK, 2)
                cv2.ellipse(canvas, (px, py), (17, ph), 0, 0, 360, (10, 40, 70), -1)
                cv2.circle(canvas, (px - 6, py - int(12 * squash)), 5, (255, 255, 255), -1)
                apply_led_scanlines(canvas, ex - 60, cy - 80, ex + 60, cy + 80)
            wt = now * 2.0  # idle waveform: soft ripple, same voice as speaking
            for i in range(-9, 10):
                bar_x = 640 + (i * 14)
                bar_h = int(abs(np.sin(wt + i * 0.5)) * 8) + 2
                cv2.line(canvas, (bar_x, 388 - bar_h), (bar_x, 388 + bar_h), PINK, 2)
        elif CURRENT_STATE == "listening":
            # animated - the ring breathes while she listens.
            pulse = int(6 * np.sin(time.time() * 4))
            for ex, side in ((lx, -1), (rx, 1)):
                cv2.circle(canvas, (ex, cy), 70 + pulse, (255, 80, 255), -1)
                cv2.circle(canvas, (ex, cy), 70 + pulse, LINER, 2)
                _draw_lashes(canvas, ex, cy, 70, 70, side)
                cv2.circle(canvas, (ex, cy), 24, (40, 10, 60), -1)  # pupils lock on you
                cv2.circle(canvas, (ex - 8, cy - 10), 8, (255, 255, 255), -1)
                apply_led_scanlines(canvas, ex - 70, cy - 70, ex + 70, cy + 70)
            _draw_waveform_mouth(canvas, MOUTH_YELLOW, time.time())
        elif CURRENT_STATE == "thinking":
            # animated - pupils dart as she thinks, dots bounce above.
            t = time.time()
            dart = int(np.sin(t * 3.1) * 14)
            for ex, tilt, dy in ((lx, -12, -15), (rx, 8, -5)):
                cv2.ellipse(canvas, (ex, cy + dy), (48, 68), tilt, 0, 360, (0, 100, 200), -1)
                cv2.ellipse(canvas, (ex, cy + dy), (42, 60), tilt, 0, 360, AMBER, -1)
                cv2.circle(canvas, (ex + dart - 12, cy + dy - 20), 7, (255, 255, 255), -1)
                apply_led_scanlines(canvas, ex - 60, cy + dy - 70, ex + 60, cy + dy + 70)
            for i in range(3):
                bounce = int(abs(np.sin(t * 4 + i * 1.1)) * 12)
                cv2.circle(canvas, (600 + i * 40, 130 - bounce), 8, AMBER, -1)
            _draw_waveform_mouth(canvas, MOUTH_YELLOW, t)
        elif CURRENT_STATE == "working":
            # animated - amber radar sweep while a tool runs.
            t = time.time()
            sweep = int((t * 240) % 120) - 60
            for ex in (lx, rx):
                cv2.ellipse(canvas, (ex, cy), (58, 78), 0, 0, 360, (20, 60, 90), -1)
                cv2.ellipse(canvas, (ex, cy), (58, 78), 0, 0, 360, AMBER, 2)
                cv2.line(canvas, (ex - 52, cy + sweep), (ex + 52, cy + sweep), AMBER, 2)
                px = ex + int(np.sin(t * 5) * 20)
                cv2.circle(canvas, (px, cy), 16, AMBER, -1)
                cv2.circle(canvas, (px - 5, cy - 6), 5, (255, 255, 255), -1)
                apply_led_scanlines(canvas, ex - 60, cy - 80, ex + 60, cy + 80)
            _draw_waveform_mouth(canvas, MOUTH_YELLOW, t)
            arc = int((t * 180) % 360)
            cv2.ellipse(canvas, (640, 425), (70, 20), 0, arc, arc + 120, AMBER, 3)
        elif CURRENT_STATE == "coding":
            t = time.time()
            glow = int(20 + 12 * np.sin(t * 2.0))  # subtle breathing glow — just a bit
            for ex in (lx, rx):
                cv2.rectangle(canvas, (ex - 55, cy - 65), (ex + 55, cy + 65), (0, 100, 40), -1)
                cv2.rectangle(canvas, (ex - 58, cy - 68), (ex + 58, cy + 68), (0, glow, 0), 1)
                cv2.rectangle(canvas, (ex - 50, cy - 60), (ex + 50, cy + 60), GREEN, 2)
                cv2.putText(canvas, "</>", (ex - 35, cy + 12), cv2.FONT_HERSHEY_SIMPLEX,
                            1.1, GREEN, 2, cv2.LINE_AA)
                apply_led_scanlines(canvas, ex - 60, cy - 70, ex + 60, cy + 70)
            _draw_waveform_mouth(canvas, (40, 240, 120), t)
        elif CURRENT_STATE == "speaking":
            for ex, side in ((lx, -1), (rx, 1)):
                cv2.ellipse(canvas, (ex, cy - 10), (52, 45), 0, 190, 350, PINK, 10)
                _draw_lashes(canvas, ex, cy - 10, 52, 45, side)
                apply_led_scanlines(canvas, ex - 60, cy - 60, ex + 60, cy + 40)
            t_speak = time.time() * 12
            for i in range(-16, 17):
                bar_x = 640 + (i * 12)
                bar_h = int(abs(np.sin(t_speak + i * 0.45)) * 34) + 4
                cv2.line(canvas, (bar_x, 370 - bar_h), (bar_x, 370 + bar_h), PINK, 2)

        # Publish cropped face frame for Phone Bridge
        publish_face_frame(canvas)


    # Subtitles panel
    cv2.putText(canvas, "[ DIRECTIVE / SYNTHESIS // SUBTITLE STREAM ]", (35, 510),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, CYAN, 1, cv2.LINE_AA)
    sub_y = 536
    lines = textwrap.wrap(SUBTITLE_TEXT, width=105)[:4]
    for line in lines:
        cv2.putText(canvas, line, (40, sub_y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, WHITE_TEXT, 1, cv2.LINE_AA)
        sub_y += 22

    # Interactive Directive / Typing Input Bar
    ix, iy, iw, ih = _INPUT_BAR
    clean_buf = hud_ascii(TYPING_BUFFER).replace("\r", "").replace("\n", " ").replace("\t", " ")

    if TYPING_ACTIVE:
        cv2.rectangle(canvas, (ix, iy), (ix + iw, iy + ih), (22, 28, 34), -1)
        cv2.rectangle(canvas, (ix, iy), (ix + iw, iy + ih), CYAN, 2)
        tag = f"[ DIRECTIVE ({len(clean_buf)}) ] >" if len(clean_buf) > 0 else "[ DIRECTIVE ] >"
        cv2.putText(canvas, tag, (ix + 10, iy + 19),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, CYAN, 1, cv2.LINE_AA)

        cursor = "_" if int(time.time() * 2.5) % 2 == 0 else " "
        disp = clean_buf[-68:] if len(clean_buf) > 68 else clean_buf
        tag_w = 175 if len(clean_buf) > 0 else 145
        cv2.putText(canvas, f"{disp}{cursor}", (ix + tag_w, iy + 19),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, WHITE_TEXT, 1, cv2.LINE_AA)
    else:
        cv2.rectangle(canvas, (ix, iy), (ix + iw, iy + ih), (10, 12, 16), -1)
        cv2.rectangle(canvas, (ix, iy), (ix + iw, iy + ih), BORDER, 1)
        cv2.putText(canvas, "[ DIRECTIVE ]", (ix + 10, iy + 19),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (100, 120, 130), 1, cv2.LINE_AA)
        if clean_buf:
            disp = clean_buf[-68:] if len(clean_buf) > 68 else clean_buf
            cv2.putText(canvas, disp, (ix + 145, iy + 19),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 210, 220), 1, cv2.LINE_AA)
        else:
            cv2.putText(canvas, "Press [T] / click to type, or Ctrl+V / Right-Click / [PASTE] to paste directive...",
                        (ix + 145, iy + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (110, 120, 130), 1, cv2.LINE_AA)

    # Interactive Action Buttons on Directive Bar
    # PASTE button
    px, py, pw, ph = _INPUT_PASTE_BTN
    cv2.rectangle(canvas, (px, py), (px + pw, py + ph), (28, 42, 54), -1)
    cv2.rectangle(canvas, (px, py), (px + pw, py + ph), CYAN if TYPING_ACTIVE else (100, 140, 160), 1)
    cv2.putText(canvas, "PASTE", (px + 12, py + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, CYAN if TYPING_ACTIVE else (180, 200, 215), 1, cv2.LINE_AA)

    # SEND button
    sx, sy, sw, sh = _INPUT_SEND_BTN
    send_ready = bool(clean_buf.strip())
    send_bg = (20, 52, 28) if send_ready else (16, 24, 18)
    send_border = GREEN if send_ready else (40, 70, 45)
    send_txt_color = GREEN if send_ready else (80, 120, 90)
    cv2.rectangle(canvas, (sx, sy), (sx + sw, sy + sh), send_bg, -1)
    cv2.rectangle(canvas, (sx, sy), (sx + sw, sy + sh), send_border, 1)
    cv2.putText(canvas, "SEND", (sx + 13, sy + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, send_txt_color, 1, cv2.LINE_AA)

    # CLR button
    cx, cy, cw, ch = _INPUT_CLEAR_BTN
    cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), (26, 28, 32), -1)
    cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), BORDER, 1)
    cv2.putText(canvas, "CLR", (cx + 12, cy + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, DIM, 1, cv2.LINE_AA)

    # ESC / Toggle Typing button
    ex, ey, ew, eh = _INPUT_ESC_BTN
    cv2.rectangle(canvas, (ex, ey), (ex + ew, ey + eh), (32, 22, 22) if TYPING_ACTIVE else (24, 26, 30), -1)
    cv2.rectangle(canvas, (ex, ey), (ex + ew, ey + eh), (90, 70, 130) if TYPING_ACTIVE else BORDER, 1)
    esc_label = "ESC" if TYPING_ACTIVE else "[T]"
    cv2.putText(canvas, esc_label, (ex + 18, ey + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, DIM, 1, cv2.LINE_AA)

    # Bottom status bar
    status_bar = f"STATUS: {CURRENT_STATE.upper()}  |  [T] TYPE  |  [CTRL+V] PASTE  |  [ENTER] SEND  |  [SPACE] PTT  |  [X] CUT  |  [V] VISOR  |  [H] COMMANDS"
    cv2.putText(canvas, status_bar, (35, 700), cv2.FONT_HERSHEY_SIMPLEX, 0.36, DIM, 1, cv2.LINE_AA)

    # Optional commands overlay
    if SHOW_COMMANDS:
        _draw_commands_overlay(canvas)

    return canvas
