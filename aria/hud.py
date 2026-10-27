"""
ARIA HUD (Heads-Up Display) and Visual Interface.
Renders the 1280x720 cybernetic interface, animated cyber-girl face,
waveform mouth, system telemetry, optical PIP, and interactive context tiles:
Live Audio Waveforms, Active Task Chips, and Spotify Telemetry.
"""

from __future__ import annotations

import math
import random
import socket
import threading
import time
from datetime import datetime
from typing import List, Tuple, Dict, Any

import cv2
import numpy as np
import psutil
from aria.pixel_avatar import draw_pixel_aria, load_theme

load_theme()  # restore Alek's saved HUD color theme

from aria.vision import publish_face_frame
from aria.tools.schemas import COMMAND_GUIDE
from aria import config as _config_silent

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
        except Exception as _e_silent:
            _config_silent.log_silent("get_cached_telemetry", _e_silent)

        if _SUBSYSTEMS_CALLBACK:
            try:
                _CACHED_TELEMETRY["subsystems"] = _SUBSYSTEMS_CALLBACK() or []
            except Exception as _e_silent:
                _config_silent.log_silent("get_cached_telemetry", _e_silent)

    return (
        _CACHED_TELEMETRY["cpu"],
        _CACHED_TELEMETRY["mem"],
        _CACHED_TELEMETRY["battery"],
        _CACHED_TELEMETRY["subsystems"]
    )






# Spotify Telemetry Cache
_SPOTIFY_CACHE: Dict[str, Any] = {
    "time": 0.0,
    "running": False,
    "playing": False,
    "status": "off",
    "source": "none",
    "title": "",
    "artist": "",
    "track": ""
}


def _get_spotify_telemetry() -> Dict[str, Any]:
    """What's actually playing, without lag. Primary source is
    SMTC (aria/smtc.py) — the OS's own per-app now-playing: real
    track / artist and real playing-vs-paused status. The old
    window-title scrape remains as fallback; it cannot tell
    paused from playing (a paused Spotify keeps the track in its
    title), and there is no more "Spotify Standby" placeholder —
    an empty title means nothing to show, and the surfaces say
    so honestly."""
    global _SPOTIFY_CACHE
    now = time.time()
    if now - _SPOTIFY_CACHE["time"] < 0.6:
        return _SPOTIFY_CACHE

    info = {
        "time": now,
        "running": False,
        "playing": False,
        "status": "off",
        "source": "title",
        "title": "",
        "artist": "",
        "track": ""
    }
    try:
        from aria import smtc
        live = smtc.now_playing()
        if live:
            info.update(live)
            _SPOTIFY_CACHE = info
            return info
    except Exception:
        pass
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
                            info["status"] = "open"
                            info["title"] = txt
                        elif " - " in txt:
                            # Best guess only — the title cannot
                            # distinguish paused from playing.
                            info["playing"] = True
                            info["status"] = "playing"
                            parts = txt.split(" - ", 1)
                            info["artist"] = parts[0].strip()
                            info["track"] = parts[1].strip()
                            info["title"] = txt
                        else:
                            info["playing"] = True
                            info["status"] = "playing"
                            info["title"] = txt
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception as _e_silent:
        _config_silent.log_silent("_get_spotify_telemetry", _e_silent)

    _SPOTIFY_CACHE = info
    return info


def _get_volume_client():
    """The shared COM-host client (aria.comclient singleton). A
    function, not a module-level binding, so tests can substitute a
    fake client by patching this one name."""
    from aria.comclient import get_client
    return get_client()


