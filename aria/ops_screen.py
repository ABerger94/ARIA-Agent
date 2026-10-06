"""OPS screen — tab-driven mission-control overlay, rendered with OpenCV.

Tabs: [1] Log  [2] Tasks  [3] Sensors  [4] Controls  [5] Notes  [6] HUB  [7] Day

- Tabs 1-3 share the three-column layout (Log 30% | Tasks 40% | Sensors 30%)
  with the selected pane focused (accent border).
- Controls / Notes / HUB / Day replace the three-column layout.
- HUB is the default landing tab (O opens the panel here): the 20 most
  recent events in one scrollable list, click one to jump to the full Log.
- Day reuses the existing command-center dashboard via a draw callback.

Typography: Roboto Mono (bundled in aria/assets/) rendered through PIL —
Hershey fonts are what made the old version look cheap. If the bundled
font is missing we fall back to Consolas / DejaVu Sans Mono.

Keys (handled by main.py): 1-7 switch tabs, J/K or arrows scroll,
Ctrl+Alt+L/T/S focus Log/Tasks/Sensors, O or ESC closes.
Mouse: click tabs, task rows, HUB events, and control buttons.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from aria import config as _config
from aria import ops as _ops
from aria import scheduler as _sched
from aria import speech as _speech
from aria import vision as _vision
from aria.agent import workers as _workers

# ---------------------------------------------------------------- palette (BGR)
ACCENT = (255, 132, 10)      # #0a84ff
BG = (23, 17, 13)            # #0d1117
PANEL = (17, 17, 17)         # #111
PANEL2 = (34, 27, 22)        # #161b22 — raised surfaces (buttons)
BORDER = (34, 34, 34)        # #222
BORDER_SOFT = (40, 33, 28)   # #1c2128 — hairlines
TEXT = (235, 235, 235)
DIM = (178, 170, 160)        # #a0aab2-ish, softened
FAINT = (132, 120, 107)      # #6b7684-ish
ERR = (85, 85, 255)          # #ff5555
WARN = (0, 204, 255)         # #ffcc00
INFO = (208, 192, 136)       # #88c0d0
OK = (120, 200, 120)
GREEN_DOT = (110, 200, 110)

W, H = 1280, 720
TAB_H = 56
CONTENT_Y = 68
CONTENT_BOT = 684
STATUS_Y = 700

TABS = ["log", "tasks", "sensors", "controls", "notes", "hub", "day"]
TAB_LABELS = ["[1] Log", "[2] Tasks", "[3] Sensors", "[4] Controls",
              "[5] Notes", "[6] HUB", "[7] Day"]
COL_TABS = ("log", "tasks", "sensors")

# ---------------------------------------------------------------- state
ACTIVE_TAB = "hub"
SCROLL: Dict[str, int] = {t: 0 for t in TABS}
SELECTED_TASK: Optional[str] = None
SHOW_SCRIPTS = False

STATE_PATH = os.path.join(_config.WORKSPACE_DIR, "ops_state.json")
NOTES_PATH = os.path.join(_config.WORKSPACE_DIR, "notes", "ops.md")
NOTES_BUF = ""

_CLICKS: List[Tuple[int, int, int, int, str, Any]] = []  # rebuilt every draw


def _sanitize(s: str) -> str:
    # Roboto Mono covers these; everything else becomes "?" so PIL/cv2 never chokes.
    return re.sub(r"[^\x20-\x7e·—–]", "?", str(s))


def _trunc(s: str, n: int) -> str:
    s = _sanitize(s)
    return s if len(s) <= n else s[:n - 3] + "..."


# ---------------------------------------------------------------- fonts
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


def _font(px: int, bold: bool = False):
    key = (px, bold)
    f = _FONTS.get(key)
    if f is None:
        paths = [_FONT_FILES[bold]] + _FALLBACKS
        f = None
        for p in paths:
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


def _rgba(color, alpha: int = 255) -> Tuple[int, int, int, int]:
    if color is None:
        return (0, 0, 0, 0)
    return (color[2], color[1], color[0], alpha)


# Text glyph cache: (text, px, color, bold) -> RGBA numpy array
_TXT_CACHE: Dict[Tuple[str, int, Tuple[int, int, int], bool], Any] = {}
_TXT_CACHE_MAX = 3000


def _render_text(s: str, px: int, color: Tuple[int, int, int], bold: bool):
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
    """Alpha-composite an RGBA numpy array onto a BGR canvas."""
    if rgba_arr is None:
        return
    fh, fw = rgba_arr.shape[:2]
    H, Wc = canvas.shape[:2]
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(Wc, x0 + fw), min(H, y0 + fh)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    fg = rgba_arr[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0].astype(np.float32)
    bg = canvas[cy0:cy1, cx0:cx1].astype(np.float32)
    a = fg[..., 3:4] / 255.0
    rgb = fg[..., :3][..., ::-1]  # RGB -> BGR
    canvas[cy0:cy1, cx0:cx1] = (rgb * a + bg * (1.0 - a)).astype(np.uint8)


def _txt(canvas, s, x, y, px: int = 13,
         color: Tuple[int, int, int] = TEXT, bold: bool = False) -> None:
    """Draw text so the glyph bounding box's top-left lands at (x, y)."""
    arr = _render_text(str(s), px, color, bold)
    if arr is None:
        return
    _paste(canvas, arr, int(x) - 3, int(y) - 3)


