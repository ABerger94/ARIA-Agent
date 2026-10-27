"""ARIA frame — the Mark-grammar mission-control window (Phase 4).

Replaces the OPS screen (aria/ops.py + aria/ops_screen.py, deleted in this
phase) with a single three-column frame in the layout grammar of
Mark-XXXIX-OR's ui.py, re-created in ARIA's own cv2-canvas renderer
(blueprint SS2.7 + the 2026-10-21 amendment: OPS is REMOVED, not reframed;
no OPS_LAYOUT flag, no tab system, no Qt).

Layout (1280x800):
  header   wordmark, live badges (voice pathway / brain / mic / tasks),
           whisper pill, clock + date
  left     SYS MONITOR strip (CPU/MEM/NET/VOL bars, battery, uptime),
           PROVIDERS chain readout, SUBSYSTEMS diagnostics, WORKERS counts,
           SPOTIFY widget (now playing + transport, via aria's existing
           telemetry/dispatch in hud.py — surfacing, not re-implementation)
  center   her pixel avatar + state word + mood/subtitle, and the live
           waveform/oscilloscope strip (state-driven; mic level published
           by main's listener via hud.set_mic_level when listening)
  right    CONVERSATION (chat log) over ACTIVITY (the config event ring —
           the data layer the OPS LOG tab used to present) over the FILE
           DROP ZONE (click-to-browse receipt; drops are analysed by
           the Phase 5 fileproc organ, off-thread, via receive_file)
  bottom   command bar: CMDS (commands overlay), MIC (push-to-talk),
           VOICE (the rehoused VOICE_ENABLED master gate), typed-command
           input routed through the same handle_action path as voice,
           PASTE / SEND / CLR

Data layer note: everything the frame shows comes from the sources that
already existed — config.recent_events (event ring), hud telemetry /
LOG_STREAM / DISPLAY_CHAT_LOG, providers chain state, scheduler,
workers, task queue. This module is presentation + input only.
"""

from __future__ import annotations

import os
import textwrap
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
import psutil
from PIL import Image, ImageDraw, ImageFont

from aria import config as _config
from aria.config import add_log
from aria.pixel_avatar import theme_colors
import aria.hud as _hud

# ---------------------------------------------------------------- palette (BGR)
# Mark's grammar: deep navy ground, thin cyan-blue borders, mono accents.
BG = (20, 14, 10)          # #0a0e14
PANEL = (31, 22, 17)       # #11161f
PANEL2 = (41, 30, 24)      # #181e29
BORDER = (61, 47, 38)      # #262f3d
BORDER_SOFT = (45, 36, 29)
ACCENT = (247, 129, 47)    # #2f81f7 electric blue
TEAL = (238, 211, 34)      # #22d3ee
TEXT = (243, 237, 230)     # #e6edf3
DIM = (158, 148, 139)      # #8b949e
FAINT = (115, 103, 92)     # #5c6773
ERR = (73, 81, 248)        # #f85149
WARN = (34, 153, 210)      # #d29922
INFO = (255, 166, 88)      # #58a6ff
OK = (80, 185, 63)         # #3fb950
PINK = (180, 80, 255)

W, H = 1280, 800

# ---------------------------------------------------------------- geometry
HEADER_H = 56
LEFT_X, LEFT_W = 12, 314
CENTER_X, CENTER_W = 340, 600
RIGHT_X, RIGHT_W = 952, 316
COL_TOP = 66
COL_BOT = 736

SYS_CARD = (LEFT_X, COL_TOP, LEFT_X + LEFT_W, 272)
PROV_CARD = (LEFT_X, 280, LEFT_X + LEFT_W, 408)
SUBS_CARD = (LEFT_X, 416, LEFT_X + LEFT_W, 596)
SPOT_CARD = (LEFT_X, 604, LEFT_X + LEFT_W, COL_BOT)

CHAT_CARD = (RIGHT_X, COL_TOP, RIGHT_X + RIGHT_W, 352)
ACT_CARD = (RIGHT_X, 360, RIGHT_X + RIGHT_W, 592)
DROP_ZONE = (RIGHT_X, 600, RIGHT_X + RIGHT_W, COL_BOT)

WAVE_CARD = (CENTER_X + 16, 618, CENTER_X + CENTER_W - 16, COL_BOT)

BAR_Y, BAR_H = 744, 44
CMDS_BTN = (340, BAR_Y, 64, BAR_H)
MIC_BTN = (412, BAR_Y, 84, BAR_H)
VOICE_BTN = (504, BAR_Y, 116, BAR_H)
INPUT_BOX = (628, BAR_Y, 402, BAR_H)
PASTE_BTN = (1038, BAR_Y, 66, BAR_H)
SEND_BTN = (1112, BAR_Y, 72, BAR_H)
CLR_BTN = (1192, BAR_Y, 76, BAR_H)

_VOL_SLIDER = (LEFT_X + 118, 232, 172, 14)   # inside SYS card; refreshed per draw
_SPOT_BTNS: List[Tuple[int, int, int, int, str]] = []  # rebuilt per draw

# ---------------------------------------------------------------- state
STAGED_FILE: Optional[str] = None
ACTIVITY_SCROLL = 0
_MIC_HELD = False
_BOOT_AT = time.time()