class VolumeManager:
    """Non-blocking volume controller for Windows master device and
    Spotify audio session — now a facade over the COM host process
    (crash round 5, aria/comhost.py).

    History: this class used to own a pycaw/COM worker thread in
    this process. Four faulthandler dumps proved any in-process COM
    is a process-killer here (cross-apartment GC finalisation of
    comtypes wrappers), so ALL of pycaw moved into the helper
    process. What remains is the exact public surface consumers
    always used: cached attributes refreshed by a 1 s poll of the
    helper, setters that flip the cached state immediately (as they
    did when they queued desired state for the worker) and forward
    the op. Helper down -> attributes hold last-known values and
    setters no-op past the cache flip — the same outward behaviour
    the dead-worker path always had.
    """

    _POLL_INTERVAL_S = 1.0

    def __init__(self):
        self.target = "device"  # "device" or "spotify"
        self.device_vol = 0.5
        self.spotify_vol = 1.0
        self.device_muted = False
        self.spotify_muted = False
        self._lock = threading.Lock()
        self._started = False
        self._running = False
        self._poll_thread = None
        self._client = None

    def start(self):
        """Start polling the COM host (idempotent)."""
        if self._started:
            return
        self._started = True
        self._running = True
        self._client_now()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="aria-volume-poll")
        self._poll_thread.start()

    def _client_now(self):
        if self._client is None and self._started:
            try:
                self._client = _get_volume_client()
            except Exception as _e_silent:
                _config_silent.log_silent("VolumeManager._client_now",
                                          _e_silent)
                self._client = None
        return self._client

    def _poll_loop(self):
        while self._running:
            client = self._client_now()
            if client is not None:
                try:
                    state = client.request("volume.get_state",
                                           timeout=3.0)
                except Exception:
                    state = None  # host down: cached state holds
                if isinstance(state, dict):
                    with self._lock:
                        for attr in ("device_vol", "spotify_vol",
                                     "device_muted", "spotify_muted"):
                            val = state.get(attr)
                            if val is not None:
                                setattr(self, attr, val)
                    errors = state.get("errors")
                    if errors:
                        # As the old in-process worker did (Merger
                        # Phase 1): a failed volume read is a
                        # classified SKIP in the failure record.
                        try:
                            from aria.agent.failures import \
                                surface_failure
                            surface_failure(
                                "hud.volume_client",
                                "; ".join(str(e) for e in errors),
                                decision="skip",
                                user_message="A volume update failed "
                                             "and was skipped.")
                        except Exception:
                            pass
            time.sleep(self._POLL_INTERVAL_S)

    def _forward(self, op, payload):
        client = self._client_now()
        if client is None:
            return
        try:
            client.send(op, payload)
        except Exception:
            pass  # host down: the optimistic cache flip already landed

    def get_level(self) -> float:
        return self.device_vol if self.target == "device" else self.spotify_vol

    def set_level(self, level: float):
        level = max(0.0, min(1.0, float(level)))
        with self._lock:
            if self.target == "device":
                self.device_vol = level
                target = "device"
            else:
                self.spotify_vol = level
                target = "spotify"
        self._forward(
            "volume.set_device" if target == "device"
            else "volume.set_spotify",
            {"level": level})

    def toggle_target(self) -> str:
        self.target = "spotify" if self.target == "device" else "device"
        return self.target

    def get_muted(self) -> bool:
        return self.device_muted if self.target == "device" else self.spotify_muted

    def set_mute(self, muted: bool):
        """Flip the cached mute state immediately (readers see the
        intended state without touching COM) and forward the op to
        the COM host, which applies it on its own thread."""
        muted = bool(muted)
        with self._lock:
            if self.target == "device":
                self.device_muted = muted
                target = "device"
            else:
                self.spotify_muted = muted
                target = "spotify"
        self._forward("volume.set_mute",
                      {"target": target, "muted": muted})

    def is_started(self) -> bool:
        """True only while started AND the COM host is live — the
        availability contract tool_volume has always read."""
        if not self._started:
            return False
        client = self._client_now()
        return bool(client is not None and client.is_up())

_VOL_MGR = VolumeManager()


def start_volume_manager():
    """Start the VolumeManager facade polling the COM host. Called
    explicitly from main.start_all after boot — constructing
    _VOL_MGR starts nothing at import time."""
    _VOL_MGR.start()


def get_hud_volume() -> float:
    return _VOL_MGR.get_level()


def set_hud_volume(pct: float):
    _VOL_MGR.set_level(pct)


def toggle_volume_target() -> str:
    return _VOL_MGR.toggle_target()


def set_hud_mute(muted: bool):
    _VOL_MGR.set_mute(muted)


def volume_manager_started() -> bool:
    return _VOL_MGR.is_started()


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
_COMMANDS_PREV_BTN = (560, 656, 110, 28)
_COMMANDS_NEXT_BTN = (810, 656, 110, 28)
_COMMANDS_CLOSE_BTN = (1110, 656, 105, 28)
_COMMANDS_X_BTN = (1205, 60, 28, 24)

# Directive Input Bar & Action Buttons

# Context Tabs bounds (x, y, w, h)

# Spotify interactive transport buttons in DASHBOARD split view:

# Spotify interactive buttons in expanded SPOTIFY view:


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
    except Exception as _e_silent:
        _config_silent.log_silent("get_clipboard_text", _e_silent)
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
    except Exception as _e_silent:
        _config_silent.log_silent("get_clipboard_text", _e_silent)
    return ""


# State tracking
CURRENT_STATE = "idle"
HUD_MODE = "visor"  # legacy compat var — Phase 4's frame is the only screen
USE_PIXEL_AVATAR = True
SHOW_COMMANDS = False
TYPING_ACTIVE: bool = False
TYPING_BUFFER: str = ""
COMMANDS_PAGE = 0
COMMANDS_PAGES: List[Any] = []
# Clickable command buttons: list of (x, y, w, h, phrase) refreshed each draw.
COMMANDS_CLICK_BOXES: List[Tuple[int, int, int, int, str]] = []
SUBTITLE_TEXT = ""
LOG_STREAM: List[str] = []
DISPLAY_CHAT_LOG: List[Tuple[str, str, str]] = []


def add_display_chat(ts: str, sender: str, msg: str):
    DISPLAY_CHAT_LOG.append((str(ts), str(sender), str(msg)))
    if len(DISPLAY_CHAT_LOG) > 60:
        DISPLAY_CHAT_LOG.pop(0)

# Context Tiles State

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