def _tw(s: str, px: int = 13, bold: bool = False) -> int:
    arr = _render_text(str(s), px, (255, 255, 255), bold)
    return int(arr.shape[1]) if arr is not None else 0


def _rrect(canvas, x0, y0, x1, y1, r: int,
           fill=None, outline=None, width: int = 1) -> None:
    """Rounded rectangle, PIL-composited (this is what kills the clunky)."""
    w, h = int(x1 - x0), int(y1 - y0)
    if w <= 2 or h <= 2:
        return
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r,
                        fill=_rgba(fill), outline=_rgba(outline), width=width)
    _paste(canvas, np.asarray(img), int(x0), int(y0))


def _arc(canvas, cx, cy, r: int, frac: float,
         track=BORDER, color=ACCENT, width: int = 9) -> None:
    """270-degree gauge arc (135° -> 405°), PIL-rendered."""
    d = r * 2 + width * 2 + 4
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    box = [width + 2, width + 2, d - width - 3, d - width - 3]
    dr.arc(box, start=135, end=405, fill=_rgba(track), width=width)
    frac = max(0.0, min(1.0, frac))
    if frac > 0.005:
        dr.arc(box, start=135, end=135 + 270 * frac, fill=_rgba(color), width=width)
    _paste(canvas, np.asarray(img), int(cx - d // 2), int(cy - d // 2))

# ---------------------------------------------------------------- log feed
_LOG_LOCK = threading.Lock()
_LOG_BUF: List[Tuple[str, str, str]] = []  # (hh:mm:ss, level, msg)
_LOG_MAX = 500

_ERR_RE = re.compile(r"error|fail|exception|traceback|fault", re.I)
_WARN_RE = re.compile(r"\bwarn", re.I)


def _level_of(msg: str) -> str:
    if _ERR_RE.search(msg):
        return "error"
    if _WARN_RE.search(msg):
        return "warn"
    return "info"


def _on_log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    with _LOG_LOCK:
        _LOG_BUF.append((ts, _level_of(msg), msg[:400]))
        if len(_LOG_BUF) > _LOG_MAX:
            del _LOG_BUF[:len(_LOG_BUF) - _LOG_MAX]


_config.register_log_listener(_on_log)


def recent_events(n: int = 20) -> List[Tuple[str, str, str]]:
    with _LOG_LOCK:
        return list(_LOG_BUF[-n:])


def _level_color(level: str):
    return {"error": ERR, "warn": WARN}.get(level, INFO)


# ---------------------------------------------------------------- persistence
def load_state() -> None:
    global ACTIVE_TAB, SELECTED_TASK
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
        if st.get("active_tab") in TABS:
            ACTIVE_TAB = st["active_tab"]
        sc = st.get("scroll") or {}
        for t in TABS:
            SCROLL[t] = max(0, int(sc.get(t, 0)))
        SELECTED_TASK = st.get("selected_task")
    except Exception:
        pass


def save_state() -> None:
    save_notes()
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"active_tab": ACTIVE_TAB, "scroll": SCROLL,
                       "selected_task": SELECTED_TASK}, f)
    except Exception:
        pass


def load_notes() -> None:
    global NOTES_BUF
    try:
        os.makedirs(os.path.dirname(NOTES_PATH), exist_ok=True)
        if os.path.exists(NOTES_PATH):
            with open(NOTES_PATH, "r", encoding="utf-8") as f:
                NOTES_BUF = f.read()
    except Exception:
        pass