_SUBMIT_CB: Optional[Callable[[str], None]] = None
_WHISPER_CB: Optional[Callable[[], None]] = None
_PTT_PRESS_CB: Optional[Callable[[], None]] = None
_PTT_RELEASE_CB: Optional[Callable[[], None]] = None


def set_submit_callback(fn) -> None:
    global _SUBMIT_CB
    _SUBMIT_CB = fn


def set_whisper_callback(fn) -> None:
    global _WHISPER_CB
    _WHISPER_CB = fn


def set_ptt_callbacks(press, release) -> None:
    global _PTT_PRESS_CB, _PTT_RELEASE_CB
    _PTT_PRESS_CB, _PTT_RELEASE_CB = press, release


def staged_file() -> Optional[str]:
    return STAGED_FILE


def scroll_activity(delta: int) -> None:
    global ACTIVITY_SCROLL
    ACTIVITY_SCROLL = max(0, ACTIVITY_SCROLL + delta)


# ---------------------------------------------------------------- fonts (PIL)
_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_FONT_FILES = {
    False: os.path.join(_ASSETS, "RobotoMono-Regular.ttf"),
    True: os.path.join(_ASSETS, "RobotoMono-Bold.ttf"),
}
_FALLBACKS = [
    "C:\\Windows\\Fonts\\consola.ttf",
    "C:\\Windows\\Fonts\\CascadiaMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]
_FONTS: Dict[Tuple[int, bool], Any] = {}
_TXT_CACHE: Dict[Tuple[str, int, Tuple[int, int, int], bool], Any] = {}
_TXT_CACHE_MAX = 3000


def _font(px: int, bold: bool = False):
    key = (px, bold)
    f = _FONTS.get(key)
    if f is None:
        f = None
        for p in [_FONT_FILES[bold]] + _FALLBACKS:
            try:
                if os.path.exists(p):
                    f = ImageFont.truetype(p, px)
                    break
            except Exception:
                continue
        if f is None:
            f = ImageFont.load_default()
        _FONTS[key] = f
    return f


def _sanitize(s: str) -> str:
    return _hud.hud_ascii(str(s))


def _render_text(s: str, px: int, color, bold: bool):
    key = (s, px, color, bold)
    arr = _TXT_CACHE.get(key)
    if arr is not None:
        return arr
    font = _font(px, bold)
    s = _sanitize(s)
    tmp = Image.new("RGBA", (8, 8))
    d = ImageDraw.Draw(tmp)
    bbox = d.textbbox((0, 0), s, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if w <= 0 or h <= 0:
        return None
    pad = 3
    img = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((pad - bbox[0], pad - bbox[1]), s, font=font,
           fill=(color[2], color[1], color[0], 255))
    arr = np.asarray(img)
    if len(_TXT_CACHE) >= _TXT_CACHE_MAX:
        _TXT_CACHE.clear()
    _TXT_CACHE[key] = arr
    return arr


def _paste(canvas, rgba_arr, x0: int, y0: int) -> None:
    if rgba_arr is None:
        return
    fh, fw = rgba_arr.shape[:2]
    Hc, Wc = canvas.shape[:2]
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(Wc, x0 + fw), min(Hc, y0 + fh)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    fg = rgba_arr[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0].astype(np.float32)
    bg = canvas[cy0:cy1, cx0:cx1].astype(np.float32)
    a = fg[..., 3:4] / 255.0
    rgb = fg[..., :3][..., ::-1]
    canvas[cy0:cy1, cx0:cx1] = (rgb * a + bg * (1.0 - a)).astype(np.uint8)


def _txt(canvas, s, x, y, px: int = 13, color=TEXT, bold: bool = False) -> None:
    arr = _render_text(str(s), px, color, bold)
    if arr is not None:
        _paste(canvas, arr, int(x) - 3, int(y) - 3)


def _tw(s: str, px: int = 13, bold: bool = False) -> int:
    arr = _render_text(str(s), px, (255, 255, 255), bold)
    return int(arr.shape[1]) if arr is not None else 0


def _txt_right(canvas, s, x_right, y, px: int = 13, color=TEXT, bold: bool = False) -> None:
    _txt(canvas, s, x_right - _tw(s, px, bold), y, px, color, bold)


def _txt_center(canvas, s, cx, y, px: int = 13, color=TEXT, bold: bool = False) -> None:
    _txt(canvas, s, cx - _tw(s, px, bold) // 2, y, px, color, bold)


# ---------------------------------------------------------------- shapes
def _rect(canvas, x0, y0, x1, y1, fill=None, outline=None, width: int = 1) -> None:
    if fill is not None:
        cv2.rectangle(canvas, (int(x0), int(y0)), (int(x1), int(y1)), fill, -1)
    if outline is not None:
        cv2.rectangle(canvas, (int(x0), int(y0)), (int(x1), int(y1)), outline, width)


def _card(canvas, box, title: Optional[str] = None) -> int:
    x0, y0, x1, y1 = box
    _rect(canvas, x0, y0, x1, y1, fill=PANEL, outline=BORDER)
    if title:
        _txt(canvas, title, x0 + 12, y0 + 9, 11, FAINT, True)
        cv2.line(canvas, (x0 + 12, y0 + 30), (x1 - 12, y0 + 30), BORDER_SOFT, 1)
        return y0 + 40
    return y0 + 10


def _bar(canvas, x, y, w, pct, color, h: int = 10) -> None:
    pct = max(0.0, min(1.0, float(pct)))
    _rect(canvas, x, y, x + w, y + h, fill=PANEL2, outline=BORDER_SOFT)
    fw = int((w - 2) * pct)
    if fw > 0:
        _rect(canvas, x + 1, y + 1, x + 1 + fw, y + h - 1, fill=color)


def _dashed_rect(canvas, x0, y0, x1, y1, color, dash: int = 10, gap: int = 7) -> None:
    for x in range(int(x0), int(x1), dash + gap):
        cv2.line(canvas, (x, int(y0)), (min(x + dash, int(x1)), int(y0)), color, 1)
        cv2.line(canvas, (x, int(y1)), (min(x + dash, int(x1)), int(y1)), color, 1)
    for y in range(int(y0), int(y1), dash + gap):
        cv2.line(canvas, (int(x0), y), (int(x0), min(y + dash, int(y1))), color, 1)
        cv2.line(canvas, (int(x1), y), (int(x1), min(y + dash, int(y1))), color, 1)


def _pill(canvas, x, y, label, color, filled: bool = True) -> int:
    w = _tw(label, 11, True) + 20
    if filled:
        _rect(canvas, x, y, x + w, y + 22, fill=color)
        _txt(canvas, label, x + 10, y + 4, 11, BG, True)
    else:
        _rect(canvas, x, y, x + w, y + 22, outline=color)
        _txt(canvas, label, x + 10, y + 4, 11, color, True)
    return w


# ---------------------------------------------------------------- data mapping
_LAST_NET = {"t": 0.0, "up": 0, "down": 0}


def _fmt_uptime(s: float) -> str:
    s = int(max(0, s))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    return f"{d}d{h}h" if d else (f"{h}h{m:02d}m" if h else f"{m}m")


def _provider_rows() -> Tuple[str, List[Dict[str, str]]]:
    """Chain readout: (active_name, [{name, state}]) — same sources the
    OPS providers panel used (providers._build_chain + quarantine maps)."""
    try:
        from aria.agent import providers as pv
        from aria import config as cfg
        now = time.time()
        rows = []
        for p in pv._build_chain():
            state = "live" if p.is_available() else "standby"
            q_until = cfg._KEY_QUARANTINE_UNTIL.get(getattr(p, "api_key", ""), 0)
            if q_until > now:
                state = "quarantined"
            rows.append({"name": p.name, "state": state,
                         "model": getattr(p, "model", "")})
        return pv.get_active_provider() or "", rows
    except Exception:
        return "", []


def strip_data() -> Dict[str, Any]:
    """Everything the left strip + header badges render, gathered from the
    existing sources. Never raises — a dead source degrades its field."""
    d: Dict[str, Any] = {
        "cpu": 0.0, "mem": 0.0, "battery": "PWR",
        "net_up_kbs": 0.0, "net_down_kbs": 0.0,
        "uptime_s": 0.0, "volume": 0.0, "volume_target": "device",
        "active_provider": "", "providers": [],
        "voice_ready": False, "voice_enabled": True,
        "subsystems": [], "jobs_running": 0, "jobs_total": 0,
        "scheduled": 0, "task_pending": 0, "task_active": 0,
    }
    try:
        cpu, mem, batt, subs = _hud.get_cached_telemetry()
        d["cpu"], d["mem"], d["battery"] = float(cpu), float(mem), batt
        d["subsystems"] = list(subs or [])
    except Exception:
        pass
    try:
        net = psutil.net_io_counters()
        now = time.time()
        if _LAST_NET["t"]:
            dt = max(0.5, now - _LAST_NET["t"])
            d["net_up_kbs"] = (net.bytes_sent - _LAST_NET["up"]) / dt / 1024.0
            d["net_down_kbs"] = (net.bytes_recv - _LAST_NET["down"]) / dt / 1024.0
        _LAST_NET.update(t=now, up=net.bytes_sent, down=net.bytes_recv)
        d["uptime_s"] = now - psutil.boot_time()
    except Exception:
        pass
    try:
        d["volume"] = float(_hud.get_hud_volume())
        d["volume_target"] = _hud._VOL_MGR.target
    except Exception:
        pass
    try:
        from aria import speech as _speech
        d["voice_ready"] = bool(_speech.voice_ready())
        d["voice_enabled"] = bool(getattr(_speech, "VOICE_ENABLED", True))
    except Exception:
        pass
    d["active_provider"], d["providers"] = _provider_rows()
    try:
        from aria.agent import workers as _workers
        jobs = _workers.list_background_jobs(limit=25)
        d["jobs_total"] = len(jobs)
        d["jobs_running"] = sum(1 for j in jobs
                                 if str(j.get("status", "")).lower() == "running")
    except Exception:
        pass
    try:
        from aria import scheduler as _sched
        d["scheduled"] = len(_sched.sched_list())
    except Exception:
        pass
    try:
        from aria.agent import taskqueue as _tq
        q = _tq.get_queue()
        d["task_pending"] = int(q.pending_count())
        d["task_active"] = sum(1 for s in q.get_all_statuses()
                                if s.get("status") == "running")
    except Exception:
        pass
    return d


def voice_pathway(data: Dict[str, Any]) -> Tuple[str, Any]:
    """Header badge: (label, color) for the voice pathway."""
    if not data.get("voice_enabled", True):
        return "VOICE: MUTED", WARN
    if data.get("voice_ready"):
        return "VOICE: EDGE", OK
    return "VOICE: SAPI", WARN


def mic_state(data: Dict[str, Any]) -> Tuple[str, Any]:
    for name, stat, ok in data.get("subsystems", []):
        if name == "Voice Pathway":
            return (f"MIC: {stat}", OK if ok else ERR)
    return "MIC: ?", FAINT


def activity_events(n: int = 200):
    """The OPS LOG tab's source: the bounded event ring in aria.config."""
    try:
        return _config.recent_events(n)
    except Exception:
        return []


def chat_entries(n: int = 40):
    """Conversation source: hud's display log, falling back to memory's."""
    try:
        raw = list(_hud.DISPLAY_CHAT_LOG)
        if not raw:
            from aria import memory as _memory
            raw = list(_memory.get_display_chat_log())
        return raw[-n:]
    except Exception:
        return []


def spotify_data() -> Dict[str, Any]:
    """Now-playing from her existing window telemetry (hud). Honest
    standby when Spotify is closed — never a fabricated track."""
    try:
        return dict(_hud._get_spotify_telemetry())
    except Exception:
        return {"running": False, "playing": False, "title": "",
                "artist": "", "track": ""}


def media_data() -> Dict[str, Any]:
    """Whatever media is ACTUALLY playing, for the dashboard's
    NOW PLAYING panel (Alek, 2026-10-27 — not just Spotify):
    1. ARIA's own video panel, when it has media loaded (its
       snapshot carries the exact position/duration);
    2. the active Windows media session via SMTC — a movie in
       Movies & TV / VLC, a browser video, Spotify, anything;
    3. the Spotify window-title telemetry as last resort.
    Same dict shape as spotify_data(), plus 'app' / 'source'."""
    try:
        from aria import media_control as _mc
        snap = _mc.panel_snapshot()
        if snap and snap.get("loaded"):
            title = snap.get("title") or "Video"
            return {"running": True, "playing": bool(snap.get("playing")),
                    "status": "playing" if snap.get("playing") else "paused",
                    "source": "hud", "app": "ARIA player",
                    "title": title, "track": title, "artist": "",
                    "position_ms": snap.get("position_ms", 0),
                    "duration_ms": snap.get("duration_ms", 0)}
    except Exception:
        pass
    try:
        from aria import smtc as _smtc
        live = _smtc.now_playing_any()
        if live:
            return dict(live)
    except Exception:
        pass
    try:
        win = _hud.media_window_telemetry()
        if win:
            return dict(win)
    except Exception:
        pass
    data = spotify_data()
    data.setdefault("app", "Spotify")
    return data


# ---------------------------------------------------------------- actions
def toggle_voice() -> bool:
    """The rehoused OPS Controls gate: speech.VOICE_ENABLED, same flag,
    same semantics (output + wake-word input), new home."""
    from aria import speech as _speech
    _speech.VOICE_ENABLED = not getattr(_speech, "VOICE_ENABLED", True)
    add_log(f"Voice {'enabled' if _speech.VOICE_ENABLED else 'muted'}.")
    return bool(_speech.VOICE_ENABLED)


def _basename(path: str) -> str:
    """Basename that also understands Windows separators (her drops come
    from a Windows shell; os.path.basename on POSIX keeps backslashes)."""
    return str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def receive_file(path: str) -> str:
    """Drop-zone receipt: stage the file, log it by name, and hand it
    to the Phase 5 fileproc organ on a daemon thread (processing must
    never block the render loop; the organ reports back through the
    event ring + a chat-log line when it finishes)."""
    global STAGED_FILE
    STAGED_FILE = str(path)
    name = _basename(str(path)) or str(path)
    add_log(f"File received: {name}")
    try:
        _hud.add_hud_log(f"File received: {name}")
    except Exception:
        pass
    _start_file_processing(str(path))
    return name


def _start_file_processing(path: str) -> None:
    def _run():
        try:
            from aria.tools import fileproc
            fileproc.process_and_report(path)
        except Exception as e:
            add_log(f"File processing failed to start: {e}")
    threading.Thread(target=_run, daemon=True).start()


def _browse_for_file() -> None:
    """Click-to-browse on the drop zone (daemon thread; modal dialog must
    not block the render loop)."""
    def _run():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                title="Send a file to ARIA",
                filetypes=[("All files", "*.*"),
                           ("Images", "*.png *.jpg *.jpeg *.gif *.bmp"),
                           ("Documents", "*.pdf *.txt *.md *.docx"),
                           ("Media", "*.mp3 *.wav *.mp4 *.mkv"),
                           ("Code/Data", "*.py *.js *.json *.csv")])
            root.destroy()
            if path:
                receive_file(path)
        except Exception as e:
            add_log(f"File browse failed: {e}")
    threading.Thread(target=_run, daemon=True).start()


def submit_typed() -> bool:
    """SEND / Enter: route the typed buffer through the same brain path
    voice commands take (main wires the callback to handle_action)."""
    text = _hud.TYPING_BUFFER.strip()
    if not text:
        add_log("Directive is empty — type or paste text first.")
        return False
    _hud.TYPING_ACTIVE = False
    _hud.TYPING_BUFFER = ""
    add_log(f"Typed directive: '{text[:35]}...'")
    if _SUBMIT_CB is not None:
        _SUBMIT_CB(text)
    return True


# ---------------------------------------------------------------- drawing
def new_canvas() -> np.ndarray:
    canvas = np.zeros((H, W, 3), dtype=np.uint8)
    canvas[:] = BG
    return canvas


def _draw_header(canvas, data) -> None:
    _rect(canvas, 0, 0, W, HEADER_H, fill=PANEL)
    cv2.line(canvas, (0, HEADER_H), (W, HEADER_H), BORDER, 1)
    _txt(canvas, "A.R.I.A.", 16, 12, 24, ACCENT, True)
    _txt(canvas, "ADAPTIVE ROBOTIC INTELLIGENCE AGENT", 16, 38, 10, FAINT, False)

    x = 300
    label, col = voice_pathway(data)
    x += _pill(canvas, x, 16, label, col, filled=data.get("voice_ready", False)) + 8
    brain = (data.get("active_provider") or "?").upper()
    x += _pill(canvas, x, 16, f"BRAIN: {brain}", INFO, filled=False) + 8
    label, col = mic_state(data)
    x += _pill(canvas, x, 16, label, col, filled=False) + 8
    tasks = data.get("task_pending", 0) + data.get("task_active", 0)
    if tasks:
        x += _pill(canvas, x, 16, f"TASKS: {tasks}", WARN, filled=False) + 8

    # Whisper pill (clickable; callback wired by main)
    whisper_on = bool(getattr(_hud, "WHISPER_MODE", False))
    wx = 952
    _pill(canvas, wx, 16, "WHISPER ON" if whisper_on else "WHISPER OFF",
          (200, 120, 255) if whisper_on else FAINT, filled=whisper_on)

    now = datetime.now()
    clock = now.strftime("%H:%M:%S")
    date = now.strftime("%a %d %b %Y").upper()
    _txt_right(canvas, clock, W - 16, 10, 20, TEXT, True)
    _txt_right(canvas, date, W - 16, 36, 11, DIM, False)


def _draw_sys_card(canvas, data) -> None:
    top = _card(canvas, SYS_CARD, "SYS MONITOR")
    x0, _, x1, _ = SYS_CARD
    lx, bw = x0 + 12, x1 - x0 - 130
    rows = [
        ("CPU", data["cpu"] / 100.0, f"{data['cpu']:.0f}%", ACCENT),
        ("MEM", data["mem"] / 100.0, f"{data['mem']:.0f}%", TEAL),
        ("NET DN", min(1.0, data["net_down_kbs"] / 10240.0),
         f"{data['net_down_kbs']:.0f}K/s", INFO),
        ("NET UP", min(1.0, data["net_up_kbs"] / 10240.0),
         f"{data['net_up_kbs']:.0f}K/s", INFO),
    ]
    y = top + 2
    for label, frac, val, col in rows:
        _txt(canvas, label, lx, y + 1, 11, DIM, True)
        _bar(canvas, lx + 62, y, bw - 62, frac, col, 10)
        _txt(canvas, val, lx + bw + 8, y + 1, 11, TEXT, False)
        y += 22
    # Volume row (slider is draggable — geometry shared with handle_drag)
    global _VOL_SLIDER
    _VOL_SLIDER = (lx + 62, y + 1, bw - 62, 12)
    _txt(canvas, "VOL", lx, y + 1, 11, DIM, True)
    _bar(canvas, _VOL_SLIDER[0], _VOL_SLIDER[1], _VOL_SLIDER[2],
         data["volume"], TEAL, 12)
    _txt(canvas, f"{int(data['volume'] * 100)}%", lx + bw + 8, y + 1, 11, TEXT, False)
    y += 26
    foot = (f"UP {_fmt_uptime(data['uptime_s'])}   BATT {data['battery']}   "
            f"JOBS {data['jobs_running']}/{data['jobs_total']}   "
            f"SCHED {data['scheduled']}   QUEUE {data['task_pending']}")
    _txt(canvas, foot, lx, y + 2, 10, FAINT, False)


def _draw_providers_card(canvas, data) -> None:
    top = _card(canvas, PROV_CARD, "PROVIDERS")
    x0 = PROV_CARD[0]
    y = top
    active = data.get("active_provider", "")
    for row in data.get("providers", [])[:5]:
        is_active = row["name"] == active
        dot = OK if row["state"] == "live" else (ERR if row["state"] == "quarantined" else FAINT)
        cv2.circle(canvas, (x0 + 18, y + 7), 4, dot, -1)
        _txt(canvas, row["name"], x0 + 30, y, 12,
             TEXT if is_active else DIM, is_active)
        _txt_right(canvas, row["state"].upper(), PROV_CARD[2] - 12, y, 10,
                   dot, False)
        y += 17
    if not data.get("providers"):
        _txt(canvas, "chain offline", x0 + 12, y, 11, FAINT, False)


def _draw_subsystems_card(canvas, data) -> None:
    top = _card(canvas, SUBS_CARD, "SUBSYSTEMS")
    x0, _, x1, _ = SUBS_CARD
    y = top
    for name, stat, ok in data.get("subsystems", [])[:8]:
        dot = OK if ok else ERR
        cv2.circle(canvas, (x0 + 18, y + 7), 4, dot, -1)
        _txt(canvas, name, x0 + 30, y, 11, TEXT, False)
        _txt_right(canvas, str(stat)[:18], x1 - 12, y, 11, dot, False)
        y += 17
    if not data.get("subsystems"):
        _txt(canvas, "diagnostics offline", x0 + 12, y, 11, FAINT, False)


def _draw_spotify_card(canvas) -> None:
    global _SPOT_BTNS
    top = _card(canvas, SPOT_CARD, "SPOTIFY")
    x0, _, x1, y1 = SPOT_CARD
    spot = spotify_data()
    if spot.get("playing"):
        label, col = "PLAYING", OK
    elif spot.get("status") == "paused":
        label, col = "PAUSED", WARN
    elif spot.get("running"):
        label, col = "OPEN", WARN
    else:
        label, col = "OFFLINE", FAINT
    _pill(canvas, x0 + 12, top, label, col, filled=bool(spot.get("playing")))
    title = spot.get("title") or "Spotify Offline"
    _txt(canvas, title[:34], x0 + 12, top + 28, 13, TEXT, True)
    artist = spot.get("artist") or ""
    if artist:
        _txt(canvas, artist[:38], x0 + 12, top + 48, 11, DIM, False)
    # Transport — her existing actions only (hud._dispatch_spotify_action)
    btns = [("prev", "|<"), ("play_pause", "> / ||"), ("next", ">|"),
            ("dj", "DJ"), ("focus", "APP")]
    _SPOT_BTNS = []
    bx = x0 + 12
    by = y1 - 30
    for action, blabel in btns:
        bw = 52 if action != "play_pause" else 64
        _rect(canvas, bx, by, bx + bw, by + 24, fill=PANEL2, outline=BORDER)
        _txt_center(canvas, blabel, bx + bw // 2, by + 6, 11, TEAL, True)
        _SPOT_BTNS.append((bx, by, bw, 24, action))
        bx += bw + 8


def _draw_center(canvas, data) -> None:
    # Her face, unchanged: hud's avatar renderer (pixel avatar + waveform
    # mouth for the classic states) draws at canvas-center coordinates.
    acc, acc2 = theme_colors()
    mood = "calm"
    try:
        if _hud._MOOD_CALLBACK:
            mood = _hud._MOOD_CALLBACK().lower()
    except Exception:
        pass
    _hud._draw_hud_avatar(canvas, acc, acc2, mood)

    state = str(getattr(_hud, "CURRENT_STATE", "idle")).upper()
    _txt_center(canvas, state, 640, 516, 26, ACCENT, True)
    mood_line = f"MOOD: {mood.upper()}"
    _txt_center(canvas, mood_line, 640, 550, 11, DIM, False)
    sub = _sanitize(getattr(_hud, "SUBTITLE_TEXT", "") or "")
    if sub:
        for i, line in enumerate(textwrap.wrap(sub, width=62)[:2]):
            _txt_center(canvas, line, 640, 574 + i * 18, 12, TEXT, False)

    _draw_waveform(canvas)


def _draw_waveform(canvas) -> None:
    """Oscilloscope strip: spectrum bars + trace, driven by CURRENT_STATE
    (the same variable the face uses) with amplitude by state — plus the
    live mic level main publishes while listening. Adapted from the old
    AUDIO tile's math, re-homed per the Phase 4 addendum."""
    x0, y0, x1, y1 = WAVE_CARD
    _rect(canvas, x0, y0, x1, y1, fill=PANEL, outline=BORDER)
    _txt(canvas, "AUDIO // OSCILLOSCOPE", x0 + 12, y0 + 9, 11, FAINT, True)
    state = str(getattr(_hud, "CURRENT_STATE", "idle")).lower()
    badge = {"speaking": "SYNTHESIS", "listening": "PERCEPTION"}.get(state, "VAD ARMED")
    _txt_right(canvas, badge, x1 - 12, y0 + 9, 10, TEAL, True)
    cv2.line(canvas, (x0 + 12, y0 + 30), (x1 - 12, y0 + 30), BORDER_SOFT, 1)

    t = time.time()
    # Mic level decays fast once the listener stops publishing (phrase end).
    mic = float(getattr(_hud, "MIC_LEVEL", 0.0))
    if t - float(getattr(_hud, "MIC_LEVEL_AT", 0.0)) > 0.4:
        mic = 0.0
    if state == "speaking":
        amp, speed = 1.0, 14.0
    elif state == "listening":
        amp, speed = 0.55 + 0.65 * mic, 6.0
    elif state == "thinking":
        amp, speed = 0.5, 9.0
    else:
        amp, speed = 0.3, 2.2

    # Spectrum bars
    bars_y = y0 + 74
    n = 34
    bw_area = (x1 - x0 - 24)
    for i in range(n):
        bx = x0 + 12 + int(i * bw_area / n)
        bh = int(abs(np.sin(t * speed + i * 0.45) *
                     np.cos(t * speed * 0.57 + i * 0.2)) * 34 * amp) + 3
        bh = min(bh, 40)
        cv2.line(canvas, (bx, bars_y), (bx, bars_y - bh),
                 TEAL if i % 2 == 0 else ACCENT, 2)
    # Trace
    mid_y = y0 + 96
    cv2.line(canvas, (x0 + 12, mid_y), (x1 - 12, mid_y), BORDER_SOFT, 1)
    pts = []
    for px in range(x0 + 12, x1 - 12, 4):
        ph = (px - x0 - 12) / float(bw_area)
        wy = mid_y + int(np.sin(ph * 16.0 + t * speed) *
                         np.cos(ph * 6.0 + t * speed * 0.44) * 14 * amp)
        pts.append((px, wy))
    for k in range(len(pts) - 1):
        cv2.line(canvas, pts[k], pts[k + 1], OK, 1, cv2.LINE_AA)


def _chat_accent_bgr():
    """The system colour currently SET, as a BGR tuple — ARIA's
    chat colour follows the accent (Alek, 2026-10-27), read in
    the dashboard's order (ui_prefs, then settings 'ui_accent').
    Falls back to the palette ACCENT when nothing is set."""
    hex_ = None
    try:
        from aria import ui_prefs as _up
        hex_ = _up.get_accent()
    except Exception:
        hex_ = None
    if not hex_:
        try:
            hex_ = _config.get_setting('ui_accent', None)
        except Exception:
            hex_ = None
    try:
        s = str(hex_ or '').strip().lower()
        if len(s) == 7 and s.startswith('#'):
            r, g, b = (int(s[i:i + 2], 16) for i in (1, 3, 5))
            return (b, g, r)
    except Exception:
        pass
    return ACCENT


def _draw_chat_card(canvas) -> None:
    top = _card(canvas, CHAT_CARD, "CONVERSATION")
    x0, _, x1, y1 = CHAT_CARD
    entries = chat_entries(40)
    lines: List[Tuple[str, Any]] = []
    for entry in entries:
        if not entry:
            continue
        if isinstance(entry, (tuple, list)) and len(entry) >= 3:
            role, text = str(entry[1]), str(entry[2])
        elif isinstance(entry, (tuple, list)) and len(entry) == 2:
            role, text = str(entry[0]), str(entry[1])
        else:
            role, text = "MSG", str(entry)
        is_aria = role.upper() in ("A.R.I.A.", "ARIA")
        # ARIA's responses in the system colour (whatever accent
        # is set); the user's text white — whole entry, wrapped
        # lines included (Alek, 2026-10-27).
        col = _chat_accent_bgr() if is_aria else TEXT
        wrapped = textwrap.wrap(_sanitize(text), width=40) or [""]
        lines.append((f"{role}: {wrapped[0]}", col))
        for extra in wrapped[1:3]:
            lines.append((f"  {extra}", col))
    max_lines = 11
    y = top + 2
    for text, col in lines[-max_lines:]:
        _txt(canvas, text[:44], x0 + 12, y, 11, col, False)
        y += 20
        if y > y1 - 16:
            break
    if not lines:
        _txt(canvas, "no conversation yet", x0 + 12, y, 11, FAINT, False)


def _draw_activity_card(canvas) -> None:
    top = _card(canvas, ACT_CARD, "ACTIVITY")
    x0, _, x1, y1 = ACT_CARD
    events = activity_events(200)
    rows: List[Tuple[str, str, Any]] = []
    for ts, level, msg, meta in events:
        col = {"error": ERR, "warn": WARN}.get(level, INFO)
        wrapped = textwrap.wrap(_sanitize(msg), width=38) or [""]
        rows.append((str(ts), wrapped[0], col))
        if len(wrapped) > 1:
            rows.append(("", wrapped[1], DIM))
    max_rows = 9
    total = len(rows)
    scroll = max(0, min(ACTIVITY_SCROLL, max(0, total - max_rows)))
    view = rows[max(0, total - max_rows - scroll): total - scroll or None]
    y = top + 2
    for ts, text, col in view:
        if ts:
            _txt(canvas, ts, x0 + 12, y, 10, FAINT, False)
        _txt(canvas, text[:40], x0 + 66, y, 11, col, False)
        y += 20
        if y > y1 - 16:
            break
    if not rows:
        _txt(canvas, "event ring empty", x0 + 12, y, 11, FAINT, False)


def _draw_drop_zone(canvas) -> None:
    x0, y0, x1, y1 = DROP_ZONE
    _rect(canvas, x0, y0, x1, y1, fill=PANEL)
    _dashed_rect(canvas, x0 + 4, y0 + 4, x1 - 4, y1 - 4, ACCENT)
    _txt_center(canvas, "DROP FILE HERE", (x0 + x1) // 2, y0 + 26, 16, ACCENT, True)
    _txt_center(canvas, "or click to browse", (x0 + x1) // 2, y0 + 54, 11, DIM, False)
    _txt_center(canvas, "Images - Video - Audio - PDF", (x0 + x1) // 2, y0 + 78, 10, FAINT, False)
    _txt_center(canvas, "Docs - Code - Data", (x0 + x1) // 2, y0 + 94, 10, FAINT, False)
    if STAGED_FILE:
        name = _basename(STAGED_FILE) or STAGED_FILE
        _txt_center(canvas, f"staged: {name[:30]}", (x0 + x1) // 2, y0 + 112, 10, OK, False)


def _draw_command_bar(canvas, data) -> None:
    _rect(canvas, 0, BAR_Y - 6, W, H, fill=PANEL)
    cv2.line(canvas, (0, BAR_Y - 6), (W, BAR_Y - 6), BORDER, 1)

    def _btn(box, label, col, filled=False):
        x, y, w, h = box
        _rect(canvas, x, y, x + w, y + h, fill=col if filled else PANEL2,
              outline=col)
        _txt_center(canvas, label, x + w // 2, y + h // 2 - 7, 12,
                    BG if filled else col, True)

    _btn(CMDS_BTN, "CMDS", ACCENT)
    mic_label = "MIC REC" if _MIC_HELD else "MIC"
    _, mic_col = mic_state(data)
    _btn(MIC_BTN, mic_label, ERR if _MIC_HELD else mic_col, filled=_MIC_HELD)
    voice_on = bool(data.get("voice_enabled", True))
    _btn(VOICE_BTN, "VOICE ON" if voice_on else "VOICE OFF",
         OK if voice_on else WARN, filled=voice_on)

    ix, iy, iw, ih = INPUT_BOX
    typing = bool(getattr(_hud, "TYPING_ACTIVE", False))
    buf = _sanitize(getattr(_hud, "TYPING_BUFFER", "") or "")
    _rect(canvas, ix, iy, ix + iw, iy + ih, fill=BG,
          outline=ACCENT if typing else BORDER, width=2 if typing else 1)
    if buf:
        disp = buf[-46:]
        _txt(canvas, "> " + disp, ix + 10, iy + 15, 13, TEXT, False)
    elif typing:
        cursor = "_" if int(time.time() * 2.5) % 2 == 0 else " "
        _txt(canvas, "> " + cursor, ix + 10, iy + 15, 13, TEXT, False)
    else:
        _txt(canvas, "Type a directive, or click to type...",
             ix + 10, iy + 15, 12, FAINT, False)

    _btn(PASTE_BTN, "PASTE", DIM)
    ready = bool(buf.strip())
    _btn(SEND_BTN, "SEND", OK if ready else FAINT, filled=ready)
    _btn(CLR_BTN, "CLR", DIM)


def draw_frame(canvas) -> np.ndarray:
    """Render the full frame onto canvas (1280x800)."""
    data = strip_data()
    _draw_header(canvas, data)
    _draw_sys_card(canvas, data)
    _draw_providers_card(canvas, data)
    _draw_subsystems_card(canvas, data)
    _draw_spotify_card(canvas)
    _draw_center(canvas, data)
    _draw_chat_card(canvas)
    _draw_activity_card(canvas)
    _draw_drop_zone(canvas)
    _draw_command_bar(canvas, data)
    return canvas


# ---------------------------------------------------------------- input
def _in(box, x, y) -> bool:
    bx, by, bw, bh = box
    return bx <= x <= bx + bw and by <= y <= by + bh


def input_rect() -> Tuple[int, int, int, int]:
    return INPUT_BOX


def handle_click(x: int, y: int) -> bool:
    """Frame click routing. Returns True when the click was consumed."""
    global _MIC_HELD
    if _in(CMDS_BTN, x, y):
        _hud.SHOW_COMMANDS = not _hud.SHOW_COMMANDS
        return True
    if _in(MIC_BTN, x, y):
        _MIC_HELD = True
        if _PTT_PRESS_CB is not None:
            _PTT_PRESS_CB()
        return True
    if _in(VOICE_BTN, x, y):
        toggle_voice()
        return True
    if _in(DROP_ZONE, x, y):
        _browse_for_file()
        return True
    for bx, by, bw, bh, action in _SPOT_BTNS:
        if bx <= x <= bx + bw and by <= y <= by + bh:
            _hud._dispatch_spotify_action(action)
            return True
    sx, sy, sw, sh = _VOL_SLIDER
    if sx <= x <= sx + sw and sy - 4 <= y <= sy + sh + 4:
        _hud.set_hud_volume(max(0.0, min(1.0, (x - sx) / float(sw))))
        return True
    # Whisper pill (header, fixed geometry next to the badges)
    if 952 <= x <= 1090 and 14 <= y <= 40:
        if _WHISPER_CB is not None:
            _WHISPER_CB()
        return True
    if _in(SEND_BTN, x, y):
        submit_typed()
        return True
    if _in(PASTE_BTN, x, y):
        pasted = _hud.get_clipboard_text()
        _hud.TYPING_ACTIVE = True
        if pasted:
            _hud.TYPING_BUFFER += pasted
            add_log(f"Pasted from clipboard ({len(pasted)} chars).")
        else:
            add_log("Clipboard empty or non-text.")
        return True
    if _in(CLR_BTN, x, y):
        _hud.TYPING_BUFFER = ""
        add_log("Directive buffer cleared.")
        return True
    if _in(INPUT_BOX, x, y):
        _hud.TYPING_ACTIVE = True
        add_log("Typing mode engaged.")
        return True
    return False


def handle_release(x: int, y: int) -> None:
    global _MIC_HELD
    if _MIC_HELD:
        _MIC_HELD = False
        if _PTT_RELEASE_CB is not None:
            _PTT_RELEASE_CB()


def handle_drag(x: int, y: int) -> bool:
    sx, sy, sw, sh = _VOL_SLIDER
    if sx - 10 <= x <= sx + sw + 10 and sy - 8 <= y <= sy + sh + 8:
        _hud.set_hud_volume(max(0.0, min(1.0, (x - sx) / float(sw))))
        return True
    return False


def handle_wheel(x: int, y: int, up: bool) -> bool:
    ax0, ay0, ax1, ay1 = ACT_CARD
    if ax0 <= x <= ax1 and ay0 <= y <= ay1:
        scroll_activity(3 if up else -3)
        return True
    sx, sy, sw, sh = _VOL_SLIDER
    if sx <= x <= sx + sw and sy - 6 <= y <= sy + sh + 6:
        delta = 0.05 if up else -0.05
        _hud.set_hud_volume(_hud.get_hud_volume() + delta)
        return True
    return False