def _dispatch_media_action(action: str):
    """Dashboard transport for WHATEVER is playing (Alek,
    2026-10-27): ARIA's own video panel when it has media, else
    the active SMTC session, else the global media keys (with
    the Spotify path's state-aware play/pause). Async, like the
    Spotify dispatch."""
    def _run():
        try:
            from aria import media_control
            snap = media_control.panel_snapshot()
            if snap and snap.get("loaded"):
                res = media_control.control(
                    "prev" if action == "previous" else action,
                    target="auto")
                add_hud_log(res)
                return
            from aria import smtc
            if action in ("play", "pause"):
                # Closed-loop, like the Spotify path: aim, verify,
                # escalate to the toggle if the session shrugs.
                want = (action == "play")
                pre = smtc.refresh_now()["active"]
                if pre is not None:
                    if bool(pre["playing"]) == want:
                        add_hud_log(f"Media: already {'playing' if want else 'paused'} ({pre.get('app', 'session')}).")
                        return
                    final = smtc.set_playing(want)
                    add_hud_log(f"Media: {action} ({pre.get('app', 'session')})."
                                if final == want else
                                f"Media: {action} did not take on {pre.get('app', 'the session')}.")
                    return
            if smtc.control_active(action):
                add_hud_log(f"Media: {action}.")
                return
            if action in ("play", "pause", "play_pause"):
                from aria import spotify
                add_hud_log(spotify.tool_spotify(action))
            else:
                from aria.tools.builtins import tool_media_key
                add_hud_log(tool_media_key(action))
        except Exception as e:
            add_hud_log(f"Media action error: {e}")
    threading.Thread(target=_run, daemon=True).start()


def _dispatch_spotify_action(action: str):
    """Trigger Spotify action asynchronously in background thread."""
    def _run():
        try:
            from aria import spotify
            if action in ("play_pause", "play", "pause", "previous", "next"):
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
    # COMMAND_GUIDE (aria/tools/schemas.py) is a LIST of
    # (category, action, phrase) triples — bridge.py consumes it the
    # same way. This loop used to iterate it as a dict
    # ({category: [(action, phrase)]}), so opening the overlay raised
    # "'list' object has no attribute 'items'" and the render guard in
    # main.py painted HUD RENDER FAULT (user screenshot, 2026-10-21;
    # the shape mismatch predates the merger — present at f80957e).
    for cat, act, phr in COMMAND_GUIDE:
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
    global COMMANDS_PAGES, COMMANDS_PAGE, COMMANDS_CLICK_BOXES
    if not COMMANDS_PAGES:
        COMMANDS_PAGES = _build_commands_pages()

    total_pages = max(1, len(COMMANDS_PAGES))
    cur_page_idx = COMMANDS_PAGE % total_pages
    page = COMMANDS_PAGES[cur_page_idx]
    COMMANDS_CLICK_BOXES = []

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
            # Clickable button background
            btn_x, btn_y, btn_w, btn_h = x - 6, y - 16, 372, 20
            cv2.rectangle(canvas, (btn_x, btn_y), (btn_x + btn_w, btn_y + btn_h), (18, 24, 34), -1)
            cv2.rectangle(canvas, (btn_x, btn_y), (btn_x + btn_w, btn_y + btn_h), BORDER, 1)
            COMMANDS_CLICK_BOXES.append((btn_x, btn_y, btn_w, btn_h, b))
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































def _draw_hud_avatar(canvas, acc, acc2, mood) -> None:
    """Center stage: visor/pixel avatar mode."""
    lx, rx, cy = 520, 760, 235
    if USE_PIXEL_AVATAR:
        draw_pixel_aria(canvas, CURRENT_STATE, time.time(), mood=mood)
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




# ------------------------------------------------------------- frame (Phase 4)
# The window is now aria.frame's three-column layout. These delegates keep
# hud's public input/draw API stable for main.py; aria.frame is imported
# lazily (it imports hud back — same pattern the OPS screen used).
MIC_LEVEL: float = 0.0
MIC_LEVEL_AT: float = 0.0


def set_mic_level(v: float) -> None:
    """Publish the live mic level (0..1) for the frame waveform. Called by
    main's wake listener with the RMS it already computes per phrase."""
    global MIC_LEVEL, MIC_LEVEL_AT
    MIC_LEVEL = max(0.0, min(1.0, float(v)))
    MIC_LEVEL_AT = time.time()


def handle_click(x: int, y: int) -> bool:
    import aria.frame as _frame
    return _frame.handle_click(x, y)


def handle_drag(x: int, y: int) -> bool:
    import aria.frame as _frame
    return _frame.handle_drag(x, y)


def handle_wheel(x: int, y: int, up: bool) -> bool:
    import aria.frame as _frame
    return _frame.handle_wheel(x, y, up)


def handle_release(x: int, y: int):
    import aria.frame as _frame
    _frame.handle_release(x, y)


def draw_hud() -> np.ndarray:
    """Render one window frame: the Phase 4 three-column frame, with the
    commands overlay on top when open."""
    import aria.frame as _frame
    canvas = _frame.new_canvas()
    _frame.draw_frame(canvas)
    if SHOW_COMMANDS:
        _draw_commands_overlay(canvas)
    return canvas