def save_notes() -> None:
    try:
        os.makedirs(os.path.dirname(NOTES_PATH), exist_ok=True)
        with open(NOTES_PATH, "w", encoding="utf-8") as f:
            f.write(NOTES_BUF)
    except Exception:
        pass


load_state()
load_notes()


# ---------------------------------------------------------------- panel API
def open_panel() -> None:
    """O key: open OPS on the HUB tab."""
    global ACTIVE_TAB
    ACTIVE_TAB = "hub"
    load_notes()


def set_tab(idx: int) -> None:
    global ACTIVE_TAB, SHOW_SCRIPTS
    if 0 <= idx < len(TABS):
        if ACTIVE_TAB != TABS[idx]:
            save_notes()
        ACTIVE_TAB = TABS[idx]
        SHOW_SCRIPTS = False


def scroll_active(delta: int) -> None:
    SCROLL[ACTIVE_TAB] = max(0, SCROLL.get(ACTIVE_TAB, 0) + delta)


def handle_wheel(up: bool) -> None:
    scroll_active(3 if up else -3)


# ---------------------------------------------------------------- notes editing
def notes_type(ch: str) -> None:
    global NOTES_BUF
    NOTES_BUF += ch


def notes_backspace() -> None:
    global NOTES_BUF
    NOTES_BUF = NOTES_BUF[:-1]


def notes_newline() -> None:
    global NOTES_BUF
    NOTES_BUF += "\n"
    save_notes()  # autosave


# ---------------------------------------------------------------- task rows
def _task_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        for tid, kind, prompt, interval_s, next_run in _sched.sched_list():
            rows.append({"id": f"S{tid}", "kind": "sched",
                         "desc": (prompt or "")[:60] or kind,
                         "status": "sched", "eta": str(next_run or "?"),
                         "detail": f"kind={kind} interval={interval_s}s next={next_run}"})
    except Exception:
        pass
    try:
        for j in _workers.list_background_jobs(limit=12):
            st = j.get("status", "?")
            rows.append({"id": f"J{j.get('id')}", "kind": "job",
                         "desc": str(j.get("name") or "")[:60],
                         "status": st, "eta": "-",
                         "detail": f"pid={j.get('pid')} log={j.get('log_file')}",
                         "job_id": j.get("id")})
    except Exception:
        pass
    order = {"running": 0, "sched": 1, "done": 2}
    rows.sort(key=lambda r: order.get(r["status"], 3))
    return rows


_PILL_STYLE = {
    "running": ((58, 42, 10), WARN, "RUNNING"),
    "sched": ((40, 44, 52), DIM, "SCHED"),
    "done": ((28, 58, 34), OK, "DONE"),
    "completed": ((28, 58, 34), OK, "DONE"),
    "failed": ((66, 28, 30), ERR, "FAILED"),
    "error": ((66, 28, 30), ERR, "FAILED"),
}


def _pill_style(status: str):
    return _PILL_STYLE.get(status, ((40, 44, 52), DIM, "?"))


def _spinner() -> str:
    return ["|", "/", "-", "\\"][int(time.time() * 4) % 4]


# ---------------------------------------------------------------- sensors data
def _sys_stats() -> Optional[Dict[str, float]]:
    """None when psutil is unavailable — callers show the install hint."""
    try:
        import psutil  # noqa: F401 -- probing availability only
    except ImportError:
        return None
    try:
        d = (_ops.fetch_systems().get("data") or {})
        return {"cpu": float(d.get("cpu_pct") or 0),
                "mem": float(d.get("mem_pct") or 0),
                "disk": float(d.get("disk_pct") or 0)}
    except Exception:
        return {"cpu": 0.0, "mem": 0.0, "disk": 0.0}


