"""
ARIA HUD (Heads-Up Display) and Visual Interface.
Renders the 1280x720 cybernetic interface, animated cyber-girl face,
waveform mouth, system telemetry, optical PIP, and interactive context tiles:
Live Audio Waveforms, Active Task Chips, and Spotify Telemetry.
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
from aria.pixel_avatar import draw_pixel_aria, theme_colors, load_theme

load_theme()  # restore Alek's saved HUD color theme

from aria.config import PHONE_BRIDGE_PORT, GITHUB_USERNAME, GITHUB_TOKEN
from aria.vision import publish_face_frame
import aria.vision as _vision  # module ref: read LATEST_CAMERA_FRAME live (see Optical PIP)
import aria.ops as _ops  # OPS command-center data (no cv2 dep)
from aria.tools.schemas import COMMAND_GUIDE

_HUD_SUBS = {
    "\u2022": "-", "\u2014": "-", "\u2013": "-", "\u00b0": "deg",
    "\u2192": "->", "\u2190": "<-", "\u201c": '"', "\u201d": '"',
    "\u2018": "'", "\u2019": "'", "\u2026": "..."
}


def hud_ascii(s: Any) -> str:
    """Map Unicode characters to ASCII lookalikes so OpenCV Hershey fonts never render '?'."""
    res = str(s)
    for k, v in _HUD_SUBS.items():
        res = res.replace(k, v)
    return res.encode("ascii", errors="replace").decode("ascii")


def lan_ip() -> str:
    """Best-effort local LAN IP for display; cached after first resolve."""
    global _CACHED_LAN_IP
    if _CACHED_LAN_IP:
        return _CACHED_LAN_IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        _CACHED_LAN_IP = s.getsockname()[0]
        s.close()
    except Exception:
        _CACHED_LAN_IP = "127.0.0.1"
    return _CACHED_LAN_IP


_CACHED_LAN_IP = ""

# Telemetry Cache
_LAST_TELEMETRY_SAMPLE = 0.0
_CACHED_TELEMETRY: Dict[str, Any] = {
    "cpu": 0.0,
    "mem": 0.0,
    "battery": "100%",
    "subsystems": []
}


def get_cached_telemetry() -> Tuple[float, float, str, List[Tuple[str, str, bool]]]:
    """Sample hardware metrics at most once every 1.0s to avoid bogging down render loop."""
    global _LAST_TELEMETRY_SAMPLE, _CACHED_TELEMETRY
    now = time.time()
    if now - _LAST_TELEMETRY_SAMPLE >= 1.0:
        _LAST_TELEMETRY_SAMPLE = now
        try:
            _CACHED_TELEMETRY["cpu"] = psutil.cpu_percent(interval=None)
            _CACHED_TELEMETRY["mem"] = psutil.virtual_memory().percent
            bat = psutil.sensors_battery()
            _CACHED_TELEMETRY["battery"] = f"{int(bat.percent)}%" if bat else "PWR"
        except Exception:
            pass

        if _SUBSYSTEMS_CALLBACK:
            try:
                _CACHED_TELEMETRY["subsystems"] = _SUBSYSTEMS_CALLBACK() or []
            except Exception:
                pass

    return (
        _CACHED_TELEMETRY["cpu"],
        _CACHED_TELEMETRY["mem"],
        _CACHED_TELEMETRY["battery"],
        _CACHED_TELEMETRY["subsystems"]
    )


_LAST_INBOX_SAMPLE = 0.0
_CACHED_INBOX_COUNT = 0


def inbox_count_cached() -> int:
    """Cache inbox file count: sampled at most once every 3 seconds."""
    global _LAST_INBOX_SAMPLE, _CACHED_INBOX_COUNT
    now = time.time()
    if now - _LAST_INBOX_SAMPLE >= 3.0:
        _LAST_INBOX_SAMPLE = now
        try:
            from aria import inbox
            _CACHED_INBOX_COUNT = inbox.inbox_count()
        except Exception:
            _CACHED_INBOX_COUNT = 0
    return _CACHED_INBOX_COUNT


# Spotify Telemetry Cache
_SPOTIFY_CACHE: Dict[str, Any] = {
    "time": 0.0,
    "running": False,
    "playing": False,
    "title": "Spotify Standby",
    "artist": "",
    "track": ""
}


def _get_spotify_telemetry() -> Dict[str, Any]:
    """Detect Spotify window status and track title without lag."""
    global _SPOTIFY_CACHE
    now = time.time()
    if now - _SPOTIFY_CACHE["time"] < 0.6:
        return _SPOTIFY_CACHE

    info = {
        "time": now,
        "running": False,
        "playing": False,
        "title": "Spotify Standby",
        "artist": "",
        "track": ""
    }
    try:
        import ctypes

        def enum_cb(hwnd, extra):
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                    txt = buff.value.strip()
                    if "spotify" in txt.lower():
                        info["running"] = True
                        if txt in ("Spotify", "Spotify Free", "Spotify Premium"):
                            info["playing"] = False
                            info["title"] = txt
                        elif " - " in txt:
                            info["playing"] = True
                            parts = txt.split(" - ", 1)
                            info["artist"] = parts[0].strip()
                            info["track"] = parts[1].strip()
                            info["title"] = txt
                        else:
                            info["playing"] = True
                            info["title"] = txt
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception:
        pass

    _SPOTIFY_CACHE = info
    return info


class VolumeManager:
    """Non-blocking volume controller for Windows master device and Spotify audio session."""
    def __init__(self):
        self.target = "device"  # "device" or "spotify"
        self.device_vol = 0.5
        self.spotify_vol = 1.0
        self._desired_device = None
        self._desired_spotify = None
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            from ctypes import cast, POINTER

            def _query_dev():
                dev = AudioUtilities.GetSpeakers()
                if hasattr(dev, "EndpointVolume"):
                    vol = dev.EndpointVolume
                else:
                    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    vol = cast(iface, POINTER(IAudioEndpointVolume))
                return float(vol.GetMasterVolumeLevelScalar())

            def _apply_dev(level):
                dev = AudioUtilities.GetSpeakers()
                if hasattr(dev, "EndpointVolume"):
                    vol = dev.EndpointVolume
                else:
                    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    vol = cast(iface, POINTER(IAudioEndpointVolume))
                vol.SetMasterVolumeLevelScalar(max(0.0, min(1.0, level)), None)

            def _query_spot():
                sessions = AudioUtilities.GetAllSessions()
                for s in sessions:
                    if s.Process and s.Process.name().lower() == "spotify.exe":
                        return float(s.SimpleAudioVolume.GetMasterVolume())
                return 1.0

            def _apply_spot(level):
                sessions = AudioUtilities.GetAllSessions()
                for s in sessions:
                    if s.Process and s.Process.name().lower() == "spotify.exe":
                        s.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, level)), None)

            try:
                self.device_vol = _query_dev()
            except Exception:
                pass
            try:
                self.spotify_vol = _query_spot()
            except Exception:
                pass

            last_poll = time.time()
            while self._running:
                self._event.wait(timeout=1.0)
                self._event.clear()

                with self._lock:
                    s_dev = self._desired_device
                    s_spot = self._desired_spotify
                    self._desired_device = None
                    self._desired_spotify = None

                if s_dev is not None:
                    try:
                        _apply_dev(s_dev)
                        self.device_vol = s_dev
                    except Exception:
                        pass

                if s_spot is not None:
                    try:
                        _apply_spot(s_spot)
                        self.spotify_vol = s_spot
                    except Exception:
                        pass

                now = time.time()
                if now - last_poll > 1.5 and s_dev is None and s_spot is None:
                    try:
                        self.device_vol = _query_dev()
                    except Exception:
                        pass
                    try:
                        self.spotify_vol = _query_spot()
                    except Exception:
                        pass
                    last_poll = now
        except Exception:
            pass
        finally:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def get_level(self) -> float:
        return self.device_vol if self.target == "device" else self.spotify_vol

    def set_level(self, level: float):
        level = max(0.0, min(1.0, float(level)))
        with self._lock:
            if self.target == "device":
                self.device_vol = level
                self._desired_device = level
            else:
                self.spotify_vol = level
                self._desired_spotify = level
        self._event.set()

    def toggle_target(self) -> str:
        self.target = "spotify" if self.target == "device" else "device"
        return self.target


_VOL_MGR = VolumeManager()


def get_hud_volume() -> float:
    return _VOL_MGR.get_level()


def set_hud_volume(pct: float):
    _VOL_MGR.set_level(pct)


def toggle_volume_target() -> str:
    return _VOL_MGR.toggle_target()


# Colors (BGR)
BG = (10, 12, 16)
PANEL_BG = (14, 16, 22)
BORDER = (38, 48, 64)
ACC = (240, 160, 40)
CYAN = (255, 230, 40)
GREEN = (40, 240, 120)
WHITE_TEXT = (230, 235, 240)
PINK = (180, 80, 255)
MOUTH_YELLOW = (40, 220, 255)
PINK_DEEP = (110, 45, 190)
LINER = (70, 25, 120)
DIM = (150, 160, 170)
AMBER = (0, 180, 255)

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

# Context Tabs bounds (x, y, w, h)
_TAB_DASHBOARD = (35, 486, 125, 20)
_TAB_SUBTITLES = (168, 486, 115, 20)
_TAB_AUDIO     = (291, 486, 115, 20)
_TAB_TASKS     = (414, 486, 115, 20)
_TAB_SPOTIFY   = (537, 486, 105, 20)
_TAB_MINIMIZE  = (1155, 486, 90, 20)

# Spotify interactive transport buttons in DASHBOARD split view:
_DASH_SPOTIFY_PREV   = (845, 608, 30, 22)
_DASH_SPOTIFY_PLAY   = (883, 608, 48, 22)
_DASH_SPOTIFY_NEXT   = (939, 608, 30, 22)
_DASH_SPOTIFY_DJ     = (977, 608, 36, 22)
_DASH_SPOTIFY_FOCUS  = (1021, 608, 42, 22)
_DASH_VOL_TARGET_BTN = (1070, 608, 42, 22)
_DASH_VOL_SLIDER     = (1118, 608, 122, 22)

# Spotify interactive buttons in expanded SPOTIFY view:
_EXP_SPOTIFY_PREV    = (60, 595, 90, 28)
_EXP_SPOTIFY_PLAY    = (160, 595, 120, 28)
_EXP_SPOTIFY_NEXT    = (290, 595, 90, 28)
_EXP_SPOTIFY_DJ      = (390, 595, 130, 28)
_EXP_SPOTIFY_FOCUS   = (530, 595, 100, 28)
_EXP_SPOTIFY_CHILL   = (640, 595, 95, 28)
_EXP_SPOTIFY_FOCUSM  = (745, 595, 95, 28)
_EXP_SPOTIFY_ENERGY  = (850, 595, 95, 28)
_EXP_VOL_TARGET_BTN  = (955, 595, 80, 28)
_EXP_VOL_SLIDER      = (1045, 595, 195, 28)


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
USE_PIXEL_AVATAR = True
SHOW_COMMANDS = False
TYPING_ACTIVE: bool = False
TYPING_BUFFER: str = ""
COMMANDS_PAGE = 0
COMMANDS_PAGES: List[Any] = []
SUBTITLE_TEXT = ""
LOG_STREAM: List[str] = []
DISPLAY_CHAT_LOG: List[Tuple[str, str, str]] = []


def add_display_chat(ts: str, sender: str, msg: str):
    DISPLAY_CHAT_LOG.append((str(ts), str(sender), str(msg)))
    if len(DISPLAY_CHAT_LOG) > 60:
        DISPLAY_CHAT_LOG.pop(0)

# Context Tiles State
TILE_MODE: str = "dashboard"  # "dashboard", "subtitles", "audio", "tasks", "spotify"
TILE_COLLAPSED: bool = False
_TILE_MODES_ORDER = ["dashboard", "subtitles", "audio", "tasks", "spotify"]

# Whispering mode toggle
WHISPER_MODE: bool = False

_FACE: Dict[str, Any] = {
    "eye_dx": 0.0,
    "eye_dy": 0.0,
    "target_dx": 0.0,
    "target_dy": 0.0,
    "next_glance": 0.0,
    "next_blink": 0.0,
    "blink_until": 0.0,
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
    global LOG_STREAM
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


def set_tile_mode(mode: str):
    global TILE_MODE, TILE_COLLAPSED
    if mode in _TILE_MODES_ORDER:
        TILE_MODE = mode
        TILE_COLLAPSED = False


def cycle_tile_mode() -> str:
    global TILE_MODE, TILE_COLLAPSED
    TILE_COLLAPSED = False
    idx = (_TILE_MODES_ORDER.index(TILE_MODE) + 1) % len(_TILE_MODES_ORDER)
    TILE_MODE = _TILE_MODES_ORDER[idx]
    return TILE_MODE


def _dispatch_spotify_action(action: str):
    """Trigger Spotify action asynchronously in background thread."""
    def _run():
        try:
            from aria import spotify
            if action in ("play_pause", "previous", "next"):
                res = spotify.tool_spotify(action)
                add_hud_log(res)
            elif action == "dj":
                res = spotify.tool_dj("liked songs")
                add_hud_log(res)
            elif action == "focus":
                from aria.tools.builtins import tool_focus_window
                res = tool_focus_window("Spotify")
                add_hud_log(res)
            elif action == "chill":
                res = spotify.tool_dj("chill")
                add_hud_log(res)
            elif action == "focus_music":
                res = spotify.tool_dj("focus")
                add_hud_log(res)
            elif action == "energy":
                res = spotify.tool_dj("energy")
                add_hud_log(res)
        except Exception as e:
            add_hud_log(f"Spotify action error: {e}")
    threading.Thread(target=_run, daemon=True).start()


def handle_click(x: int, y: int) -> bool:
    """Handle mouse clicks on HUD context tabs, collapse toggle, and Spotify transport."""
    global TILE_MODE, TILE_COLLAPSED

    # 1. Collapse toggle button
    bx, by, bw, bh = _TAB_MINIMIZE
    if bx <= x <= bx + bw and by <= y <= by + bh:
        TILE_COLLAPSED = not TILE_COLLAPSED
        add_hud_log(f"Context tiles {'minimized' if TILE_COLLAPSED else 'expanded'}.")
        return True

    # 2. Context Tab clicks
    tabs = [
        (_TAB_DASHBOARD, "dashboard"),
        (_TAB_SUBTITLES, "subtitles"),
        (_TAB_AUDIO,     "audio"),
        (_TAB_TASKS,     "tasks"),
        (_TAB_SPOTIFY,   "spotify"),
    ]
    for (tx, ty, tw, th), mode in tabs:
        if tx <= x <= tx + tw and ty <= y <= ty + th:
            TILE_COLLAPSED = False
            TILE_MODE = mode
            add_hud_log(f"Context tile: {mode.upper()}")
            return True

    # 3. Spotify transport buttons and volume slider in DASHBOARD split view
    if not TILE_COLLAPSED and TILE_MODE == "dashboard":
        # Target toggle button
        vx, vy, vw, vh = _DASH_VOL_TARGET_BTN
        if vx <= x <= vx + vw and vy <= y <= vy + vh:
            t = _VOL_MGR.toggle_target()
            add_hud_log(f"Volume target: {t.upper()}")
            return True

        # Volume slider click
        sx, sy, sw, sh = _DASH_VOL_SLIDER
        if sx <= x <= sx + sw and sy <= y <= sy + sh:
            pct = (x - sx) / float(sw)
            _VOL_MGR.set_level(pct)
            add_hud_log(f"{_VOL_MGR.target.upper()} vol: {int(_VOL_MGR.get_level() * 100)}%")
            return True

        btns = [
            (_DASH_SPOTIFY_PREV, "previous"),
            (_DASH_SPOTIFY_PLAY, "play_pause"),
            (_DASH_SPOTIFY_NEXT, "next"),
            (_DASH_SPOTIFY_DJ,   "dj"),
            (_DASH_SPOTIFY_FOCUS,"focus"),
        ]
        for (bx, by, bw, bh), action in btns:
            if bx <= x <= bx + bw and by <= y <= by + bh:
                _dispatch_spotify_action(action)
                return True

    # 4. Spotify transport buttons and volume slider in expanded SPOTIFY view
    if not TILE_COLLAPSED and TILE_MODE == "spotify":
        # Target toggle button
        vx, vy, vw, vh = _EXP_VOL_TARGET_BTN
        if vx <= x <= vx + vw and vy <= y <= vy + vh:
            t = _VOL_MGR.toggle_target()
            add_hud_log(f"Volume target: {t.upper()}")
            return True

        # Volume slider click
        sx, sy, sw, sh = _EXP_VOL_SLIDER
        if sx <= x <= sx + sw and sy <= y <= sy + sh:
            pct = (x - sx) / float(sw)
            _VOL_MGR.set_level(pct)
            add_hud_log(f"{_VOL_MGR.target.upper()} vol: {int(_VOL_MGR.get_level() * 100)}%")
            return True

        exp_btns = [
            (_EXP_SPOTIFY_PREV, "previous"),
            (_EXP_SPOTIFY_PLAY, "play_pause"),
            (_EXP_SPOTIFY_NEXT, "next"),
            (_EXP_SPOTIFY_DJ,   "dj"),
            (_EXP_SPOTIFY_FOCUS,"focus"),
            (_EXP_SPOTIFY_CHILL,"chill"),
            (_EXP_SPOTIFY_FOCUSM,"focus_music"),
            (_EXP_SPOTIFY_ENERGY,"energy"),
        ]
        for (bx, by, bw, bh), action in exp_btns:
            if bx <= x <= bx + bw and by <= y <= by + bh:
                _dispatch_spotify_action(action)
                return True

    return False


def handle_drag(x: int, y: int) -> bool:
    """Handle mouse drag over volume slider in active tile."""
    if TILE_COLLAPSED:
        return False
    if TILE_MODE == "dashboard":
        sx, sy, sw, sh = _DASH_VOL_SLIDER
        if sx - 10 <= x <= sx + sw + 10 and sy - 8 <= y <= sy + sh + 8:
            pct = (x - sx) / float(sw)
            _VOL_MGR.set_level(pct)
            return True
    elif TILE_MODE == "spotify":
        sx, sy, sw, sh = _EXP_VOL_SLIDER
        if sx - 10 <= x <= sx + sw + 10 and sy - 8 <= y <= sy + sh + 8:
            pct = (x - sx) / float(sw)
            _VOL_MGR.set_level(pct)
            return True
    return False


def handle_wheel(x: int, y: int, up: bool) -> bool:
    """Handle mouse wheel scrolling over volume slider or target button."""
    if TILE_COLLAPSED:
        return False
    delta = 0.05 if up else -0.05
    if TILE_MODE == "dashboard":
        for (bx, by, bw, bh) in (_DASH_VOL_SLIDER, _DASH_VOL_TARGET_BTN):
            if bx <= x <= bx + bw and by <= y <= by + bh:
                _VOL_MGR.set_level(_VOL_MGR.get_level() + delta)
                add_hud_log(f"{_VOL_MGR.target.upper()} vol: {int(_VOL_MGR.get_level() * 100)}%")
                return True
    elif TILE_MODE == "spotify":
        for (bx, by, bw, bh) in (_EXP_VOL_SLIDER, _EXP_VOL_TARGET_BTN):
            if bx <= x <= bx + bw and by <= y <= by + bh:
                _VOL_MGR.set_level(_VOL_MGR.get_level() + delta)
                add_hud_log(f"{_VOL_MGR.target.upper()} vol: {int(_VOL_MGR.get_level() * 100)}%")
                return True
    return False


def handle_release(x: int, y: int):
    """Handle mouse button release."""
    pass


def _get_active_task_chips() -> List[Tuple[str, str, Tuple[int, int, int]]]:
    """Returns list of (label, value, color) chips for active tasks."""
    chips = []
    latest_tool = "IDLE"
    for log in reversed(LOG_STREAM):
        if "Tool " in log or "tool_" in log or "run_python_code" in log or "fetch_url" in log or "read_screen" in log:
            import re
            m = re.search(r"(?:tool_|Tool\s+|call:\s*)([a-zA-Z0-9_]+)", log)
            if m:
                latest_tool = m.group(1)[:14]
                break
            elif "run_python_code" in log:
                latest_tool = "python_code"
                break
            elif "read_screen" in log:
                latest_tool = "read_screen"
                break
    chips.append(("TOOL", latest_tool, GREEN if latest_tool != "IDLE" else DIM))

    try:
        from aria.scheduler import sched_list
        active_sched = len(sched_list())
        chips.append(("TIMERS", str(active_sched), AMBER if active_sched > 0 else DIM))
    except Exception:
        chips.append(("TIMERS", "0", DIM))

    chips.append(("STATE", CURRENT_STATE.upper()[:9], CYAN))
    chips.append(("INBOX", str(inbox_count_cached()), (240, 160, 60)))
    vtgt = _VOL_MGR.target.upper()[:3]
    cur_vol = int(_VOL_MGR.get_level() * 100)
    vol_col = CYAN if _VOL_MGR.target == "device" else GREEN
    chips.append((f"VOL:{vtgt}", f"{cur_vol}%", vol_col))
    return chips


def apply_led_scanlines(canvas: np.ndarray, x1: int, y1: int, x2: int, y2: int):
    for y in range(max(0, y1), min(canvas.shape[0], y2), 4):
        canvas[y, max(0, x1):min(canvas.shape[1], x2)] = \
            canvas[y, max(0, x1):min(canvas.shape[1], x2)] // 2


def _update_idle_face(now: float):
    """Advance idle-face animation state for classic visor."""
    f = _FACE
    if f["next_glance"] == 0.0:
        f["next_glance"] = now + 0.5
        f["next_blink"] = now + 3.0

    _mw = (_MOOD_CALLBACK().lower() if _MOOD_CALLBACK else "calm")
    if "sleepy" in _mw:
        glance_min, glance_max = 5.0, 9.0
        blink_min, blink_max = 6.0, 10.0
        max_dist = 6.0
    elif "curious" in _mw or "excited" in _mw:
        glance_min, glance_max = 1.0, 2.5
        blink_min, blink_max = 2.0, 4.0
        max_dist = 16.0
    else:
        glance_min, glance_max = 2.0, 4.5
        blink_min, blink_max = 2.5, 5.0
        max_dist = 12.0

    if now >= f["next_glance"]:
        f["target_dx"] = random.uniform(-max_dist, max_dist)
        f["target_dy"] = random.uniform(-max_dist * 0.5, max_dist * 0.5)
        f["next_glance"] = now + random.uniform(glance_min, glance_max)

    smooth = 0.15
    f["eye_dx"] += (f["target_dx"] - f["eye_dx"]) * smooth
    f["eye_dy"] += (f["target_dy"] - f["eye_dy"]) * smooth

    if now >= f["next_blink"]:
        f["blink_until"] = now + 0.18
        f["next_blink"] = now + random.uniform(blink_min, blink_max)


def _blink_squash(now: float) -> float:
    f = _FACE
    if now < f["blink_until"]:
        phase = (f["blink_until"] - now) / 0.18
        return max(0.08, float(abs(math.sin(phase * math.pi))))
    return 1.0


def _draw_lashes(canvas: np.ndarray, ex: int, cy: int, ew: int, eh: int, side: int):
    outer_x = ex + side * int(ew * 0.82)
    top_y = cy - int(eh * 0.72)
    lash_tip1 = (outer_x + side * 18, top_y - 14)
    cv2.line(canvas, (outer_x, top_y), lash_tip1, LINER, 3, cv2.LINE_AA)
    mid_outer_x = ex + side * int(ew * 0.60)
    mid_top_y = cy - int(eh * 0.90)
    lash_tip2 = (mid_outer_x + side * 10, mid_top_y - 12)
    cv2.line(canvas, (mid_outer_x, mid_top_y), lash_tip2, LINER, 2, cv2.LINE_AA)


def _draw_waveform_mouth(canvas: np.ndarray, color: Tuple[int, int, int], t: float,
                         y_center: int = 370, num_bars: int = 33,
                         spacing: int = 12, max_amplitude: int = 34):
    for i in range(-num_bars // 2 + 1, num_bars // 2 + 1):
        bar_x = 640 + (i * spacing)
        bar_h = int(abs(np.sin(t + i * 0.45)) * max_amplitude) + 4
        cv2.line(canvas, (bar_x, y_center - bar_h), (bar_x, y_center + bar_h), color, 2)


def _build_commands_pages() -> List[List[Tuple[str, List[Tuple[str, str]]]]]:
    cols_per_page = 3
    items_per_col = 14
    capacity = cols_per_page * items_per_col
    all_items: List[Tuple[str, str, str]] = []
    for cat, items in COMMAND_GUIDE.items():
        for act, phr in items:
            all_items.append((cat, act, phr))

    pages: List[List[Tuple[str, List[Tuple[str, str]]]]] = []
    for p_start in range(0, len(all_items), capacity):
        page_items = all_items[p_start:p_start + capacity]
        page_cols: List[Tuple[str, List[Tuple[str, str]]]] = []
        for c_idx in range(cols_per_page):
            c_start = c_idx * items_per_col
            c_items = page_items[c_start:c_start + items_per_col]
            if not c_items:
                break
            col_cat = c_items[0][0]
            col_list = [(act, phr) for (_, act, phr) in c_items]
            page_cols.append((col_cat, col_list))
        pages.append(page_cols)
    return pages or [[("COMMANDS", [("Help", "No commands registered")])]]


def _draw_commands_overlay(canvas: np.ndarray):
    global COMMANDS_PAGES, COMMANDS_PAGE
    if not COMMANDS_PAGES:
        COMMANDS_PAGES = _build_commands_pages()

    total_pages = max(1, len(COMMANDS_PAGES))
    cur_page_idx = COMMANDS_PAGE % total_pages
    page = COMMANDS_PAGES[cur_page_idx]

    overlay = canvas.copy()
    cv2.rectangle(overlay, (36, 52), (1244, 700), (8, 10, 16), -1)
    cv2.addWeighted(overlay, 0.94, canvas, 0.06, 0, canvas)
    cv2.rectangle(canvas, (36, 52), (1244, 700), CYAN, 2)
    cv2.line(canvas, (36, 92), (1244, 92), CYAN, 1)

    cv2.putText(canvas, "A.R.I.A. COMMAND GUIDE  //  VERBAL & ACTION MATRIX",
                (56, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.65, CYAN, 2, cv2.LINE_AA)

    xx, xy, xw, xh = _COMMANDS_X_BTN
    cv2.rectangle(canvas, (xx, xy), (xx + xw, xy + xh), (35, 22, 30), -1)
    cv2.rectangle(canvas, (xx, xy), (xx + xw, xy + xh), PINK, 1)
    cv2.putText(canvas, "X", (xx + 8, xy + 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, PINK, 2, cv2.LINE_AA)

    col_xs = [60, 450, 840]
    for c_idx, (cat_name, items) in enumerate(page):
        if c_idx >= len(col_xs):
            break
        x = col_xs[c_idx]
        y = 120
        cv2.putText(canvas, f"// {cat_name.upper()}", (x, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.46, ACC, 1, cv2.LINE_AA)
        cv2.line(canvas, (x, y + 4), (x + 360, y + 4), BORDER, 1)
        y += 24
        for a, b in items:
            tw = cv2.getTextSize(a + ": ", cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)[0][0]
            cv2.putText(canvas, a + ": ", (x, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, CYAN, 1, cv2.LINE_AA)
            cv2.putText(canvas, '- "' + b[:48] + '"', (x + tw, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, WHITE_TEXT, 1, cv2.LINE_AA)
            y += 21

    cv2.putText(canvas, "LEFT/RIGHT arrows or click buttons to flip  |  Press H or ESC to close",
                (60, 675), cv2.FONT_HERSHEY_SIMPLEX, 0.38, DIM, 1, cv2.LINE_AA)

    px, py, pw, ph = _COMMANDS_PREV_BTN
    cv2.rectangle(canvas, (px, py), (px + pw, py + ph), (28, 32, 42), -1)
    cv2.rectangle(canvas, (px, py), (px + pw, py + ph), CYAN, 1)
    cv2.putText(canvas, "< PREV", (px + 22, py + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.45, CYAN, 1, cv2.LINE_AA)

    page_text = f"PAGE {cur_page_idx + 1} / {total_pages}"
    tw = cv2.getTextSize(page_text, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)[0][0]
    mid_x = (px + pw) + (810 - (px + pw) - tw) // 2
    cv2.putText(canvas, page_text, (mid_x, 675), cv2.FONT_HERSHEY_SIMPLEX, 0.42, WHITE_TEXT, 1, cv2.LINE_AA)

    nx, ny, nw, nh = _COMMANDS_NEXT_BTN
    cv2.rectangle(canvas, (nx, ny), (nx + nw, ny + nh), (28, 32, 42), -1)
    cv2.rectangle(canvas, (nx, ny), (nx + nw, ny + nh), CYAN, 1)
    cv2.putText(canvas, "NEXT >", (nx + 24, ny + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.45, CYAN, 1, cv2.LINE_AA)

    cx, cy, cw, ch = _COMMANDS_CLOSE_BTN
    cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), (35, 22, 30), -1)
    cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), PINK, 1)
    cv2.putText(canvas, "CLOSE [H]", (cx + 15, cy + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.42, PINK, 1, cv2.LINE_AA)


def commands_next_page() -> int:
    global COMMANDS_PAGES, COMMANDS_PAGE
    if not COMMANDS_PAGES:
        COMMANDS_PAGES = _build_commands_pages()
    COMMANDS_PAGE = (COMMANDS_PAGE + 1) % len(COMMANDS_PAGES)
    return COMMANDS_PAGE


def commands_prev_page() -> int:
    global COMMANDS_PAGES, COMMANDS_PAGE
    if not COMMANDS_PAGES:
        COMMANDS_PAGES = _build_commands_pages()
    COMMANDS_PAGE = (COMMANDS_PAGE - 1) % len(COMMANDS_PAGES)
    return COMMANDS_PAGE


def tool_show_commands() -> str:
    global SHOW_COMMANDS
    SHOW_COMMANDS = True
    return "Commands overlay is now OPEN on the HUD. Say 'hide commands' or press H to dismiss."


def tool_hide_commands() -> str:
    global SHOW_COMMANDS
    SHOW_COMMANDS = False
    return "Commands overlay CLOSED."


def _draw_context_tiles(canvas: np.ndarray, ACC: Tuple[int, int, int], ACC2: Tuple[int, int, int],
                        t: float, state: str):
    """Render interactive context tiles: live waveforms, task chips, Spotify telemetry."""
    # 1. Header Tab Bar (y: 486..508)
    tabs = [
        (_TAB_DASHBOARD, "[1] DASHBOARD", "dashboard"),
        (_TAB_SUBTITLES, "[2] SUBTITLES", "subtitles"),
        (_TAB_AUDIO,     "[3] AUDIO OSC",  "audio"),
        (_TAB_TASKS,     "[4] TASK FLEET", "tasks"),
        (_TAB_SPOTIFY,   "[5] SPOTIFY",    "spotify"),
    ]
    for (tx, ty, tw, th), label, mode in tabs:
        is_active = (not TILE_COLLAPSED) and (TILE_MODE == mode)
        bg_col = (30, 42, 54) if is_active else (18, 22, 28)
        border_col = CYAN if is_active else BORDER
        text_col = CYAN if is_active else DIM
        cv2.rectangle(canvas, (tx, ty), (tx + tw, ty + th), bg_col, -1)
        cv2.rectangle(canvas, (tx, ty), (tx + tw, ty + th), border_col, 1)
        cv2.putText(canvas, label, (tx + 8, ty + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.35, text_col, 1, cv2.LINE_AA)

    # Collapse toggle button
    bx, by, bw, bh = _TAB_MINIMIZE
    cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), (26, 30, 38), -1)
    cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), BORDER, 1)
    min_label = "[+] EXPAND" if TILE_COLLAPSED else "[-] COLLAPSE"
    cv2.putText(canvas, min_label, (bx + 8, by + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.33, (170, 185, 200), 1, cv2.LINE_AA)

    # 2. Collapsed View
    if TILE_COLLAPSED:
        cv2.rectangle(canvas, (35, 514), (1245, 538), (14, 16, 22), -1)
        cv2.rectangle(canvas, (35, 514), (1245, 538), BORDER, 1)
        spot = _get_spotify_telemetry()
        s_title = spot["title"][:30]
        ticker = f"SYNTHESIS: {SUBTITLE_TEXT[:60]}...  |  AUDIO: ARMED  |  SPOTIFY: {s_title}"
        cv2.putText(canvas, ticker, (45, 530), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (170, 190, 205), 1, cv2.LINE_AA)
        return

    # 3. View: DASHBOARD (Split 3-Tile Context Mode)
    if TILE_MODE == "dashboard":
        # --- Card 1: Subtitles Stream (x: 35..425, y: 512..638) ---
        cv2.rectangle(canvas, (35, 512), (425, 638), (16, 18, 24), -1)
        cv2.rectangle(canvas, (35, 512), (425, 638), BORDER, 1)
        cv2.putText(canvas, "[ SUBTITLES // SYNTHESIS ]", (45, 528),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, ACC, 1, cv2.LINE_AA)
        dot_col = GREEN if state in ("speaking", "thinking") else (70, 90, 110)
        cv2.circle(canvas, (412, 524), 4, dot_col, -1)
        cv2.line(canvas, (35, 534), (425, 534), BORDER, 1)

        sub_lines = textwrap.wrap(SUBTITLE_TEXT, width=42)[:4]
        sy = 552
        for s_line in sub_lines:
            cv2.putText(canvas, s_line, (45, sy), cv2.FONT_HERSHEY_SIMPLEX, 0.36, WHITE_TEXT, 1, cv2.LINE_AA)
            sy += 20

        # --- Card 2: Live Audio Dynamics & Oscilloscope (x: 435..825, y: 512..638) ---
        cv2.rectangle(canvas, (435, 512), (825, 638), (14, 16, 22), -1)
        cv2.rectangle(canvas, (435, 512), (825, 638), BORDER, 1)
        cv2.putText(canvas, "[ AUDIO // LIVE OSCILLOSCOPE ]", (445, 528),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, ACC, 1, cv2.LINE_AA)
        aud_badge = "SYNTHESIS" if state == "speaking" else ("PERCEPTION" if state == "listening" else "VAD ARMED")
        cv2.putText(canvas, aud_badge, (735, 528), cv2.FONT_HERSHEY_SIMPLEX, 0.33, CYAN, 1, cv2.LINE_AA)
        cv2.line(canvas, (435, 534), (825, 534), BORDER, 1)

        # 28-Band FFT Spectrum Bars (y: 538..582)
        for i in range(28):
            bx = 445 + i * 13
            if state == "speaking":
                bh = int(abs(np.sin(t * 14 + i * 0.45) * np.cos(t * 8 + i * 0.2)) * 36) + 4
            elif state == "listening":
                bh = int(abs(np.sin(t * 6 + i * 0.6)) * 24) + 3
            elif state == "thinking":
                bh = int(abs(np.sin(t * 9 + i * 0.8)) * 18) + 3
            else:
                bh = int(abs(np.sin(t * 2.2 + i * 0.35)) * 12) + 2
            
            bh = min(bh, 42)
            cv2.line(canvas, (bx, 582), (bx, 582 - bh), CYAN if i % 2 == 0 else ACC, 2)
            cv2.circle(canvas, (bx, max(540, 582 - bh - 2)), 1, (255, 255, 255), -1)

        # Oscilloscope Waveform Trace (y: 590..634, baseline at y: 612)
        cv2.line(canvas, (445, 612), (815, 612), (25, 30, 40), 1)
        pts = []
        amp = 18 if state == "speaking" else (12 if state in ("listening", "thinking") else 6)
        for px in range(445, 816, 4):
            ph = (px - 445) / 370.0
            wy = 612 + int(np.sin(ph * 16.0 + t * 9.0) * np.cos(ph * 6.0 + t * 4.0) * amp)
            pts.append((px, wy))
        if len(pts) > 1:
            for k in range(len(pts) - 1):
                cv2.line(canvas, pts[k], pts[k + 1], GREEN, 1, cv2.LINE_AA)

        # --- Card 3: Active Task Fleet & Spotify (x: 835..1250, y: 512..638) ---
        cv2.rectangle(canvas, (835, 512), (1250, 638), (16, 18, 24), -1)
        cv2.rectangle(canvas, (835, 512), (1250, 638), BORDER, 1)
        cv2.putText(canvas, "[ TASK FLEET & SPOTIFY ]", (845, 528),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, ACC, 1, cv2.LINE_AA)
        cv2.line(canvas, (835, 534), (1250, 534), BORDER, 1)

        # Section A: Active Task Chips (y: 540..568)
        chips = _get_active_task_chips()
        cx = 845
        for lbl, val, col in chips:
            chip_str = f"{lbl}:{val}"
            (cw, _), _ = cv2.getTextSize(chip_str, cv2.FONT_HERSHEY_SIMPLEX, 0.33, 1)
            cv2.rectangle(canvas, (cx, 542), (cx + cw + 14, 562), (22, 28, 36), -1)
            cv2.rectangle(canvas, (cx, 542), (cx + cw + 14, 562), col, 1)
            cv2.putText(canvas, chip_str, (cx + 7, 556), cv2.FONT_HERSHEY_SIMPLEX, 0.33, col, 1, cv2.LINE_AA)
            cx += cw + 20

        cv2.line(canvas, (845, 570), (1240, 570), (28, 34, 44), 1)

        # Section B: Spotify Telemetry & Controls (y: 574..634)
        spot = _get_spotify_telemetry()
        sp_stat_col = GREEN if spot["playing"] else (AMBER if spot["running"] else DIM)
        sp_stat_lbl = "PLAYING" if spot["playing"] else ("PAUSED" if spot["running"] else "STANDBY")

        cv2.rectangle(canvas, (845, 578), (915, 598), (22, 28, 36), -1)
        cv2.rectangle(canvas, (845, 578), (915, 598), sp_stat_col, 1)
        cv2.putText(canvas, sp_stat_lbl, (852, 592), cv2.FONT_HERSHEY_SIMPLEX, 0.33, sp_stat_col, 1, cv2.LINE_AA)

        spot_disp = spot["title"][:38] if spot["title"] else "Spotify Offline"
        cv2.putText(canvas, spot_disp, (925, 592), cv2.FONT_HERSHEY_SIMPLEX, 0.36, WHITE_TEXT, 1, cv2.LINE_AA)

        # Transport Buttons: Prev, Play/Pause, Next, DJ, APP
        s_btns = [
            (_DASH_SPOTIFY_PREV,  "|<", DIM),
            (_DASH_SPOTIFY_PLAY,  "> / ||", CYAN if spot["playing"] else GREEN),
            (_DASH_SPOTIFY_NEXT,  ">|", DIM),
            (_DASH_SPOTIFY_DJ,    "DJ", (200, 140, 255)),
            (_DASH_SPOTIFY_FOCUS, "APP", (160, 180, 200)),
        ]
        for (bx, by, bw, bh), blabel, bcol in s_btns:
            cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), (24, 28, 36), -1)
            cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), bcol, 1)
            cv2.putText(canvas, blabel, (bx + 6, by + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.34, bcol, 1, cv2.LINE_AA)

        # Volume Mode Toggle Button & Volume Slider
        vtgt = _VOL_MGR.target
        vcol = CYAN if vtgt == "device" else GREEN
        vlbl = "DEV" if vtgt == "device" else "SPT"
        vx, vy, vw, vh = _DASH_VOL_TARGET_BTN
        cv2.rectangle(canvas, (vx, vy), (vx + vw, vy + vh), (24, 28, 36), -1)
        cv2.rectangle(canvas, (vx, vy), (vx + vw, vy + vh), vcol, 1)
        cv2.putText(canvas, vlbl, (vx + 8, vy + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.33, vcol, 1, cv2.LINE_AA)

        sx, sy, sw, sh = _DASH_VOL_SLIDER
        v_level = _VOL_MGR.get_level()
        fill_w = int(max(0.0, min(1.0, v_level)) * (sw - 2))
        cv2.rectangle(canvas, (sx, sy), (sx + sw, sy + sh), (18, 22, 28), -1)
        if fill_w > 0:
            fill_tint = (int(vcol[0] * 0.35), int(vcol[1] * 0.35), int(vcol[2] * 0.35))
            cv2.rectangle(canvas, (sx + 1, sy + 1), (sx + 1 + fill_w, sy + sh - 1), fill_tint, -1)
            cv2.rectangle(canvas, (sx + fill_w - 1, sy + 1), (sx + fill_w + 1, sy + sh - 1), vcol, -1)
        cv2.rectangle(canvas, (sx, sy), (sx + sw, sy + sh), BORDER, 1)

        vol_pct_str = f"VOL {int(v_level * 100)}%"
        (tw, th), _ = cv2.getTextSize(vol_pct_str, cv2.FONT_HERSHEY_SIMPLEX, 0.32, 1)
        tx = sx + (sw - tw) // 2
        ty = sy + (sh + th) // 2 - 1
        cv2.putText(canvas, vol_pct_str, (tx + 1, ty + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (10, 12, 16), 1, cv2.LINE_AA)
        cv2.putText(canvas, vol_pct_str, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.32, WHITE_TEXT, 1, cv2.LINE_AA)

    # 4. View: SUBTITLES (Expanded Stream)
    elif TILE_MODE == "subtitles":
        cv2.rectangle(canvas, (35, 512), (1250, 638), (16, 18, 24), -1)
        cv2.rectangle(canvas, (35, 512), (1250, 638), BORDER, 1)
        cv2.putText(canvas, "[ DIRECTIVE & SYNTHESIS STREAM // FULL TRANSCRIPT VIEW ]", (45, 528),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, ACC, 1, cv2.LINE_AA)
        cv2.line(canvas, (35, 534), (1250, 534), BORDER, 1)
        lines = textwrap.wrap(SUBTITLE_TEXT, width=105)[:5]
        sy = 554
        for l in lines:
            cv2.putText(canvas, l, (45, sy), cv2.FONT_HERSHEY_SIMPLEX, 0.40, WHITE_TEXT, 1, cv2.LINE_AA)
            sy += 20

    # 5. View: AUDIO (Expanded High-Res Audio Studio)
    elif TILE_MODE == "audio":
        cv2.rectangle(canvas, (35, 512), (1250, 638), (14, 16, 22), -1)
        cv2.rectangle(canvas, (35, 512), (1250, 638), BORDER, 1)
        cv2.putText(canvas, "[ AUDIO STUDIO // 80-BAND SPECTRUM & MULTI-TRACE OSCILLOSCOPE ]", (45, 528),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, ACC, 1, cv2.LINE_AA)
        cv2.putText(canvas, "SAMPLE RATE: 48000Hz | FASTER-WHISPER VAD: ARMED | TTS: NEURAL EDGE",
                    (750, 528), cv2.FONT_HERSHEY_SIMPLEX, 0.34, CYAN, 1, cv2.LINE_AA)
        cv2.line(canvas, (35, 534), (1250, 534), BORDER, 1)

        # 80 FFT Bars
        for i in range(80):
            bx = 45 + i * 15
            bh = int(abs(np.sin(t * 12 + i * 0.25) * np.cos(t * 5 + i * 0.1)) * 38) + 4
            cv2.line(canvas, (bx, 580), (bx, 580 - bh), CYAN if i % 2 == 0 else ACC, 2)
            cv2.circle(canvas, (bx, max(540, 580 - bh - 2)), 1, (255, 255, 255), -1)

        # Dual trace oscilloscope
        cv2.line(canvas, (45, 614), (1240, 614), (25, 30, 40), 1)
        pts_a, pts_b = [], []
        for px in range(45, 1241, 6):
            ph = (px - 45) / 1195.0
            wy1 = 614 + int(np.sin(ph * 24.0 + t * 10.0) * 16)
            wy2 = 614 + int(np.cos(ph * 18.0 - t * 8.0) * 12)
            pts_a.append((px, wy1))
            pts_b.append((px, wy2))
        for k in range(len(pts_a) - 1):
            cv2.line(canvas, pts_a[k], pts_a[k + 1], GREEN, 1, cv2.LINE_AA)
            cv2.line(canvas, pts_b[k], pts_b[k + 1], PINK, 1, cv2.LINE_AA)

    # 6. View: TASKS (Expanded Task Fleet & Scheduler)
    elif TILE_MODE == "tasks":
        cv2.rectangle(canvas, (35, 512), (1250, 638), (16, 18, 24), -1)
        cv2.rectangle(canvas, (35, 512), (1250, 638), BORDER, 1)
        cv2.putText(canvas, "[ TASK FLEET // SCHEDULER & ACTIONS MATRIX ]", (45, 528),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, ACC, 1, cv2.LINE_AA)
        cv2.line(canvas, (35, 534), (1250, 534), BORDER, 1)

        # Col 1: Recent logs
        cv2.putText(canvas, "// RECENT ACTIONS", (45, 550), cv2.FONT_HERSHEY_SIMPLEX, 0.34, CYAN, 1, cv2.LINE_AA)
        ly = 568
        for log in LOG_STREAM[-3:]:
            cv2.putText(canvas, log[:50], (45, ly), cv2.FONT_HERSHEY_SIMPLEX, 0.34, WHITE_TEXT, 1, cv2.LINE_AA)
            ly += 18

        # Col 2: Timers
        cv2.line(canvas, (430, 534), (430, 638), BORDER, 1)
        cv2.putText(canvas, "// SCHEDULER TIMERS", (445, 550), cv2.FONT_HERSHEY_SIMPLEX, 0.34, CYAN, 1, cv2.LINE_AA)
        try:
            from aria.scheduler import sched_list
            items = sched_list()
            if items:
                ty = 568
                for it in items[:3]:
                    cv2.putText(canvas, f"- {str(it)[:46]}", (445, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.34, WHITE_TEXT, 1, cv2.LINE_AA)
                    ty += 18
            else:
                cv2.putText(canvas, "No pending reminders or timers.", (445, 574), cv2.FONT_HERSHEY_SIMPLEX, 0.34, DIM, 1, cv2.LINE_AA)
        except Exception:
            cv2.putText(canvas, "Scheduler online.", (445, 574), cv2.FONT_HERSHEY_SIMPLEX, 0.34, DIM, 1, cv2.LINE_AA)

        # Col 3: Subsystems
        cv2.line(canvas, (840, 534), (840, 638), BORDER, 1)
        cv2.putText(canvas, "// SUBSYSTEM MATRIX", (855, 550), cv2.FONT_HERSHEY_SIMPLEX, 0.34, CYAN, 1, cv2.LINE_AA)
        chips = _get_active_task_chips()
        cy = 568
        for lbl, val, col in chips:
            cv2.putText(canvas, f"{lbl}: {val}", (855, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.34, col, 1, cv2.LINE_AA)
            cy += 18

    # 7. View: SPOTIFY (Expanded Music Deck)
    elif TILE_MODE == "spotify":
        cv2.rectangle(canvas, (35, 512), (1250, 638), (16, 18, 24), -1)
        cv2.rectangle(canvas, (35, 512), (1250, 638), BORDER, 1)
        cv2.putText(canvas, "[ SPOTIFY MUSIC DECK // TRANSPORT & PLAYLISTS ]", (45, 528),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, ACC, 1, cv2.LINE_AA)
        cv2.line(canvas, (35, 534), (1250, 534), BORDER, 1)

        spot = _get_spotify_telemetry()
        sp_stat_col = GREEN if spot["playing"] else (AMBER if spot["running"] else DIM)
        sp_stat_lbl = "PLAYING" if spot["playing"] else ("PAUSED" if spot["running"] else "STANDBY")

        cv2.rectangle(canvas, (45, 545), (125, 570), (22, 28, 36), -1)
        cv2.rectangle(canvas, (45, 545), (125, 570), sp_stat_col, 1)
        cv2.putText(canvas, sp_stat_lbl, (52, 562), cv2.FONT_HERSHEY_SIMPLEX, 0.38, sp_stat_col, 1, cv2.LINE_AA)

        track_info = f"NOW PLAYING: {spot['title']}" if spot["title"] else "NOW PLAYING: Spotify Offline"
        cv2.putText(canvas, track_info, (140, 562), cv2.FONT_HERSHEY_SIMPLEX, 0.44, WHITE_TEXT, 1, cv2.LINE_AA)

        # Equalizer line
        for i in range(40):
            bx = 820 + i * 10
            bh = int(abs(np.sin(t * 10 + i * 0.4)) * 18) + 2 if spot["playing"] else 3
            cv2.line(canvas, (bx, 570), (bx, 570 - bh), GREEN if spot["playing"] else DIM, 2)

        # Control buttons
        exp_btns = [
            (_EXP_SPOTIFY_PREV,   "|< PREV", DIM),
            (_EXP_SPOTIFY_PLAY,   "> / || PLAY/PAUSE", CYAN if spot["playing"] else GREEN),
            (_EXP_SPOTIFY_NEXT,   "NEXT >|", DIM),
            (_EXP_SPOTIFY_DJ,     "DJ: LIKED", (200, 140, 255)),
            (_EXP_SPOTIFY_FOCUS,  "OPEN APP", (160, 180, 200)),
            (_EXP_SPOTIFY_CHILL,  "CHILL", (100, 200, 240)),
            (_EXP_SPOTIFY_FOCUSM, "FOCUS", (140, 240, 180)),
            (_EXP_SPOTIFY_ENERGY, "ENERGY", (240, 160, 80)),
        ]
        for (bx, by, bw, bh), blabel, bcol in exp_btns:
            cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), (24, 28, 36), -1)
            cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), bcol, 1)
            cv2.putText(canvas, blabel, (bx + 8, by + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.34, bcol, 1, cv2.LINE_AA)

        # Volume controls in Expanded Spotify view
        vtgt = _VOL_MGR.target
        vcol = CYAN if vtgt == "device" else GREEN
        v_level = _VOL_MGR.get_level()

        evx, evy, evw, evh = _EXP_VOL_TARGET_BTN
        cv2.rectangle(canvas, (evx, evy), (evx + evw, evy + evh), (24, 28, 36), -1)
        cv2.rectangle(canvas, (evx, evy), (evx + evw, evy + evh), vcol, 1)
        ev_lbl = "DEV VOL" if vtgt == "device" else "SPOT VOL"
        cv2.putText(canvas, ev_lbl, (evx + 9, evy + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.34, vcol, 1, cv2.LINE_AA)

        esx, esy, esw, esh = _EXP_VOL_SLIDER
        efill_w = int(max(0.0, min(1.0, v_level)) * (esw - 2))
        cv2.rectangle(canvas, (esx, esy), (esx + esw, esy + esh), (18, 22, 28), -1)
        if efill_w > 0:
            fill_tint = (int(vcol[0] * 0.35), int(vcol[1] * 0.35), int(vcol[2] * 0.35))
            cv2.rectangle(canvas, (esx + 1, esy + 1), (esx + 1 + efill_w, esy + esh - 1), fill_tint, -1)
            cv2.rectangle(canvas, (esx + efill_w - 2, esy + 1), (esx + efill_w + 1, esy + esh - 1), vcol, -1)
        cv2.rectangle(canvas, (esx, esy), (esx + esw, esy + esh), BORDER, 1)
        evol_str = f"{'DEVICE' if vtgt == 'device' else 'SPOTIFY'} VOL: {int(v_level * 100)}%"
        (etw, eth), _ = cv2.getTextSize(evol_str, cv2.FONT_HERSHEY_SIMPLEX, 0.34, 1)
        etx = esx + (esw - etw) // 2
        ety = esy + (esh + eth) // 2 - 1
        cv2.putText(canvas, evol_str, (etx + 1, ety + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (10, 12, 16), 1, cv2.LINE_AA)
        cv2.putText(canvas, evol_str, (etx, ety), cv2.FONT_HERSHEY_SIMPLEX, 0.34, WHITE_TEXT, 1, cv2.LINE_AA)



def _ops_panel(canvas, x1, y1, x2, y2, title, ACC, stale=False):
    cv2.rectangle(canvas, (x1, y1), (x2, y2), PANEL_BG, -1)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), BORDER, 1)
    cv2.putText(canvas, "[ " + title + " ]", (x1 + 12, y1 + 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, ACC, 1, cv2.LINE_AA)
    if stale:
        cv2.putText(canvas, "(stale)", (x2 - 72, y1 + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, DIM, 1, cv2.LINE_AA)
    return x1 + 12, y1 + 46


def _ops_row(canvas, x, y, text, color=WHITE_TEXT, scale=0.38, max_chars=52):
    t = hud_ascii(text)
    if len(t) > max_chars:
        t = t[:max_chars - 1] + "..."
    cv2.putText(canvas, t, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale,
                color, 1, cv2.LINE_AA)


def _ops_bar(canvas, x, y, w, pct, color):
    pct = max(0.0, min(100.0, float(pct)))
    cv2.rectangle(canvas, (x, y), (x + w, y + 10), (30, 34, 44), -1)
    fw = int(w * pct / 100.0)
    if fw > 1:
        cv2.rectangle(canvas, (x, y), (x + fw, y + 10), color, -1)


def _ops_spark(canvas, x, y, w, h, hist, color):
    if not hist or len(hist) < 2:
        return
    n = len(hist)
    bw = max(2, w // n)
    for i, v in enumerate(hist):
        bh = int(h * max(0.0, min(100.0, float(v))) / 100.0)
        bx = x + i * bw
        if bh > 0:
            cv2.rectangle(canvas, (bx, y + h - bh), (bx + bw - 1, y + h),
                          color, -1)


def _draw_ops_body(canvas, ACC, ACC2):
    """OPS command center dashboard. Drawn when HUD_MODE == 'ops'."""
    from datetime import timedelta
    dash = _ops.get_dashboard()
    now = datetime.now()

    def entry(name):
        return dash.get(name) or {"data": None, "stale": True}

    # Title override
    cv2.rectangle(canvas, (28, 16), (700, 46), BG, -1)
    cv2.putText(canvas, "A.R.I.A. // OPS COMMAND CENTER", (30, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, ACC, 2, cv2.LINE_AA)

    # ---- attention strip
    cv2.rectangle(canvas, (20, 82), (1260, 118), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 82), (1260, 118), BORDER, 1)
    attn = _ops.compute_attention(dash, now)
    if attn:
        cv2.putText(canvas, "ATTENTION", (34, 106),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 180, 255), 1, cv2.LINE_AA)
        _ops_row(canvas, 170, 106, " | ".join(attn), (255, 200, 120), 0.4, 110)
    else:
        cv2.putText(canvas, "STATUS", (34, 106),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREEN, 1, cv2.LINE_AA)
        _ops_row(canvas, 170, 106, "All clear.", GREEN, 0.4, 110)

    sched_e, inbox_e, tasks_e = entry("schedule"), entry("inbox"), entry("tasks")
    prov_e, sys_e = entry("providers"), entry("systems")
    sched = (sched_e["data"] or {})
    inbox = (inbox_e["data"] or {})
    tasks = (tasks_e["data"] or {})
    prov = (prov_e["data"] or {})
    sysd = (sys_e["data"] or {})

    # ---- left column: day timeline + schedule
    px, rx, ry = _ops_panel(canvas, 20, 128, 430, 200, "DAY", ACC)
    events = sched.get("events", []) if sched.get("connected") else []
    if events:
        blocks = _ops.compute_timeline(events, now)
        midnight = now.replace(hour=0, minute=0, second=0,
                               microsecond=0) + timedelta(days=1)
        span = max(1.0, (midnight - now).total_seconds())
        bx1, bx2, by = px, 430 - 14, ry + 6
        cv2.rectangle(canvas, (bx1, by), (bx2, by + 14), (30, 34, 44), -1)
        for b in blocks:
            x0 = int(bx1 + b["x0"] * (bx2 - bx1))
            x1b = int(bx1 + b["x1"] * (bx2 - bx1))
            col = ACC if b["active"] else (90, 140, 200)
            cv2.rectangle(canvas, (x0, by), (max(x0 + 2, x1b), by + 14), col, -1)
        for h in range(now.hour + 1, 24):
            frac = (now.replace(hour=h, minute=0, second=0,
                                microsecond=0) - now).total_seconds() / span
            tx = int(bx1 + frac * (bx2 - bx1))
            cv2.line(canvas, (tx, by), (tx, by + 14), BORDER, 1)
            if h % 3 == 0:
                cv2.putText(canvas, f"{h % 12 or 12}p", (tx - 8, by + 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.3, DIM, 1, cv2.LINE_AA)
    else:
        _ops_row(canvas, px, ry + 16,
                 "no calendar" if not sched.get("connected") else "nothing scheduled",
                 DIM, 0.36, 40)

    px, ry = _ops_panel(canvas, 20, 210, 430, 436, "SCHEDULE", ACC,
                        sched_e["stale"])
    if not sched.get("connected"):
        _ops_row(canvas, px, ry, "set ICAL_URL in aria_keys.json", DIM, 0.36, 40)
    else:
        evs = [e for e in events if e["end"] > now][:8]
        if not evs:
            _ops_row(canvas, px, ry, "nothing upcoming", DIM, 0.36, 40)
        for i, e in enumerate(evs):
            y = ry + i * 20
            if y > 428:
                break
            active = e["start"] <= now <= e["end"]
            if e["all_day"]:
                txt = f"[all day] {e['summary']}"
            else:
                s = e["start"].strftime("%I:%M%p").lstrip("0").lower()
                txt = f"{s} {e['summary']}"
            _ops_row(canvas, px + (14 if active else 0), y,
                     ("> " if active else "") + txt,
                     ACC if active else WHITE_TEXT, 0.36, 42)

    # ---- middle column: inbox + providers
    px, ry = _ops_panel(canvas, 445, 128, 845, 322, "INBOX", ACC,
                        inbox_e["stale"])
    items = inbox.get("items", [])
    if not inbox.get("connected", True):
        _ops_row(canvas, px, ry, "no Gmail credentials", DIM, 0.36, 40)
    elif not items:
        _ops_row(canvas, px, ry, "inbox zero", GREEN, 0.36, 40)
    else:
        for i, it in enumerate(items[:7]):
            y = ry + i * 20
            if y > 314:
                break
            if i == _ops.SELECTED_MAIL:
                cv2.rectangle(canvas, (445 + 4, y - 14),
                              (845 - 4, y + 4), (28, 38, 52), -1)
            star = "*" if it.get("important") else " "
            _ops_row(canvas, px, y,
                     f"{i + 1}{star} {it['sender']} | {it['subject']}",
                     ACC2 if it.get("important") else WHITE_TEXT, 0.34, 46)

    px, ry = _ops_panel(canvas, 445, 332, 845, 436, "PROVIDERS", ACC,
                        prov_e["stale"])
    chain = prov.get("chain", [])
    if not chain:
        _ops_row(canvas, px, ry, "loading...", DIM, 0.36, 40)
    else:
        for i, c in enumerate(chain):
            y = ry + i * 20
            col = {"live": GREEN, "standby": DIM,
                   "quarantined": (60, 180, 255)}.get(c["state"], DIM)
            mark = ">" if c["name"] == prov.get("active") else " "
            det = f" ({c['detail']})" if c.get("detail") else ""
            _ops_row(canvas, px, y,
                     f"{mark} {c['name']}{det}", col, 0.36, 44)
        stats = prov.get("stats", {})
        _ops_row(canvas, px, ry + len(chain) * 20,
                 f"failovers: {stats.get('failovers', 0)}", DIM, 0.34, 44)

    # ---- right column: tasks
    px, ry = _ops_panel(canvas, 860, 128, 1260, 436, "TASKS", ACC,
                        tasks_e["stale"])
    titems = tasks.get("items", [])
    if not titems:
        _ops_row(canvas, px, ry, "no scheduled tasks", DIM, 0.36, 40)
    for i, t in enumerate(titems[:12]):
        y = ry + i * 20
        if y > 428:
            break
        cd = _ops.fmt_countdown(t["delta_s"])
        rec = " (R)" if t["recurring"] else ""
        _ops_row(canvas, px, y, f"{t['prompt']}{rec} -- {cd}",
                 (60, 180, 255) if t["overdue"] else WHITE_TEXT, 0.34, 46)

    # ---- systems strip
    px, ry = _ops_panel(canvas, 20, 446, 1260, 636, "SYSTEMS", ACC,
                        sys_e["stale"])
    if not sysd.get("available"):
        _ops_row(canvas, px, ry + 10, "pip install psutil for live stats",
                 DIM, 0.38, 60)
    else:
        # bars
        labels = [("CPU", sysd["cpu"], GREEN), ("MEM", sysd["mem"], CYAN),
                  ("DISK", sysd["disk"],
                   (60, 180, 255) if sysd["disk"] > 90 else WHITE_TEXT)]
        bx = px
        for lab, pct, col in labels:
            cv2.putText(canvas, lab, (bx, ry + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, DIM, 1, cv2.LINE_AA)
            _ops_bar(canvas, bx + 52, ry + 1, 150, pct, col)
            cv2.putText(canvas, f"{pct:.0f}%", (bx + 210, ry + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, WHITE_TEXT, 1,
                        cv2.LINE_AA)
            bx += 300
        # sparklines
        _ops_spark(canvas, px, ry + 30, 260, 40, sysd.get("cpu_hist"), GREEN)
        cv2.putText(canvas, "cpu hist", (px, ry + 84),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, DIM, 1, cv2.LINE_AA)
        _ops_spark(canvas, px + 300, ry + 30, 260, 40, sysd.get("mem_hist"), CYAN)
        cv2.putText(canvas, "mem hist", (px + 300, ry + 84),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.3, DIM, 1, cv2.LINE_AA)
        # net / uptime / battery
        net_txt = (f"NET ^ {sysd['up_kbs']:.0f} KB/s  v {sysd['down_kbs']:.0f} KB/s"
                   f"    UP {_ops.fmt_uptime(sysd['uptime_s'])}")
        b = sysd.get("batt")
        if b:
            net_txt += f"    BATT {b['pct']}%{' (chg)' if b['plugged'] else ''}"
        _ops_row(canvas, px + 620, ry + 10, net_txt, DIM, 0.36, 60)
        fails = (prov.get("stats") or {}).get("last_failover")
        if fails:
            _ops_row(canvas, px + 620, ry + 34,
                     f"last failover: {fails[0]} "
                     f"{int((now.timestamp() - fails[1]) // 60)}m ago",
                     DIM, 0.34, 60)

    # footer hints
    _ops_row(canvas, 34, 664,
             "[O] face   [1-8] select mail   [E] read aloud   [R] refresh"
             "   [T] directive   [V] visor",
             DIM, 0.36, 120)


def draw_hud() -> np.ndarray:
    """Render full 1280x720 HUD frame and publish face frame for phone bridge."""
    ACC, ACC2 = theme_colors()
    h, w = 720, 1280
    canvas = np.zeros((h, w, 3), dtype=np.uint8)
    canvas[:] = BG

    # Grid background
    for x in range(0, w, 40):
        cv2.line(canvas, (x, 0), (x, h), (18, 20, 24), 1)
    for y in range(0, h, 40):
        cv2.line(canvas, (0, y), (w, y), (18, 20, 24), 1)

    # Panels
    cv2.rectangle(canvas, (20, 70), (280, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 70), (280, 460), BORDER, 1)
    cv2.rectangle(canvas, (1000, 70), (1260, 460), PANEL_BG, -1)
    cv2.rectangle(canvas, (1000, 70), (1260, 460), BORDER, 1)
    cv2.rectangle(canvas, (20, 480), (1260, 705), PANEL_BG, -1)
    cv2.rectangle(canvas, (20, 480), (1260, 705), BORDER, 1)

    # Header
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cpu_usage, mem_usage, bat_str, cached_subs = get_cached_telemetry()
    inbox_cnt = inbox_count_cached()

    cv2.putText(canvas, "A.R.I.A. // Adaptive Robotic Intelligence Agent", (30, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, ACC, 2, cv2.LINE_AA)

    sys_stats = f"TIME: {now_str}  |  CPU: {cpu_usage}%  |  MEM: {mem_usage}%  |  PWR: {bat_str}"
    cv2.putText(canvas, sys_stats, (650, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 170, 180), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"PHONE BRIDGE: https://{lan_ip()}:{PHONE_BRIDGE_PORT}   |   INBOX: {inbox_cnt}",
                (30, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (120, 150, 170), 1, cv2.LINE_AA)

    # Whisper Mode Button
    bx, by, bw, bh = _WHISPER_BTN
    _wcol = (200, 120, 255) if WHISPER_MODE else (70, 80, 95)
    cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), PANEL_BG, -1)
    cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), _wcol, 1)
    cv2.putText(canvas, "WHISPER " + ("ON" if WHISPER_MODE else "OFF"),
                (bx + 10, by + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.34, _wcol, 1, cv2.LINE_AA)

    # Mood String
    _mw = (_MOOD_CALLBACK().lower() if _MOOD_CALLBACK else "calm")
    mood_str = "MOOD: " + (_MOOD_CALLBACK().upper() if _MOOD_CALLBACK else "CALM")
    (mw, _), _ = cv2.getTextSize(mood_str, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
    cv2.putText(canvas, mood_str, (bx - 20 - mw, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (170, 190, 200), 1, cv2.LINE_AA)

    cv2.line(canvas, (20, 72), (1260, 72), ACC, 1)

    if HUD_MODE == "ops":
        _draw_ops_body(canvas, ACC, ACC2)
        status_bar = ("STATUS: OPS COMMAND CENTER  |  [O] FACE  |  [1-8] SELECT MAIL"
                      "  |  [E] READ ALOUD  |  [R] REFRESH  |  [T] DIRECTIVE")
        cv2.putText(canvas, status_bar, (35, 700), cv2.FONT_HERSHEY_SIMPLEX,
                    0.35, DIM, 1, cv2.LINE_AA)
        if SHOW_COMMANDS:
            _draw_commands_overlay(canvas)
        return canvas

    # Left Panel: Subsystems
    cv2.putText(canvas, "[ SUBSYSTEMS ]", (35, 102), cv2.FONT_HERSHEY_SIMPLEX, 0.5, ACC, 1, cv2.LINE_AA)
    subsystems = cached_subs
    for i, (mod, stat, ok) in enumerate(subsystems[:8]):
        item_y = 135 + (i * 22)
        dot = GREEN if ok else (40, 60, 240)
        cv2.circle(canvas, (43, item_y - 5), 4, dot, -1)
        (tw, _), _ = cv2.getTextSize(stat, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
        stat_x = 275 - tw
        cv2.putText(canvas, stat, (stat_x, item_y), cv2.FONT_HERSHEY_SIMPLEX, 0.38, dot, 1, cv2.LINE_AA)
        cv2.putText(canvas, mod, (55, item_y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv2.LINE_AA)

    # Optical PIP
    pip_x, pip_y, pip_w, pip_h = 35, 315, 230, 130
    cv2.rectangle(canvas, (pip_x, pip_y), (pip_x + pip_w, pip_y + pip_h), (30, 35, 45), -1)
    # NOTE: read the frame live off the vision module every draw. A
    # `from aria.vision import LATEST_CAMERA_FRAME` here would bind the
    # import-time value (None) forever, since vision.py *rebinds* the name
    # on each capture — the PIP would never render. (Fixed 2026-10-05.)
    _cam_frame = _vision.LATEST_CAMERA_FRAME
    if _cam_frame is not None:
        try:
            thumb = cv2.resize(_cam_frame, (pip_w, pip_h))
            canvas[pip_y:pip_y + pip_h, pip_x:pip_x + pip_w] = thumb
        except Exception:
            pass
    cv2.rectangle(canvas, (pip_x, pip_y), (pip_x + pip_w, pip_y + pip_h), BORDER, 1)
    cv2.putText(canvas, "CAM.01 // OPTIC PIP", (pip_x + 10, pip_y + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, CYAN, 1, cv2.LINE_AA)

    # Right Panel: Action Stream
    cv2.putText(canvas, "[ ACTION STREAM ]", (1015, 102), cv2.FONT_HERSHEY_SIMPLEX, 0.5, ACC, 1, cv2.LINE_AA)
    log_y = 130
    for log in LOG_STREAM[-6:]:
        for sub_line in textwrap.wrap(log, width=32)[:2]:
            cv2.putText(canvas, sub_line, (1015, log_y), cv2.FONT_HERSHEY_SIMPLEX, 0.38, WHITE_TEXT, 1, cv2.LINE_AA)
            log_y += 18

    # Center Stage: Avatar or Chat Log
    if HUD_MODE == "chat_log":
        cv2.rectangle(canvas, (300, 76), (980, 460), (12, 14, 18), -1)
        cv2.rectangle(canvas, (300, 76), (980, 460), ACC, 1)

        # Header bar with clean separation and zero text overlap
        title_text = "[ TACTICAL CHAT LOG ]"
        cv2.putText(canvas, title_text, (315, 101),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, CYAN, 1, cv2.LINE_AA)

        ctrl_text = "[V] VISOR  |  [UP/DN / WHEEL] SCROLL"
        (cw, _), _ = cv2.getTextSize(ctrl_text, cv2.FONT_HERSHEY_SIMPLEX, 0.34, 1)
        cv2.putText(canvas, ctrl_text, (965 - cw, 101),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, DIM, 1, cv2.LINE_AA)

        # Divider line between header and chat log lines
        cv2.line(canvas, (300, 114), (980, 114), BORDER, 1)

        try:
            from aria import memory
            raw_log = list(DISPLAY_CHAT_LOG) if DISPLAY_CHAT_LOG else list(memory.get_display_chat_log())
            rendered_lines = []
            for entry in raw_log:
                if not entry:
                    continue
                if isinstance(entry, (tuple, list)):
                    if len(entry) >= 3:
                        ts, role, text = entry[0], entry[1], entry[2]
                        ts_str = str(ts).strip()
                        if len(ts_str) > 8 and " " in ts_str:
                            ts_str = ts_str.split()[-1]
                        header = hud_ascii(f"[{ts_str}] {role}: ")
                    elif len(entry) == 2:
                        role, text = entry[0], entry[1]
                        header = hud_ascii(f"{role}: ")
                    else:
                        role, text = "MSG", str(entry[0])
                        header = "MSG: "
                else:
                    role, text = "MSG", str(entry)
                    header = "MSG: "

                header_color = ACC if str(role).upper() in ("A.R.I.A.", "ARIA") else CYAN
                (hw, _), _ = cv2.getTextSize(header, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
                content_x = max(465, 315 + hw + 8)
                msg_lines = textwrap.wrap(hud_ascii(text), width=58)
                if msg_lines:
                    rendered_lines.append([(315, header, header_color), (content_x, msg_lines[0], WHITE_TEXT)])
                    for sub_line in msg_lines[1:]:
                        rendered_lines.append([(content_x, sub_line, WHITE_TEXT)])

            max_visible = 14
            max_scroll = max(0, len(rendered_lines) - max_visible)
            effective_scroll = max(0, min(CHAT_SCROLL, max_scroll))
            start_idx = max(0, len(rendered_lines) - max_visible - effective_scroll)
            view_lines = rendered_lines[start_idx:start_idx + max_visible]
            log_y = 134
            for line_segs in view_lines:
                for x, text, col in line_segs:
                    cv2.putText(canvas, text, (x, log_y), cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1, cv2.LINE_AA)
                log_y += 22
        except Exception as e:
            cv2.putText(canvas, f"Chat log render error: {hud_ascii(e)}", (320, 140),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1, cv2.LINE_AA)
    else:
        # Visor Mode (Pixel Avatar with Fluid Cognitive Reactions)
        lx, rx, cy = 520, 760, 235
        if USE_PIXEL_AVATAR:
            draw_pixel_aria(canvas, CURRENT_STATE, time.time(), mood=_mw)
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
            wt = now * 2.0
            for i in range(-9, 10):
                bar_x = 640 + (i * 14)
                bar_h = int(abs(np.sin(wt + i * 0.5)) * 8) + 2
                cv2.line(canvas, (bar_x, 388 - bar_h), (bar_x, 388 + bar_h), PINK, 2)
        elif CURRENT_STATE == "listening":
            pulse = int(6 * np.sin(time.time() * 4))
            for ex, side in ((lx, -1), (rx, 1)):
                cv2.circle(canvas, (ex, cy), 58 + pulse, CYAN, 2)
                cv2.circle(canvas, (ex, cy), 46 + pulse, PINK, 3)
                _draw_lashes(canvas, ex, cy, 46 + pulse, 46 + pulse, side)
                cv2.circle(canvas, (ex, cy), 18, (255, 255, 255), -1)
                apply_led_scanlines(canvas, ex - 60, cy - 70, ex + 60, cy + 70)
            _draw_waveform_mouth(canvas, MOUTH_YELLOW, time.time())
        elif CURRENT_STATE == "thinking":
            t = time.time() * 8
            for ex, side in ((lx, -1), (rx, 1)):
                cv2.ellipse(canvas, (ex, cy), (48, 48), 0, int(t * 10) % 360,
                            (int(t * 10) + 220) % 360, CYAN, 5)
                _draw_lashes(canvas, ex, cy, 48, 48, side)
                cv2.putText(canvas, "?", (ex - 12, cy + 12), cv2.FONT_HERSHEY_SIMPLEX,
                            1.1, CYAN, 2, cv2.LINE_AA)
                apply_led_scanlines(canvas, ex - 60, cy - 70, ex + 60, cy + 70)
            _draw_waveform_mouth(canvas, MOUTH_YELLOW, t)
        elif CURRENT_STATE in ("working", "coding"):
            t = time.time() * 10
            for ex, side in ((lx, -1), (rx, 1)):
                cv2.ellipse(canvas, (ex, cy), (48, 48), 0, int(t * 12) % 360,
                            (int(t * 12) + 260) % 360, GREEN, 5)
                _draw_lashes(canvas, ex, cy, 48, 48, side)
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

        publish_face_frame(canvas)

    # Bottom Area: Collapsible Context Tiles
    _draw_context_tiles(canvas, ACC, ACC2, time.time(), CURRENT_STATE)

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
    px, py, pw, ph = _INPUT_PASTE_BTN
    cv2.rectangle(canvas, (px, py), (px + pw, py + ph), (28, 42, 54), -1)
    cv2.rectangle(canvas, (px, py), (px + pw, py + ph), CYAN if TYPING_ACTIVE else (100, 140, 160), 1)
    cv2.putText(canvas, "PASTE", (px + 12, py + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, CYAN if TYPING_ACTIVE else (180, 200, 215), 1, cv2.LINE_AA)

    sx, sy, sw, sh = _INPUT_SEND_BTN
    send_ready = bool(clean_buf.strip())
    send_bg = (20, 52, 28) if send_ready else (16, 24, 18)
    send_border = GREEN if send_ready else (40, 70, 45)
    send_txt_color = GREEN if send_ready else (80, 120, 90)
    cv2.rectangle(canvas, (sx, sy), (sx + sw, sy + sh), send_bg, -1)
    cv2.rectangle(canvas, (sx, sy), (sx + sw, sy + sh), send_border, 1)
    cv2.putText(canvas, "SEND", (sx + 13, sy + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, send_txt_color, 1, cv2.LINE_AA)

    cx, cy, cw, ch = _INPUT_CLEAR_BTN
    cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), (26, 28, 32), -1)
    cv2.rectangle(canvas, (cx, cy), (cx + cw, cy + ch), BORDER, 1)
    cv2.putText(canvas, "CLR", (cx + 12, cy + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, DIM, 1, cv2.LINE_AA)

    ex, ey, ew, eh = _INPUT_ESC_BTN
    cv2.rectangle(canvas, (ex, ey), (ex + ew, ey + eh), (32, 22, 22) if TYPING_ACTIVE else (24, 26, 30), -1)
    cv2.rectangle(canvas, (ex, ey), (ex + ew, ey + eh), (90, 70, 130) if TYPING_ACTIVE else BORDER, 1)
    esc_label = "ESC" if TYPING_ACTIVE else "[T]"
    cv2.putText(canvas, esc_label, (ex + 18, ey + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, DIM, 1, cv2.LINE_AA)

    # Bottom status bar
    status_bar = f"STATUS: {CURRENT_STATE.upper()}  |  [T] TYPE  |  [1-5] TILES  |  [TAB] FLIP  |  [SPACE] PTT  |  [X] CUT  |  [C] COLORS  |  [V] VISOR  |  [O] OPS  |  [H] COMMANDS"
    cv2.putText(canvas, status_bar, (35, 700), cv2.FONT_HERSHEY_SIMPLEX, 0.35, DIM, 1, cv2.LINE_AA)

    if SHOW_COMMANDS:
        _draw_commands_overlay(canvas)

    return canvas
