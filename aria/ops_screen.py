"""OPS screen — tab-driven mission-control overlay, rendered with OpenCV.

Tabs: [1] Log  [2] Tasks  [3] Sensors  [4] Controls  [5] Notes  [6] HUB  [7] Day

- Tabs 1-3 share the three-column layout (Log 30% | Tasks 40% | Sensors 30%)
  with the selected pane focused (accent border).
- Controls / Notes / HUB / Day replace the three-column layout.
- HUB is the default landing tab (O opens the panel here): the 20 most
  recent events in one scrollable list, click one to jump to the full Log.
- Day reuses the existing command-center dashboard via a draw callback.

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

from aria import config as _config
from aria import ops as _ops
from aria import scheduler as _sched
from aria import speech as _speech
from aria import vision as _vision
from aria.agent import workers as _workers

# ---------------------------------------------------------------- palette
# BGR tuples. (Hershey fonts stand in for Roboto Mono — no PIL/TTF dep.)
ACCENT = (255, 132, 10)      # #0a84ff
BG = (13, 17, 23)            # #0d1117
PANEL = (17, 17, 17)          # #111
BORDER = (34, 34, 34)        # #222
TEXT = (235, 235, 235)
DIM = (150, 160, 170)
ERR = (85, 85, 255)          # #ff5555
WARN = (0, 204, 255)         # #ffcc00
INFO = (208, 192, 136)       # #88c0d0
OK = (120, 200, 120)

W, H = 1280, 720
TAB_H = 48
CONTENT_Y = 56
CONTENT_BOT = 676
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
    return re.sub(r"[^\x20-\x7e]", "?", str(s))


def _trunc(s: str, n: int) -> str:
    s = _sanitize(s)
    return s if len(s) <= n else s[:n - 3] + "..."


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


# ---------------------------------------------------------------- draw helpers
def _txt(canvas, s, x, y, scale=0.5, color=TEXT, thick=1):
    cv2.putText(canvas, _sanitize(s), (int(x), int(y)),
                cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def _panel(canvas, x0, y0, x1, y1, focused=False):
    cv2.rectangle(canvas, (x0, y0), (x1, y1), PANEL, -1)
    cv2.rectangle(canvas, (x0, y0), (x1, y1), ACCENT if focused else BORDER, 1)


def _gauge(canvas, cx, cy, r, frac, label, val_text, color=ACCENT):
    frac = max(0.0, min(1.0, frac))
    cv2.ellipse(canvas, (cx, cy), (r, r), 0, 0, 360, BORDER, 6)
    if frac > 0.01:
        cv2.ellipse(canvas, (cx, cy), (r, r), 0, 90, 90 - 360 * frac, color, 6)
    _txt(canvas, val_text, cx - 28, cy + 6, 0.55, TEXT, 2)
    _txt(canvas, label, cx - 28, cy + r + 22, 0.45, DIM, 1)


def _provider_name() -> str:
    try:
        d = (_ops.get_dashboard().get("providers") or {}).get("data") or {}
        return str(d.get("active") or d.get("primary") or "?")
    except Exception:
        return "?"


def _level_color(level: str):
    return {"error": ERR, "warn": WARN}.get(level, INFO)


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


def _status_glyph(status: str) -> Tuple[str, Any]:
    if status == "running":
        glyph = ["|", "/", "-", "\\"][int(time.time() * 4) % 4]
        return glyph, WARN
    if status in ("done", "completed"):
        return "OK", OK
    if status in ("failed", "error"):
        return "!!", ERR
    return "?", DIM


# ---------------------------------------------------------------- panes
def _pane_log(canvas, x0, y0, x1, y1, focused):
    _panel(canvas, x0, y0, x1, y1, focused)
    _txt(canvas, "[ LOG ]", x0 + 12, y0 + 22, 0.5, ACCENT if focused else DIM, 1)
    with _LOG_LOCK:
        lines = list(_LOG_BUF)
    row_h, top = 18, y0 + 46
    vis = (y1 - top) // row_h
    start = max(0, len(lines) - vis - SCROLL["log"])
    if SCROLL["log"] > max(0, len(lines) - vis):
        SCROLL["log"] = max(0, len(lines) - vis)
    for i, (ts, level, msg) in enumerate(lines[start:start + vis]):
        _txt(canvas, f"{ts} {_trunc(msg, 80)}", x0 + 12, top + i * row_h,
             0.42, _level_color(level), 1)
    if not lines:
        _txt(canvas, "(no events yet)", x0 + 12, top, 0.42, DIM, 1)


def _pane_tasks(canvas, x0, y0, x1, y1, focused):
    _panel(canvas, x0, y0, x1, y1, focused)
    _txt(canvas, "[ TASKS ]", x0 + 12, y0 + 22, 0.5, ACCENT if focused else DIM, 1)
    rows = _task_rows()
    _txt(canvas, f"{'ID':<6}{'STATUS':<8}{'ETA':<18}DESCRIPTION",
         x0 + 12, y0 + 44, 0.4, DIM, 1)
    row_h, top = 20, y0 + 64
    vis = (y1 - top - 60) // row_h
    start = SCROLL["tasks"]
    if start > max(0, len(rows) - vis):
        start = SCROLL["tasks"] = max(0, len(rows) - vis)
    for i, r in enumerate(rows[start:start + vis]):
        yy = top + i * row_h
        glyph, gcol = _status_glyph(r["status"])
        if r["id"] == SELECTED_TASK:
            cv2.rectangle(canvas, (x0 + 6, yy - 14), (x1 - 6, yy + 5), BORDER, -1)
        _txt(canvas, f"{r['id']:<6}", x0 + 12, yy, 0.4, TEXT, 1)
        _txt(canvas, f"{glyph:<8}", x0 + 72, yy, 0.4, gcol, 1)
        _txt(canvas, f"{_trunc(r['eta'], 16):<18}", x0 + 140, yy, 0.4, DIM, 1)
        _txt(canvas, _trunc(r["desc"], 34), x0 + 270, yy, 0.4, TEXT, 1)
        _CLICKS.append((x0 + 6, yy - 14, x1 - 6, yy + 5, "task", r["id"]))
    # expanded detail
    if SELECTED_TASK:
        sel = next((r for r in rows if r["id"] == SELECTED_TASK), None)
        if sel:
            dy = y1 - 52
            cv2.line(canvas, (x0 + 10, dy - 14), (x1 - 10, dy - 14), BORDER, 1)
            _txt(canvas, _trunc(f"> {sel['id']}: {sel['detail']}", 72),
                 x0 + 12, dy + 4, 0.4, INFO, 1)
            if sel["kind"] == "job" and sel.get("job_id") is not None:
                try:
                    tail = _workers.get_background_job_log(sel["job_id"], 2)
                    last = _sanitize(tail.strip().splitlines()[-1]) if tail.strip() else "(empty)"
                    _txt(canvas, _trunc("  " + last, 72), x0 + 12, dy + 22, 0.4, DIM, 1)
                except Exception:
                    pass


def _gpu_pct() -> Optional[float]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
        return float(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def _pane_sensors(canvas, x0, y0, x1, y1, focused):
    _panel(canvas, x0, y0, x1, y1, focused)
    _txt(canvas, "[ SENSORS ]", x0 + 12, y0 + 22, 0.5, ACCENT if focused else DIM, 1)
    try:
        sysd = (_ops.fetch_systems().get("data") or {})
    except Exception:
        sysd = {}
    cpu = float(sysd.get("cpu_pct") or 0)
    mem = float(sysd.get("mem_pct") or 0)
    disk = float(sysd.get("disk_pct") or 0)
    gy = y0 + 92
    r = 44
    n = 4
    gw = (x1 - x0 - 40) // n
    gauges = [("CPU", cpu / 100.0, f"{cpu:.0f}%"),
              ("RAM", mem / 100.0, f"{mem:.0f}%"),
              ("DISK", disk / 100.0, f"{disk:.0f}%")]
    gpu = _gpu_pct()
    gauges.append(("GPU", (gpu / 100.0) if gpu is not None else 0.0,
                   f"{gpu:.0f}%" if gpu is not None else "n/a"))
    for i, (label, frac, val) in enumerate(gauges):
        gx = x0 + 20 + gw * i + gw // 2
        _gauge(canvas, gx, gy, r, frac, label, val)
    # webcam preview (optional)
    frame = getattr(_vision, "LATEST_CAMERA_FRAME", None)
    py = gy + r + 44
    _txt(canvas, "CAM", x0 + 12, py, 0.45, DIM, 1)
    if frame is not None and py + 150 < y1:
        try:
            thumb = cv2.resize(frame, (200, 150))
            h_, w_ = thumb.shape[:2]
            canvas[py + 8:py + 8 + h_, x0 + 12:x0 + 12 + w_] = thumb
            cv2.rectangle(canvas, (x0 + 12, py + 8),
                          (x0 + 12 + w_, py + 8 + h_), BORDER, 1)
        except Exception:
            _txt(canvas, "(cam unavailable)", x0 + 60, py, 0.42, DIM, 1)
    else:
        _txt(canvas, "(cam unavailable)", x0 + 60, py, 0.42, DIM, 1)
    upt = sysd.get("uptime_s")
    if upt:
        _txt(canvas, f"UP {_ops.fmt_uptime(upt)}", x0 + 12, y1 - 14, 0.42, DIM, 1)


def _draw_columns(canvas, ACC):
    """Three-column layout shared by Log/Tasks/Sensors tabs."""
    focus = ACTIVE_TAB if ACTIVE_TAB in COL_TABS else None
    _pane_log(canvas, 20, CONTENT_Y, 392, CONTENT_BOT, focus == "log")
    _pane_tasks(canvas, 400, CONTENT_Y, 896, CONTENT_BOT, focus == "tasks")
    _pane_sensors(canvas, 904, CONTENT_Y, 1260, CONTENT_BOT, focus == "sensors")


# ---------------------------------------------------------------- controls tab
_CONTROLS = [
    ("sched", "SCHEDULER", "start/stop"),
    ("shot", "SCREENSHOT", "capture"),
    ("voice", "VOICE", "on/off"),
    ("script", "RUN SCRIPT", "pick below"),
    ("close", "CLOSE OPS", "back to face"),
    ("quit", "QUIT ARIA", "shutdown"),
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


def _draw_controls(canvas, ACC):
    _txt(canvas, "[ CONTROLS ]", 32, CONTENT_Y + 22, 0.5, ACCENT, 1)
    n = len(_CONTROLS)
    bs, gap = 96, 28
    total = n * bs + (n - 1) * gap
    x0 = (W - total) // 2
    y0 = CONTENT_Y + 60
    for i, (name, label, _hint) in enumerate(_CONTROLS):
        bx = x0 + i * (bs + gap)
        by = y0
        cv2.rectangle(canvas, (bx, by), (bx + bs, by + bs), PANEL, -1)
        cv2.rectangle(canvas, (bx, by), (bx + bs, by + bs),
                      ACCENT if _ctl_active(name) else BORDER, 2 if _ctl_active(name) else 1)
        sub = _ctl_sub(name)
        _txt(canvas, label, bx + 8, by + 44, 0.38, TEXT, 1)
        if sub:
            _txt(canvas, sub, bx + 8, by + 66, 0.38,
                 OK if _ctl_active(name) else WARN, 1)
        _CLICKS.append((bx, by, bx + bs, by + bs, "ctl", name))
    # script picker
    if SHOW_SCRIPTS:
        sy = y0 + bs + 36
        _txt(canvas, "Pick a script to run (output -> Tasks / Log):",
             x0, sy, 0.45, DIM, 1)
        try:
            files = sorted(f for f in os.listdir(_scripts_dir())
                           if f.endswith(".py"))
        except Exception:
            files = []
        if not files:
            _txt(canvas, f"(none yet — drop .py files in {_scripts_dir()})",
                 x0, sy + 26, 0.42, DIM, 1)
        for i, f in enumerate(files[:10]):
            yy = sy + 26 + i * 24
            _txt(canvas, f"> {f}", x0, yy, 0.45, INFO, 1)
            _CLICKS.append((x0, yy - 16, x0 + 400, yy + 6, "script", f))


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


# ---------------------------------------------------------------- notes tab
def _draw_notes(canvas, ACC):
    _txt(canvas, "[ NOTES ]  ~/ARIA/notes/ops.md   [T] type, autosaves",
         32, CONTENT_Y + 22, 0.5, ACCENT, 1)
    _panel(canvas, 20, CONTENT_Y + 36, 1260, CONTENT_BOT, False)
    lines = NOTES_BUF.splitlines() or ["(empty — press T to type)"]
    row_h, top = 20, CONTENT_Y + 62
    vis = (CONTENT_BOT - top) // row_h
    start = SCROLL["notes"]
    if start > max(0, len(lines) - vis):
        start = SCROLL["notes"] = max(0, len(lines) - vis)
    for i, ln in enumerate(lines[start:start + vis]):
        _txt(canvas, _trunc(ln, 150), 36, top + i * row_h, 0.45, TEXT, 1)


# ---------------------------------------------------------------- HUB tab
def _draw_hub(canvas, ACC):
    _txt(canvas, "[ HUB ]  latest events — click one for the full Log view",
         32, CONTENT_Y + 22, 0.5, ACCENT, 1)
    _panel(canvas, 20, CONTENT_Y + 36, 1260, CONTENT_BOT, False)
    events = list(reversed(recent_events(20)))
    row_h, top = 24, CONTENT_Y + 66
    vis = (CONTENT_BOT - top) // row_h
    start = SCROLL["hub"]
    if start > max(0, len(events) - vis):
        start = SCROLL["hub"] = max(0, len(events) - vis)
    for i, (ts, level, msg) in enumerate(events[start:start + vis]):
        yy = top + i * row_h
        _txt(canvas, ts, 36, yy, 0.45, DIM, 1)
        _txt(canvas, _trunc(msg, 110), 130, yy, 0.45, _level_color(level), 1)
        _CLICKS.append((28, yy - 17, 1252, yy + 6, "hub", i))
    if not events:
        _txt(canvas, "(no events yet)", 36, top, 0.45, DIM, 1)


# ---------------------------------------------------------------- chrome
def _draw_tab_bar(canvas):
    cv2.rectangle(canvas, (0, 0), (W, TAB_H), PANEL, -1)
    n = len(TABS)
    tw = W // n
    for i, (key, label) in enumerate(zip(TABS, TAB_LABELS)):
        x0 = i * tw
        x1 = (i + 1) * tw if i < n - 1 else W
        sel = (key == ACTIVE_TAB)
        if sel:
            cv2.rectangle(canvas, (x0, 0), (x1, TAB_H), ACCENT, -1)
            _txt(canvas, label, x0 + 14, 31, 0.55, (255, 255, 255), 2)
            cv2.line(canvas, (x0, 0), (x1, 0), (255, 255, 255), 3)
        else:
            _txt(canvas, label, x0 + 14, 31, 0.55, TEXT, 1)
        _CLICKS.append((x0, 0, x1, TAB_H, "tab", key))
    cv2.rectangle(canvas, (0, 0), (W - 1, H - 1), BORDER, 4)


def _draw_status(canvas):
    now = datetime.now().strftime("%H:%M:%S")
    prov = _provider_name()
    _txt(canvas, f"PROVIDER: {prov}", 28, STATUS_Y, 0.45, INFO, 1)
    hint = "[1-7] tabs   [J/K] scroll   [O]/[ESC] close   [E] read mail   [R] refresh"
    _txt(canvas, hint, 340, STATUS_Y, 0.42, DIM, 1)
    _txt(canvas, now, 1200, STATUS_Y, 0.45, DIM, 1)


# ---------------------------------------------------------------- main draw
def draw(canvas, ACC, ACC2, day_draw_fn: Callable) -> None:
    """Draw the whole OPS overlay. day_draw_fn(canvas, ACC, ACC2) draws the
    existing Day dashboard (callback avoids a hud<->ops_screen import cycle)."""
    _CLICKS.clear()
    canvas[:, :] = BG
    if ACTIVE_TAB in COL_TABS:
        _draw_columns(canvas, ACC)
    elif ACTIVE_TAB == "controls":
        _draw_controls(canvas, ACC)
    elif ACTIVE_TAB == "notes":
        _draw_notes(canvas, ACC)
    elif ACTIVE_TAB == "hub":
        _draw_hub(canvas, ACC)
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