def _gpu_pct() -> Optional[float]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
        return float(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


# ---------------------------------------------------------------- controls
_CONTROLS = [
    ("sched", "SCHEDULER"),
    ("shot", "SCREENSHOT"),
    ("voice", "VOICE"),
    ("script", "RUN SCRIPT"),
    ("close", "CLOSE OPS"),
    ("quit", "QUIT ARIA"),
]


def _ctl_sub(name: str) -> str:
    if name == "sched":
        return "PAUSED" if _sched.is_paused() else "RUNNING"
    if name == "voice":
        return "ON" if getattr(_speech, "VOICE_ENABLED", True) else "OFF"
    return ""


def _ctl_active(name: str) -> bool:
    if name == "sched":
        return not _sched.is_paused()
    if name == "voice":
        return bool(getattr(_speech, "VOICE_ENABLED", True))
    return False


def _scripts_dir() -> str:
    d = os.path.join(_config.WORKSPACE_DIR, "scripts")
    os.makedirs(d, exist_ok=True)
    return d


def _ctl_action(name: str) -> None:
    global SHOW_SCRIPTS
    if name == "sched":
        paused = _sched.set_paused(not _sched.is_paused())
        _config.add_log(f"Scheduler {'paused' if paused else 'resumed'}.")
    elif name == "shot":
        try:
            res = _vision.tool_screenshot("ops")
            _config.add_log(f"Screenshot: {_trunc(res, 80)}")
        except Exception as e:
            _config.add_log(f"Screenshot failed: {e}")
    elif name == "voice":
        _speech.VOICE_ENABLED = not getattr(_speech, "VOICE_ENABLED", True)
        _config.add_log(f"Voice {'enabled' if _speech.VOICE_ENABLED else 'muted'}.")
    elif name == "script":
        SHOW_SCRIPTS = not SHOW_SCRIPTS
    elif name == "close":
        save_state()
        from aria import hud as _hud
        _hud.HUD_MODE = "visor"
        _config.add_log("OPS closed.")
    elif name == "quit":
        _config.add_log("Shutdown requested from OPS.")
        from aria import main as _main
        _main.request_shutdown()


def _run_script_file(fname: str) -> None:
    global SHOW_SCRIPTS
    SHOW_SCRIPTS = False
    path = os.path.join(_scripts_dir(), fname)
    try:
        res = _workers.start_background_job(f'"{sys.executable}" "{path}"',
                                            name=fname)
        _config.add_log(f"Script '{fname}' started as job #{res.get('id')}.")
    except Exception as e:
        _config.add_log(f"Script '{fname}' failed to start: {e}")


def _provider_name() -> str:
    try:
        d = (_ops.get_dashboard().get("providers") or {}).get("data") or {}
        return str(d.get("active") or d.get("primary") or "?")
    except Exception:
        return "?"

# ---------------------------------------------------------------- chrome
def _draw_tab_bar(canvas) -> None:
    cv2.rectangle(canvas, (0, 0), (W, TAB_H), PANEL, -1)
    n = len(TABS)
    tw = W / n
    for i, (key, label) in enumerate(zip(TABS, TAB_LABELS)):
        x0 = int(i * tw)
        x1 = int((i + 1) * tw) if i < n - 1 else W
        sel = (key == ACTIVE_TAB)
        if sel:
            cv2.rectangle(canvas, (x0, 0), (x1, TAB_H), ACCENT, -1)
            _txt(canvas, label, x0 + (x1 - x0 - _tw(label, 14, True)) // 2,
                 20, 14, (255, 255, 255), True)
            cv2.rectangle(canvas, (x0, 0), (x1, 3), (255, 255, 255), -1)
        else:
            _txt(canvas, label, x0 + (x1 - x0 - _tw(label, 14)) // 2,
                 20, 14, DIM, False)
            if i:
                cv2.line(canvas, (x0, 10), (x0, TAB_H - 10), BORDER, 1)
        _CLICKS.append((x0, 0, x1, TAB_H, "tab", key))
    cv2.rectangle(canvas, (0, 0), (W - 1, H - 1), BORDER, 4)


def _draw_status(canvas) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    prov = _provider_name()
    cv2.circle(canvas, (30, STATUS_Y + 4), 4, GREEN_DOT, -1)
    _txt(canvas, prov, 42, STATUS_Y - 2, 12, INFO, True)
    hint = "[1-7] tabs    [J/K] scroll    [O]/[ESC] close    [E] read mail    [R] refresh"
    _txt(canvas, hint, 300, STATUS_Y - 2, 12, FAINT)
    cw = _tw(now, 12)
    _txt(canvas, now, W - 28 - cw, STATUS_Y - 2, 12, FAINT)


def _panel_title(canvas, text, x, y) -> None:
    _txt(canvas, text, x, y, 11, FAINT, True)


# ---------------------------------------------------------------- panes
def _pane_log(canvas, x0, y0, x1, y1, focused) -> None:
    _rrect(canvas, x0, y0, x1, y1, 10, fill=PANEL, outline=ACCENT if focused else BORDER)
    _panel_title(canvas, "LOG", x0 + 16, y0 + 14)
    with _LOG_LOCK:
        lines = list(_LOG_BUF)
    row_h, top = 21, y0 + 40
    vis = (y1 - top - 8) // row_h
    max_scroll = max(0, len(lines) - vis)
    if SCROLL["log"] > max_scroll:
        SCROLL["log"] = max_scroll
    start = max(0, len(lines) - vis - SCROLL["log"])
    max_chars = min(80, max(20, int((x1 - x0 - 112) / 7.8)))
    for i, (ts, level, msg) in enumerate(lines[start:start + vis]):
        yy = top + i * row_h
        _txt(canvas, ts, x0 + 16, yy, 12, FAINT)
        _txt(canvas, _trunc(msg, max_chars), x0 + 92, yy, 13, _level_color(level))
    if not lines:
        _txt(canvas, "No events yet.", x0 + 16, top, 13, FAINT)


def _pane_tasks(canvas, x0, y0, x1, y1, focused) -> None:
    _rrect(canvas, x0, y0, x1, y1, 10, fill=PANEL, outline=ACCENT if focused else BORDER)
    _panel_title(canvas, "TASKS", x0 + 16, y0 + 14)
    rows = _task_rows()
    # header
    hx = x0 + 16
    _txt(canvas, "ID", hx, y0 + 38, 11, FAINT, True)
    _txt(canvas, "STATUS", hx + 64, y0 + 38, 11, FAINT, True)
    _txt(canvas, "ETA", hx + 160, y0 + 38, 11, FAINT, True)
    _txt(canvas, "DESCRIPTION", hx + 300, y0 + 38, 11, FAINT, True)
    cv2.line(canvas, (x0 + 16, y0 + 58), (x1 - 16, y0 + 58), BORDER_SOFT, 1)
    row_h, top = 26, y0 + 66
    vis = (y1 - top - 64) // row_h
    max_scroll = max(0, len(rows) - vis)
    if SCROLL["tasks"] > max_scroll:
        SCROLL["tasks"] = max_scroll
    start = SCROLL["tasks"]
    for i, r in enumerate(rows[start:start + vis]):
        yy = top + i * row_h
        if r["id"] == SELECTED_TASK:
            _rrect(canvas, x0 + 8, yy - 5, x1 - 8, yy + 19, 6, fill=PANEL2)
        _txt(canvas, r["id"], hx, yy, 13, TEXT, True)
        bg, fg, label = _pill_style(r["status"])
        pill_txt = _spinner() + " " + label if r["status"] == "running" else label
        pw = _tw(pill_txt, 11, True) + 18
        _rrect(canvas, hx + 64, yy - 3, hx + 64 + pw, yy + 17, 10,
               fill=bg)
        _txt(canvas, pill_txt, hx + 64 + 9, yy, 11, fg, True)
        _txt(canvas, _trunc(r["eta"], 16), hx + 160, yy, 12, DIM)
        _txt(canvas, _trunc(r["desc"], 36), hx + 300, yy, 13, TEXT)
        _CLICKS.append((x0 + 8, yy - 5, x1 - 8, yy + 19, "task", r["id"]))
    if not rows:
        _txt(canvas, "No scheduled tasks or jobs.", hx, top, 13, FAINT)
    # expanded detail
    if SELECTED_TASK:
        sel = next((r for r in rows if r["id"] == SELECTED_TASK), None)
        if sel:
            dy = y1 - 56
            cv2.line(canvas, (x0 + 16, dy - 16), (x1 - 16, dy - 16), BORDER_SOFT, 1)
            _txt(canvas, _trunc(f"> {sel['id']}  {sel['detail']}", 76),
                 x0 + 16, dy + 2, 12, INFO)
            if sel["kind"] == "job" and sel.get("job_id") is not None:
                try:
                    tail = _workers.get_background_job_log(sel["job_id"], 2)
                    last = tail.strip().splitlines()[-1] if tail.strip() else "(empty)"
                    _txt(canvas, _trunc("  " + last, 76), x0 + 16, dy + 22, 12, DIM)
                except Exception:
                    pass


def _pane_sensors(canvas, x0, y0, x1, y1, focused) -> None:
    _rrect(canvas, x0, y0, x1, y1, 10, fill=PANEL, outline=ACCENT if focused else BORDER)
    _panel_title(canvas, "SENSORS", x0 + 16, y0 + 14)
    stats = _sys_stats()
    if stats is None:
        _txt(canvas, "psutil not installed", x0 + 16, y0 + 60, 13, WARN, True)
        _txt(canvas, "Run:  pip install psutil", x0 + 16, y0 + 84, 13, DIM)
        _txt(canvas, "then restart ARIA.", x0 + 16, y0 + 106, 13, DIM)
        return
    gauges = [("CPU", stats["cpu"] / 100.0, f"{stats['cpu']:.0f}%"),
              ("RAM", stats["mem"] / 100.0, f"{stats['mem']:.0f}%"),
              ("DISK", stats["disk"] / 100.0, f"{stats['disk']:.0f}%")]
    gpu = _gpu_pct()
    if gpu is not None:  # only show a gauge for hardware that exists
        gauges.append(("GPU", gpu / 100.0, f"{gpu:.0f}%"))
    n = len(gauges)
    gw = (x1 - x0 - 32) // n
    gy = y0 + 108
    for i, (label, frac, val) in enumerate(gauges):
        gx = x0 + 16 + gw * i + gw // 2
        _arc(canvas, gx, gy, 40, frac)
        vw = _tw(val, 22, True)
        _txt(canvas, val, gx - vw // 2, gy - 13, 22, TEXT, True)
        lw = _tw(label, 12, True)
        _txt(canvas, label, gx - lw // 2, gy + 56, 12, FAINT, True)
    # webcam preview
    frame = getattr(_vision, "LATEST_CAMERA_FRAME", None)
    py = gy + 92
    _panel_title(canvas, "CAMERA", x0 + 16, py)
    if frame is not None and py + 170 < y1:
        try:
            thumb = cv2.resize(frame, (200, 150))
            img = Image.fromarray(cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB))
            mask = Image.new("L", (200, 150), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, 199, 149], radius=8, fill=255)
            rgba = np.asarray(img.convert("RGBA"))
            rgba[..., 3] = np.asarray(mask)
            _paste(canvas, rgba, x0 + 16, py + 22)
        except Exception:
            _txt(canvas, "camera unavailable", x0 + 16, py + 24, 12, FAINT)
    else:
        _txt(canvas, "camera off", x0 + 16, py + 24, 12, FAINT)


def _draw_columns(canvas) -> None:
    focus = ACTIVE_TAB if ACTIVE_TAB in COL_TABS else None
    _pane_log(canvas, 20, CONTENT_Y, 392, CONTENT_BOT, focus == "log")
    _pane_tasks(canvas, 400, CONTENT_Y, 896, CONTENT_BOT, focus == "tasks")
    _pane_sensors(canvas, 904, CONTENT_Y, 1260, CONTENT_BOT, focus == "sensors")


# ---------------------------------------------------------------- controls tab
def _draw_controls(canvas) -> None:
    _panel_title(canvas, "CONTROLS", 36, CONTENT_Y + 6)
    n = len(_CONTROLS)
    bs, gap = 96, 28
    total = n * bs + (n - 1) * gap
    x0 = (W - total) // 2
    y0 = CONTENT_Y + 44
    for i, (name, label) in enumerate(_CONTROLS):
        bx = x0 + i * (bs + gap)
        by = y0
        active = _ctl_active(name)
        _rrect(canvas, bx, by, bx + bs, by + bs, 16, fill=PANEL2,
               outline=ACCENT if active else BORDER_SOFT, width=2 if active else 1)
        lw = _tw(label, 12, True)
        _txt(canvas, label, bx + (bs - lw) // 2, by + 38, 12, TEXT, True)
        sub = _ctl_sub(name)
        if sub:
            sw = _tw(sub, 11, True)
            _txt(canvas, sub, bx + (bs - sw) // 2, by + 60, 11,
                 OK if active else WARN, True)
        _CLICKS.append((bx, by, bx + bs, by + bs, "ctl", name))
    if SHOW_SCRIPTS:
        sy = y0 + bs + 32
        _txt(canvas, "Pick a script to run  (output goes to Tasks / Log):",
             x0, sy, 12, DIM, True)
        try:
            files = sorted(f for f in os.listdir(_scripts_dir()) if f.endswith(".py"))
        except Exception:
            files = []
        if not files:
            _txt(canvas, f"No .py files yet — drop scripts in {_scripts_dir()}",
                 x0, sy + 26, 12, FAINT)
        for i, f in enumerate(files[:10]):
            yy = sy + 28 + i * 24
            _txt(canvas, "> " + f, x0, yy, 13, INFO)
            _CLICKS.append((x0, yy - 4, x0 + 420, yy + 18, "script", f))


# ---------------------------------------------------------------- notes tab
def _draw_notes(canvas) -> None:
    _txt(canvas, "NOTES", 36, CONTENT_Y + 6, 11, FAINT, True)
    _txt(canvas, "~/ARIA/notes/ops.md   ·   [T] type   ·   autosaves",
         110, CONTENT_Y + 6, 11, FAINT)
    _rrect(canvas, 20, CONTENT_Y + 30, 1260, CONTENT_BOT, 10,
           fill=PANEL, outline=BORDER)
    lines = NOTES_BUF.splitlines() or ["(empty — press T to type)"]
    row_h, top = 21, CONTENT_Y + 48
    vis = (CONTENT_BOT - top - 8) // row_h
    max_scroll = max(0, len(lines) - vis)
    if SCROLL["notes"] > max_scroll:
        SCROLL["notes"] = max_scroll
    for i, ln in enumerate(lines[SCROLL["notes"]:SCROLL["notes"] + vis]):
        _txt(canvas, _trunc(ln, 150), 36, top + i * row_h, 13, TEXT)


# ---------------------------------------------------------------- HUB tab
def _draw_hub(canvas) -> None:
    _txt(canvas, "HUB", 36, CONTENT_Y + 6, 11, FAINT, True)
    _txt(canvas, "latest events   ·   click one for the full Log view",
         110, CONTENT_Y + 6, 11, FAINT)
    _rrect(canvas, 20, CONTENT_Y + 30, 1260, CONTENT_BOT, 10,
           fill=PANEL, outline=BORDER)
    events = list(reversed(recent_events(20)))
    row_h, top = 26, CONTENT_Y + 48
    vis = (CONTENT_BOT - top - 8) // row_h
    max_scroll = max(0, len(events) - vis)
    if SCROLL["hub"] > max_scroll:
        SCROLL["hub"] = max_scroll
    for i, (ts, level, msg) in enumerate(events[SCROLL["hub"]:SCROLL["hub"] + vis]):
        yy = top + i * row_h
        _txt(canvas, ts, 36, yy, 12, FAINT)
        _txt(canvas, _trunc(msg, 110), 120, yy, 13, _level_color(level))
        if i < vis - 1:
            cv2.line(canvas, (36, yy + 20), (1244, yy + 20), BORDER_SOFT, 1)
        _CLICKS.append((28, yy - 4, 1252, yy + 20, "hub", i))
    if not events:
        _txt(canvas, "No events yet.", 36, top, 13, FAINT)


# ---------------------------------------------------------------- main draw
def draw(canvas, ACC, ACC2, day_draw_fn: Callable) -> None:
    """Draw the whole OPS overlay. day_draw_fn(canvas, ACC, ACC2) draws the
    existing Day dashboard (callback avoids a hud<->ops_screen import cycle)."""
    _CLICKS.clear()
    canvas[:, :] = BG
    if ACTIVE_TAB in COL_TABS:
        _draw_columns(canvas)
    elif ACTIVE_TAB == "controls":
        _draw_controls(canvas)
    elif ACTIVE_TAB == "notes":
        _draw_notes(canvas)
    elif ACTIVE_TAB == "hub":
        _draw_hub(canvas)
    elif ACTIVE_TAB == "day":
        day_draw_fn(canvas, ACC, ACC2)
    _draw_tab_bar(canvas)
    _draw_status(canvas)


# ---------------------------------------------------------------- input
def handle_click(x: int, y: int) -> bool:
    for (x0, y0, x1, y1, kind, arg) in _CLICKS:
        if x0 <= x <= x1 and y0 <= y <= y1:
            if kind == "tab":
                set_tab(TABS.index(arg))
            elif kind == "task":
                global SELECTED_TASK
                SELECTED_TASK = arg if SELECTED_TASK != arg else None
            elif kind == "hub":
                set_tab(TABS.index("log"))
            elif kind == "ctl":
                _ctl_action(arg)
            elif kind == "script":
                _run_script_file(arg)
            return True
    return False
