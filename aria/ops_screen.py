"""OPS screen v2 — 4-tab mission control, new visual language.

Tabs: [1] DASH  [2] LOG  [3] SYSTEM  [4] DAY

- DASH: 3-second health glance — status pills (provider / model / scheduler /
  voice / loop guard / keys), recent events, active tasks.
- LOG: full-width event log with level filters + text search, click any row
  for the inspector pane (full message, JSON syntax tint, per-call stats).
- SYSTEM: provider-chain strip (order, active leg, quarantines + countdowns,
  last error), sensor gauges + camera, control buttons.
- DAY: the existing command-center dashboard via a draw callback, with the
  notes file folded in on the right (T edits notes, autosaves).

Typography: Roboto Mono (bundled in aria/assets/) rendered through PIL.

Keys (handled by main.py): 1-4 switch tabs, J/K or arrows scroll, / searches
the log, T edits notes on DAY, O or ESC closes.
Mouse: click tabs, filter chips, log rows, task rows, control buttons.
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
from aria.agent import sentinel as _sentinel

# ---------------------------------------------------------------- palette (BGR)
ACCENT = (247, 129, 47)      # #2f81f7 — electric blue
BG = (20, 14, 10)            # #0a0e14 — deep navy-black
PANEL = (31, 22, 17)         # #11161f — cards
PANEL2 = (41, 30, 24)        # #181e29 — raised surfaces (buttons)
BORDER = (61, 47, 38)        # #262f3d — card borders
BORDER_SOFT = (45, 36, 29)   # hairlines
TEXT = (243, 237, 230)       # #e6edf3
DIM = (158, 148, 139)        # #8b949e
FAINT = (115, 103, 92)       # #5c6773
ERR = (73, 81, 248)          # #f85149
WARN = (34, 153, 210)        # #d29922
INFO = (255, 166, 88)        # #58a6ff
OK = (80, 185, 63)           # #3fb950
GREEN_DOT = (110, 200, 110)

W, H = 1280, 720
TAB_H = 64
CONTENT_Y = 76
CONTENT_BOT = 684
STATUS_Y = 700

TABS = ["dash", "log", "system", "day"]
TAB_LABELS = ["[1] DASH", "[2] LOG", "[3] SYSTEM", "[4] DAY"]

# ---------------------------------------------------------------- state
ACTIVE_TAB = "dash"
SCROLL: Dict[str, int] = {t: 0 for t in TABS}
SELECTED_TASK: Optional[str] = None
SHOW_SCRIPTS = False

# LOG tab: level filter ("all"/"info"/"warn"/"error") + text search.
LOG_FILTER = "all"
LOG_SEARCH = ""
SEARCH_FOCUS = False
# DAY tab: notes editing focus.
NOTES_FOCUS = False

STATE_PATH = os.path.join(_config.WORKSPACE_DIR, "ops_state.json")
NOTES_PATH = os.path.join(_config.WORKSPACE_DIR, "notes", "ops.md")
NOTES_BUF = ""

_CLICKS: List[Tuple[int, int, int, int, str, Any]] = []  # rebuilt every draw


def _sanitize(s: str) -> str:
    # Roboto Mono covers these; everything else becomes "?" so PIL/cv2 never chokes.
    return re.sub(r"[^\x20-\x7e·—–●→✓★]", "?", str(s))


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


def _blend_bgr(fg: Tuple[int, int, int], bg: Tuple[int, int, int],
               alpha: int) -> Tuple[int, int, int]:
    """Pre-blended BGR for cv2 drawing (cv2 has no alpha; _rgba is PIL-only)."""
    a = max(0.0, min(1.0, alpha / 255.0))
    return tuple(int(fg[i] * a + bg[i] * (1.0 - a)) for i in range(3))


ACCENT_DIM = _blend_bgr(ACCENT, BG, 70)  # subtle selection tint


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


def _pill(canvas, x: int, y: int, label: str,
          color: Tuple[int, int, int]) -> int:
    """Filled status pill. Returns the pill width."""
    w = _tw(label, 11, True) + 22
    _rrect(canvas, x, y, x + w, y + 24, 12, fill=color)
    _txt(canvas, label, x + 11, y + 5, 11, BG, True)
    return w


def _pill_outline(canvas, x: int, y: int, label: str,
                  color: Tuple[int, int, int]) -> int:
    """Outline pill for inactive states. Returns the pill width."""
    w = _tw(label, 11, True) + 22
    _rrect(canvas, x, y, x + w, y + 24, 12, outline=color, width=1)
    _txt(canvas, label, x + 11, y + 5, 11, color, True)
    return w


def _card(canvas, x0, y0, x1, y1, title: Optional[str] = None) -> int:
    """Card with optional small-caps title + hairline. Returns content top."""
    _rrect(canvas, x0, y0, x1, y1, 12, fill=PANEL, outline=BORDER)
    if title:
        _txt(canvas, title, x0 + 18, y0 + 12, 11, FAINT, True)
        cv2.line(canvas, (x0 + 18, y0 + 34), (x1 - 18, y0 + 34), BORDER_SOFT, 1)
        return y0 + 46
    return y0 + 14

# ---------------------------------------------------------------- log feed
# The canonical event ring lives in aria.config (bounded at 1000 entries;
# each entry is (hh:mm:ss, level, msg, meta)). This module only reads it.
_LOG_LOCK = threading.Lock()

# Row inspector: selected event, as (tab_name, view_index) or None.
SELECTED_EVENT = None


def recent_events(n: int = 20):
    return _config.recent_events(n)


def _level_color(level: str):
    return {"error": ERR, "warn": WARN}.get(level, INFO)


def _filtered_events():
    """LOG tab: apply level filter + text search to the event ring."""
    lines = recent_events(500)
    if LOG_FILTER != "all":
        lines = [e for e in lines if e[1] == LOG_FILTER]
    q = LOG_SEARCH.strip().lower()
    if q:
        lines = [e for e in lines if q in e[2].lower()]
    return lines


# ---------------------------------------------------------------- persistence
def load_state() -> None:
    global ACTIVE_TAB, SELECTED_TASK, LOG_FILTER
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
        if st.get("active_tab") in TABS:
            ACTIVE_TAB = st["active_tab"]
        sc = st.get("scroll") or {}
        for t in TABS:
            SCROLL[t] = max(0, int(sc.get(t, 0)))
        SELECTED_TASK = st.get("selected_task")
        if st.get("log_filter") in ("all", "info", "warn", "error"):
            LOG_FILTER = st["log_filter"]
    except Exception:
        pass


def save_state() -> None:
    save_notes()
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"active_tab": ACTIVE_TAB, "scroll": SCROLL,
                       "selected_task": SELECTED_TASK,
                       "log_filter": LOG_FILTER}, f)
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
    """Atomic write via temp file + os.replace: a crash mid-write can never
    leave a half-written notes file."""
    global _NOTES_DIRTY_AT
    try:
        os.makedirs(os.path.dirname(NOTES_PATH), exist_ok=True)
        tmp = NOTES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(NOTES_BUF)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, NOTES_PATH)
        _NOTES_DIRTY_AT = 0.0
    except Exception:
        pass


load_state()
load_notes()


# ---------------------------------------------------------------- panel API
def open_panel() -> None:
    """O key: open OPS on the DASH tab."""
    global ACTIVE_TAB
    ACTIVE_TAB = "dash"
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


# ---------------------------------------------------------------- search / notes focus
def focus_search() -> None:
    global SEARCH_FOCUS, NOTES_FOCUS
    SEARCH_FOCUS = True
    NOTES_FOCUS = False

def unfocus_search() -> None:
    global SEARCH_FOCUS
    SEARCH_FOCUS = False


def search_editing() -> bool:
    return SEARCH_FOCUS and ACTIVE_TAB == "log"


def search_type(ch: str) -> None:
    global LOG_SEARCH
    if len(LOG_SEARCH) < 60:
        LOG_SEARCH += ch


def search_backspace() -> None:
    global LOG_SEARCH
    LOG_SEARCH = LOG_SEARCH[:-1]


def focus_notes() -> None:
    global NOTES_FOCUS, SEARCH_FOCUS
    NOTES_FOCUS = True
    SEARCH_FOCUS = False


def unfocus_notes() -> None:
    global NOTES_FOCUS
    save_notes()
    NOTES_FOCUS = False


def notes_editing() -> bool:
    return NOTES_FOCUS and ACTIVE_TAB == "day"


# ---------------------------------------------------------------- notes editing
_NOTES_DIRTY_AT: float = 0.0
_NOTES_DEBOUNCE_S = 0.3


def _notes_mark_dirty() -> None:
    global _NOTES_DIRTY_AT
    _NOTES_DIRTY_AT = time.time()


def notes_flush_if_due() -> None:
    """Called from the main loop: atomically saves debounced keystrokes."""
    global _NOTES_DIRTY_AT
    if _NOTES_DIRTY_AT and time.time() - _NOTES_DIRTY_AT >= _NOTES_DEBOUNCE_S:
        _NOTES_DIRTY_AT = 0.0
        save_notes()


def notes_type(ch: str) -> None:
    global NOTES_BUF
    NOTES_BUF += ch
    _notes_mark_dirty()


def notes_backspace() -> None:
    global NOTES_BUF
    NOTES_BUF = NOTES_BUF[:-1]
    _notes_mark_dirty()


def notes_newline() -> None:
    global NOTES_BUF
    NOTES_BUF += "\n"
    save_notes()  # autosave (immediate on newline)


# ---------------------------------------------------------------- inbox viewer
# DAY tab: recent important emails. Click a row to open the reader overlay
# with LISTEN (TTS) and SUMMARIZE (short paragraph via provider chain).
SELECTED_EMAIL: Optional[str] = None
_EMAIL_BODIES: Dict[str, str] = {}
_EMAIL_SUMMARIES: Dict[str, str] = {}
_EMAIL_SUMMARIZING: set = set()


def _inbox_items():
    try:
        d = _ops.fetch_inbox()
        if not d.get("connected"):
            return [], False
        return d.get("items") or [], True
    except Exception:
        return [], False


def _email_body(uid: str) -> str:
    if uid not in _EMAIL_BODIES:
        try:
            _EMAIL_BODIES[uid] = _ops.read_mail_body(uid, max_chars=3000)
        except Exception as e:
            _EMAIL_BODIES[uid] = f"Couldn't open that email: {e}"
    return _EMAIL_BODIES[uid]


def _listen_email(uid: str, sender: str, subject: str) -> None:
    """Speak the email (summary if available, else the body) in a thread."""
    def _run():
        try:
            text = _EMAIL_SUMMARIES.get(uid) or _email_body(uid)
            _speech.speak(f"Email from {sender}. Subject: {subject}. {text[:1200]}")
        except Exception as e:
            _config.add_log(f"Email listen failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    _config.add_log(f"Reading email from {sender} aloud.")


def _summarize_email(uid: str, sender: str, subject: str) -> None:
    """Generate a short paragraph summary via the provider chain (threaded)."""
    if uid in _EMAIL_SUMMARIZING or uid in _EMAIL_SUMMARIES:
        return
    _EMAIL_SUMMARIZING.add(uid)

    def _run():
        try:
            body = _email_body(uid)
            text = None
            try:
                from aria.agent import providers as _pv
                contents = [{"role": "user", "parts": [{
                    "text": f"From: {sender}\nSubject: {subject}\n\n{body[:2500]}"}]}]
                for p in _pv._build_chain():
                    if not p.is_available():
                        continue
                    try:
                        resp = p.call(
                            "Summarize this email in 2-3 short sentences. "
                            "Plain text only, no preamble.",
                            contents)
                        if resp:
                            for cand in resp.get("candidates") or []:
                                for part in (cand.get("content") or {}).get("parts") or []:
                                    if part.get("text"):
                                        text = part["text"].strip()
                                        break
                                if text:
                                    break
                        if text:
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            _EMAIL_SUMMARIES[uid] = text or "(couldn't summarize — provider chain unavailable)"
        except Exception as e:
            _EMAIL_SUMMARIES[uid] = f"(summary failed: {e})"
        finally:
            _EMAIL_SUMMARIZING.discard(uid)
    threading.Thread(target=_run, daemon=True).start()

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


_TASK_PILL = {
    "running": (WARN, "RUNNING"),
    "sched": (INFO, "SCHED"),
    "done": (OK, "DONE"),
    "completed": (OK, "DONE"),
    "failed": (ERR, "FAILED"),
    "error": (ERR, "FAILED"),
}


def _task_pill(status: str):
    return _TASK_PILL.get(status, (DIM, "?"))


def _cancel_job(job_id: Any) -> None:
    try:
        ok = _workers.cancel_background_job(int(job_id))
        _config.add_log(f"Job #{job_id} {'cancelled' if ok else 'not cancellable'}.")
    except Exception as e:
        _config.add_log(f"Cancel job #{job_id} failed: {e}")


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
    ("sentinel", "LOOP GUARD"),
    ("script", "RUN SCRIPT"),
    ("close", "CLOSE OPS"),
    ("quit", "QUIT ARIA"),
]


def _ctl_sub(name: str) -> str:
    if name == "sched":
        return "PAUSED" if _sched.is_paused() else "RUNNING"
    if name == "voice":
        return "ON" if getattr(_speech, "VOICE_ENABLED", True) else "OFF"
    if name == "sentinel":
        st = _sentinel.status()
        if not st["enabled"]:
            return "OFF"
        if st["tripped"]:
            return "TRIPPED"
        if st["cooldown_s"] > 0:
            return "COOLDOWN"
        return "ARMED"
    return ""


def _ctl_active(name: str) -> bool:
    if name == "sched":
        return not _sched.is_paused()
    if name == "voice":
        return bool(getattr(_speech, "VOICE_ENABLED", True))
    if name == "sentinel":
        st = _sentinel.status()
        return bool(st["enabled"] and not st["tripped"])
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
    elif name == "sentinel":
        on = _sentinel.set_enabled(not _sentinel.is_enabled())
        _config.add_log(f"Loop guard {'armed' if on else 'disabled'}.")
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


# ---------------------------------------------------------------- provider chain
def _chain_legs() -> List[Dict[str, Any]]:
    """Legs for the SYSTEM provider strip: name, model, state, detail."""
    try:
        d = _ops.fetch_providers()
        active = d.get("active") or ""
        legs = []
        for leg in d.get("chain") or []:
            name = str(leg.get("name") or "?")
            state = str(leg.get("state") or "standby")
            legs.append({
                "name": name,
                "model": str(leg.get("model") or ""),
                "state": "serving" if name == active and state == "live" else state,
                "detail": str(leg.get("detail") or ""),
            })
        return legs
    except Exception:
        return []


def _chain_pill(state: str):
    if state == "serving":
        return (OK, "SERVING")
    if state == "quarantined":
        return (WARN, "QUAR")
    return (DIM, "READY")


def _model_name() -> str:
    try:
        from aria.agent import providers as _pv
        chain = _pv._build_chain()
        active = _pv.get_active_provider()
        for p in chain:
            if getattr(p, "name", "") == active:
                return str(getattr(p, "model", "") or "")
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- routines (AST-extracted by tests — keep verbatim shape)
def _routine_rows():
    """Read-only: saved routines from ~/ARIA/routines/*.json.

    Uses stdlib json directly and deliberately does NOT import aria.routines
    (keeps the lazy-import discipline / cycle-safe). Each row is
    {"name", "steps", "trusted"}. Missing dir -> empty list.
    """
    rows = []
    try:
        rdir = os.path.expanduser(os.path.join("~", "ARIA", "routines"))
        if not os.path.isdir(rdir):
            return rows
        for fname in sorted(os.listdir(rdir)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(rdir, fname), encoding="utf-8") as _f:
                    data = json.load(_f)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            steps = data.get("steps")
            rows.append({
                "name": str(data.get("name") or fname[:-5]),
                "steps": len(steps) if isinstance(steps, list) else 0,
                "trusted": bool(data.get("trusted", False)),
            })
    except Exception:
        return []
    return rows


def _draw_routines_section(canvas, x0, x1, y1, rows_bottom) -> None:
    """Read-only Routines listing below the task rows."""
    div = rows_bottom + 10
    if div > y1 - 90:  # not enough room — skip rather than overlap the detail strip
        return
    cv2.line(canvas, (x0 + 16, div), (x1 - 16, div), BORDER_SOFT, 1)
    _txt(canvas, "ROUTINES", x0 + 16, div + 10, 11, FAINT, True)
    top_r = div + 34
    routines = _routine_rows()
    if not routines:
        _txt(canvas, "no routines yet", x0 + 16, top_r, 12, FAINT)
        return
    # reserve the bottom strip (expanded-task detail is drawn at y1 - 56)
    max_r = max(0, (y1 - 70 - top_r) // 20)
    for i, r in enumerate(routines[:max_r]):
        yy = top_r + i * 20
        _txt(canvas, _trunc(r["name"], 28), x0 + 16, yy, 12, TEXT, True)
        _txt(canvas, f"{r['steps']} steps", x0 + 240, yy, 12, DIM)
        _txt(canvas, "trusted" if r["trusted"] else "untrusted",
             x0 + 330, yy, 12, OK if r["trusted"] else FAINT)

# ---------------------------------------------------------------- tab bar + status
def _draw_tab_bar(canvas) -> None:
    cv2.rectangle(canvas, (0, 0), (W, TAB_H), (26, 18, 13), -1)  # #0d121a
    cv2.line(canvas, (0, TAB_H), (W, TAB_H), BORDER, 1)
    x = 28
    for i, (key, label) in enumerate(zip(TABS, TAB_LABELS)):
        lbl = f"{i + 1}  {label.split('] ')[1]}"
        w = _tw(lbl, 14, True) + 36
        is_act = key == ACTIVE_TAB
        if is_act:
            _rrect(canvas, x, 10, x + w, TAB_H - 6, 10, fill=PANEL2)
            cv2.rectangle(canvas, (x + 14, TAB_H - 8), (x + w - 14, TAB_H - 4),
                          ACCENT, -1)
        _txt(canvas, lbl, x + 18, 24, 14, TEXT if is_act else DIM, True)
        _CLICKS.append((x, 0, x + w, TAB_H, "tab", key))
        x += w + 8
    # provider chip + clock, right side
    now = datetime.now().strftime("%H:%M:%S")
    cw = _tw(now, 13)
    _txt(canvas, now, W - 28 - cw, 26, 13, FAINT)
    _pill(canvas, W - 28 - cw - 160, 20, "● " + _provider_name(), OK)


def _draw_status(canvas) -> None:
    cv2.rectangle(canvas, (0, CONTENT_BOT + 16), (W, H), (26, 18, 13), -1)
    cv2.line(canvas, (0, CONTENT_BOT + 16), (W, CONTENT_BOT + 16), BORDER, 1)
    hint = "[1-4] tabs    [J/K] scroll    [/] search    [T] notes    [O]/[ESC] close"
    _txt(canvas, hint, 28, STATUS_Y - 2, 12, FAINT)
    now = datetime.now().strftime("%H:%M:%S")
    cw = _tw(now, 12)
    _txt(canvas, now, W - 28 - cw, STATUS_Y - 2, 12, FAINT)


# ---------------------------------------------------------------- text helpers
def _wrap_lines(s: str, width: int):
    words = str(s).split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _txt_json_line(canvas, line: str, x: int, y: int) -> None:
    """Light JSON highlighting for the inspector: keys INFO, strings TEXT,
    numbers WARN, punctuation DIM."""
    cx, pos = x, 0
    for m in re.finditer(r'"[^"]*"|[-+]?\d+(?:\.\d+)?|\S', line):
        pre = line[pos:m.start()]
        if pre:
            _txt(canvas, pre, cx, y, 13, DIM)
            cx += _tw(pre, 13)
        tok = m.group(0)
        if tok.startswith('"'):
            col = INFO if line[m.end():m.end() + 1] == ":" else TEXT
        elif re.fullmatch(r"[-+]?\d+(?:\.\d+)?", tok):
            col = WARN
        else:
            col = DIM
        _txt(canvas, tok, cx, y, 13, col)
        cx += _tw(tok, 13)
        pos = m.end()
    rest = line[pos:]
    if rest:
        _txt(canvas, rest, cx, y, 13, DIM)


def _draw_inspector(canvas, x0: int, y0: int, x1: int, y1: int, entry) -> None:
    """Right-side inspector for a selected log row: full message + meta."""
    ts, level, msg, meta = entry
    meta = meta or {}
    _card(canvas, x0, y0, x1, y1, "INSPECTOR")
    _txt(canvas, ts, x0 + 18, y0 + 46, 12, FAINT)
    _txt(canvas, level.upper(), x0 + 100, y0 + 46, 12, _level_color(level), True)
    _txt(canvas, _trunc(msg, 48), x0 + 18, y0 + 70, 13, TEXT, True)
    bits = []
    if meta.get("duration_ms") is not None:
        bits.append(f"{meta['duration_ms']}ms")
    pt, ct = meta.get("prompt_tokens"), meta.get("completion_tokens")
    if pt is not None or ct is not None:
        bits.append(f"in {pt if pt is not None else '?'} / out "
                    f"{ct if ct is not None else '?'} tok")
    if meta.get("provider"):
        bits.append(f"via {meta['provider']}")
    if bits:
        _txt(canvas, "  ·  ".join(bits), x0 + 18, y0 + 94, 12, INFO)
    yy = y0 + 122
    width = max(20, int((x1 - x0 - 36) / 7.8))
    body = msg if len(msg) <= 900 else msg[:900] + "…"
    is_json = body.strip()[:1] in "{["
    for line in _wrap_lines(body, width)[:24]:
        if yy > y1 - 24:
            break
        if is_json:
            _txt_json_line(canvas, line, x0 + 18, yy)
        else:
            _txt(canvas, line, x0 + 18, yy, 13, TEXT)
        yy += 20
    if not is_json and len(body) > 200:
        _txt(canvas, "(full text — scroll the log row)", x0 + 18, y1 - 28, 11, FAINT)


# ---------------------------------------------------------------- DASH tab
def _dash_pills(canvas) -> None:
    """Status strip: provider / model / scheduler / voice / loop guard / keys."""
    y = CONTENT_Y + 4
    items = []
    items.append(("PROVIDER", _provider_name(), OK))
    model = _model_name()
    items.append(("MODEL", model.split("/")[-1] if model else "?", OK))
    sched_paused = _sched.is_paused()
    items.append(("SCHEDULER", "PAUSED" if sched_paused else "RUNNING",
                  WARN if sched_paused else OK))
    voice_on = bool(getattr(_speech, "VOICE_ENABLED", True))
    items.append(("VOICE", "ON" if voice_on else "OFF", OK if voice_on else WARN))
    try:
        st = _sentinel.status()
        lg = "OFF" if not st["enabled"] else ("TRIPPED" if st["tripped"] else "ARMED")
        lg_col = ERR if st["tripped"] else (WARN if not st["enabled"] else OK)
    except Exception:
        lg, lg_col = "?", DIM
    items.append(("LOOP GUARD", lg, lg_col))
    try:
        legs = _chain_legs()
        quar = sum(1 for l in legs if l["state"] == "quarantined")
        keys_lbl = f"{len(legs) - quar}/{len(legs)} OK" if legs else "?"
        items.append(("KEYS", keys_lbl, OK if quar == 0 else WARN))
    except Exception:
        items.append(("KEYS", "?", DIM))
    x = 28
    for label, value, col in items:
        _txt(canvas, label, x, y, 11, FAINT, True)
        _pill(canvas, x, y + 18, value, col)
        x += max(_tw(value, 11, True) + 22, _tw(label, 11, True)) + 26


def _draw_dash(canvas) -> None:
    _dash_pills(canvas)
    y0 = CONTENT_Y + 74
    # recent events — left 60%
    ey = _card(canvas, 28, y0, 760, CONTENT_BOT, "RECENT EVENTS")
    events = list(reversed(recent_events(14)))
    yy = ey
    for ts, level, msg, _meta in events:
        _txt(canvas, ts, 46, yy, 12, FAINT)
        _txt(canvas, _trunc(msg, 72), 130, yy, 13,
             _level_color(level) if level != "info" else TEXT)
        yy += 30
        if yy > CONTENT_BOT - 40:
            break
    if not events:
        _txt(canvas, "No events yet.", 46, ey, 13, FAINT)
    # active tasks — right 40%
    ty = _card(canvas, 776, y0, W - 28, CONTENT_BOT, "ACTIVE TASKS")
    rows = _task_rows()[:10]
    yy = ty
    for r in rows:
        _txt(canvas, _trunc(r["desc"], 30), 794, yy, 13, TEXT, True)
        col, lbl = _task_pill(r["status"])
        pw = _pill(canvas, 1050, yy - 3, lbl, col)
        _CLICKS.append((776, yy - 6, W - 28, yy + 26, "task", r["id"]))
        if r["kind"] == "job" and r["status"] == "running" and r.get("job_id") is not None:
            cw = _pill_outline(canvas, 1050 + pw + 8, yy - 3, "CANCEL", ERR)
            _CLICKS.append((1050 + pw + 8, yy - 3, 1050 + pw + 8 + cw, yy + 21,
                            "jobcancel", r["job_id"]))
        yy += 36
        if yy > CONTENT_BOT - 40:
            break
    if not rows:
        _txt(canvas, "No tasks.", 794, ty, 13, FAINT)
    _draw_routines_section(canvas, 776, W - 28, CONTENT_BOT, yy + 6)


# ---------------------------------------------------------------- LOG tab
_FILTERS = [("all", "ALL"), ("info", "INFO"), ("warn", "WARN"), ("error", "ERROR")]


def _draw_log(canvas) -> None:
    global SELECTED_EVENT
    # filter chips + search
    y = CONTENT_Y + 4
    x = 28
    for key, label in _FILTERS:
        if LOG_FILTER == key:
            w = _pill(canvas, x, y, label, ACCENT)
        else:
            w = _pill_outline(canvas, x, y, label, DIM)
        _CLICKS.append((x, y, x + w, y + 24, "filter", key))
        x += w + 10
    # search box
    sx0 = x + 10
    _rrect(canvas, sx0, y, W - 28, y + 26, 8,
           outline=ACCENT if SEARCH_FOCUS else BORDER, width=2 if SEARCH_FOCUS else 1)
    placeholder = LOG_SEARCH if LOG_SEARCH else "/  search log..."
    _txt(canvas, placeholder, sx0 + 14, y + 5, 12,
         TEXT if LOG_SEARCH else FAINT)
    if SEARCH_FOCUS and int(time.time() * 2) % 2 == 0:
        cx = sx0 + 14 + _tw(placeholder, 12)
        cv2.line(canvas, (cx, y + 4), (cx, y + 20), TEXT, 1)
    _CLICKS.append((sx0, y, W - 28, y + 26, "searchbox", None))
    # log list + inspector
    y0 = CONTENT_Y + 46
    lines = _filtered_events()
    insp_w = 400
    lx1 = W - 28 - insp_w - 16
    ly = _card(canvas, 28, y0, lx1, CONTENT_BOT,
               f"EVENT LOG  ·  {len(lines)} entries"
               + (f"  ·  filter: {LOG_FILTER}" if LOG_FILTER != "all" else "")
               + (f'  ·  "{LOG_SEARCH}"' if LOG_SEARCH else ""))
    row_h, top = 26, ly
    vis = (CONTENT_BOT - top - 8) // row_h
    max_scroll = max(0, len(lines) - vis)
    if SCROLL["log"] > max_scroll:
        SCROLL["log"] = max_scroll
    start = max(0, len(lines) - vis - SCROLL["log"])
    max_chars = min(90, max(20, int((lx1 - 28 - 152) / 7.8)))
    for i, (ts, level, msg, meta) in enumerate(lines[start:start + vis]):
        yy = top + i * row_h
        if level == "error":
            cv2.rectangle(canvas, (28, yy - 4), (lx1, yy + 20), (40, 18, 20), -1)
        if SELECTED_EVENT == ("log", start + i):
            cv2.rectangle(canvas, (28, yy - 4), (lx1, yy + 20),
                          ACCENT_DIM, -1)
        _txt(canvas, ts, 46, yy, 12, FAINT)
        _txt(canvas, level.upper()[:4], 130, yy, 11, _level_color(level), True)
        _txt(canvas, _trunc(msg, max_chars), 180, yy, 13, TEXT)
        _CLICKS.append((28, yy - 4, lx1, yy + 20, "logrow", start + i))
    if not lines:
        _txt(canvas, "No matching events.", 46, top, 13, FAINT)
    # inspector pane
    ix0 = lx1 + 16
    if SELECTED_EVENT and SELECTED_EVENT[0] == "log":
        idx = SELECTED_EVENT[1]
        if 0 <= idx < len(lines):
            _draw_inspector(canvas, ix0, y0, W - 28, CONTENT_BOT, lines[idx])
        else:
            SELECTED_EVENT = None
    else:
        iy = _card(canvas, ix0, y0, W - 28, CONTENT_BOT, "INSPECTOR")
        _txt(canvas, "click a row", ix0 + 18, iy, 13, FAINT)


# ---------------------------------------------------------------- SYSTEM tab
def _draw_system(canvas) -> None:
    # provider chain strip — full width
    py = _card(canvas, 28, CONTENT_Y, W - 28, CONTENT_Y + 150, "PROVIDER CHAIN")
    legs = _chain_legs()
    if not legs:
        _txt(canvas, "chain unavailable", 46, py, 13, FAINT)
    else:
        x = 46
        slot = (W - 92) / max(1, len(legs))
        for i, leg in enumerate(legs):
            col, lbl = _chain_pill(leg["state"])
            detail = leg.get("detail") or ""
            _txt(canvas, leg["name"].upper(), x, py, 12, TEXT, True)
            pw = _pill(canvas, x, py + 22, lbl + (f" {detail}" if detail and leg["state"] == "quarantined" else ""), col)
            _txt(canvas, _trunc(leg["model"], 28), x, py + 54, 11, FAINT)
            if i < len(legs) - 1:
                ax = x + slot
                _txt(canvas, "→", int(ax) - 12, py + 26, 16, FAINT)
            x += slot
    # bottom: sensors | controls
    by = CONTENT_Y + 166
    sy = _card(canvas, 28, by, 620, CONTENT_BOT, "SENSORS")
    stats = _sys_stats()
    if stats is None:
        _txt(canvas, "psutil not installed — pip install psutil", 46, sy, 12, FAINT)
    else:
        gauges = [("CPU", stats["cpu"]), ("RAM", stats["mem"]), ("DISK", stats["disk"])]
        gp = _gpu_pct()
        if gp is not None:
            gauges.append(("GPU", gp))
        gx = 130
        for label, val in gauges:
            frac = max(0.0, min(1.0, val / 100.0))
            col = ERR if val >= 90 else (WARN if val >= 70 else OK)
            _arc(canvas, gx, sy + 40, 44, frac, color=col)
            v = f"{val:.0f}%"
            _txt(canvas, v, gx - _tw(v, 20, True) // 2, sy + 28, 20, TEXT, True)
            _txt(canvas, label, gx - _tw(label, 11, True) // 2, sy + 96, 11, FAINT, True)
            gx += 130
    # camera
    cam_y = sy + 130
    _txt(canvas, "CAMERA", 46, cam_y, 11, FAINT, True)
    _txt(canvas, "● live" if getattr(_vision, "LATEST_CAMERA_FRAME", None) is not None else "off",
         130, cam_y, 11, OK if getattr(_vision, "LATEST_CAMERA_FRAME", None) is not None else FAINT, True)
    frame = getattr(_vision, "LATEST_CAMERA_FRAME", None)
    if frame is not None and cam_y + 190 < CONTENT_BOT:
        try:
            thumb = cv2.resize(frame, (200, 150))
            img = Image.fromarray(cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB))
            mask = Image.new("L", (200, 150), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, 199, 149], radius=8, fill=255)
            rgba = np.asarray(img.convert("RGBA"))
            rgba[..., 3] = np.asarray(mask)
            _paste(canvas, rgba, 46, cam_y + 24)
        except Exception:
            _txt(canvas, "camera unavailable", 46, cam_y + 24, 12, FAINT)
    # controls
    cy = _card(canvas, 636, by, W - 28, CONTENT_BOT, "CONTROLS")
    bw, bh, gap = 180, 64, 16
    xx, yy = 654, cy
    for name, label in _CONTROLS:
        if xx + bw > W - 28:
            xx = 654
            yy += bh + gap
        if yy + bh > CONTENT_BOT - 8:
            break
        active = _ctl_active(name)
        _rrect(canvas, xx, yy, xx + bw, yy + bh, 10, fill=PANEL2,
               outline=ACCENT if active else BORDER, width=2 if active else 1)
        _txt(canvas, label, xx + 16, yy + 12, 12, TEXT, True)
        sub = _ctl_sub(name)
        if sub:
            scol = OK if active else (ERR if sub in ("TRIPPED", "PAUSED", "OFF") else WARN)
            _pill(canvas, xx + 16, yy + 32, sub, scol)
        _CLICKS.append((xx, yy, xx + bw, yy + bh, "ctl", name))
        xx += bw + gap
    if SHOW_SCRIPTS:
        sy2 = yy + bh + 20
        _txt(canvas, "Pick a script to run  (output goes to Tasks / Log):",
             654, sy2, 12, DIM, True)
        try:
            files = sorted(f for f in os.listdir(_scripts_dir()) if f.endswith(".py"))
        except Exception:
            files = []
        if not files:
            _txt(canvas, f"No .py files yet — drop scripts in {_scripts_dir()}",
                 654, sy2 + 26, 12, FAINT)
        for i, f in enumerate(files[:8]):
            fyy = sy2 + 28 + i * 24
            _txt(canvas, "> " + f, 654, fyy, 13, INFO)
            _CLICKS.append((654, fyy - 4, 1074, fyy + 18, "script", f))


# ---------------------------------------------------------------- DAY tab
def _draw_email_reader(canvas) -> None:
    """Overlay: full email with LISTEN and SUMMARIZE actions."""
    global SELECTED_EMAIL
    items, _ = _inbox_items()
    meta = next((it for it in items if it.get("uid") == SELECTED_EMAIL), None)
    sender = (meta or {}).get("sender", "?")
    subject = (meta or {}).get("subject", "(no subject)")
    uid = SELECTED_EMAIL or ""
    x0, x1 = 180, W - 180
    y0, y1 = CONTENT_Y + 20, CONTENT_BOT - 20
    ry = _card(canvas, x0, y0, x1, y1, "EMAIL")
    _txt(canvas, _trunc(sender, 60), x0 + 18, ry, 14, TEXT, True)
    _txt(canvas, _trunc(subject, 70), x0 + 18, ry + 24, 13, INFO, True)
    # action buttons
    bx = x0 + 18
    bw = _pill(canvas, bx, ry + 52, "LISTEN", OK)
    _CLICKS.append((bx, ry + 52, bx + bw, ry + 76, "emaillisten", uid))
    bx += bw + 10
    if uid in _EMAIL_SUMMARIZING:
        _pill(canvas, bx, ry + 52, "… SUMMARIZING", WARN)
    else:
        bw2 = _pill(canvas, bx, ry + 52, "SUMMARIZE", ACCENT)
        _CLICKS.append((bx, ry + 52, bx + bw2, ry + 76, "emailsummarize", uid))
        bx += bw2 + 10
    bw3 = _pill_outline(canvas, x1 - 110, ry + 52, "CLOSE", DIM)
    _CLICKS.append((x1 - 110, ry + 52, x1 - 110 + bw3, ry + 76, "emailclose", None))
    # summary (if any)
    yy = ry + 92
    summary = _EMAIL_SUMMARIES.get(uid)
    if summary:
        _txt(canvas, "SUMMARY", x0 + 18, yy, 11, WARN, True)
        yy += 20
        for line in _wrap_lines(summary, 92)[:4]:
            _txt(canvas, line, x0 + 18, yy, 13, TEXT)
            yy += 20
        yy += 8
        cv2.line(canvas, (x0 + 18, yy), (x1 - 18, yy), BORDER_SOFT, 1)
        yy += 12
    # body
    _txt(canvas, "MESSAGE", x0 + 18, yy, 11, FAINT, True)
    yy += 20
    body = _email_body(uid)
    width = max(20, int((x1 - x0 - 36) / 7.8))
    for line in _wrap_lines(body, width)[:22]:
        if yy > y1 - 24:
            break
        _txt(canvas, line, x0 + 18, yy, 13, TEXT)
        yy += 20


def _draw_day(canvas) -> None:
    # dashboard left (2/3), notes right (1/3)
    dash_x1 = 830
    now = datetime.now()
    dy = _card(canvas, 28, CONTENT_Y, dash_x1, CONTENT_BOT,
               "TODAY  ·  " + now.strftime("%A %b ") + str(now.day))
    try:
        dash = _ops.get_dashboard()
        attn = _ops.compute_attention(dash, datetime.now())
        if attn:
            _txt(canvas, "ATTENTION", 46, dy, 11, WARN, True)
            for j, a in enumerate(attn[:3]):
                _txt(canvas, "· " + _trunc(a, 70), 46, dy + 22 + j * 20, 12, TEXT)
            ay = dy + 22 + min(3, len(attn)) * 20 + 10
        else:
            _txt(canvas, "All clear.", 46, dy, 12, OK, True)
            ay = dy + 30
        cv2.line(canvas, (46, ay), (dash_x1 - 18, ay), BORDER_SOFT, 1)
        ay += 14
        # schedule timeline
        sched = ((dash.get("schedule") or {}).get("data") or {})
        events = sched.get("events", []) if sched.get("connected") else []
        _txt(canvas, "SCHEDULE", 46, ay, 11, FAINT, True)
        ay += 24
        shown = 0
        for e in events[:8]:
            st = str(e.get("start", ""))[:16].replace("T", " ")
            _txt(canvas, "●", 46, ay, 12, INFO)
            _txt(canvas, st, 62, ay, 12, TEXT, True)
            _txt(canvas, _trunc(e.get("summary", ""), 52), 190, ay, 12, DIM)
            ay += 26
            shown += 1
            if ay > CONTENT_BOT - 60:
                break
        if not shown:
            _txt(canvas, "nothing scheduled" if sched.get("connected") else "calendar not connected",
                 46, ay, 12, FAINT)
            ay += 26
        # tasks
        if ay < CONTENT_BOT - 140:
            cv2.line(canvas, (46, ay), (dash_x1 - 18, ay), BORDER_SOFT, 1)
            ay += 14
            _txt(canvas, "TASKS", 46, ay, 11, FAINT, True)
            ay += 24
            for r in _task_rows()[:5]:
                col, lbl = _task_pill(r["status"])
                _pill(canvas, 46, ay - 3, lbl, col)
                _txt(canvas, _trunc(r["desc"], 48), 140, ay, 12, TEXT)
                ay += 28
                if ay > CONTENT_BOT - 140:
                    break
        # inbox — recent important emails
        if ay < CONTENT_BOT - 60:
            cv2.line(canvas, (46, ay), (dash_x1 - 18, ay), BORDER_SOFT, 1)
            ay += 14
            _txt(canvas, "INBOX", 46, ay, 11, FAINT, True)
            ay += 24
            items, connected = _inbox_items()
            if not connected:
                _txt(canvas, "gmail not connected", 46, ay, 12, FAINT)
            elif not items:
                _txt(canvas, "no unread mail", 46, ay, 12, FAINT)
            else:
                for it in items[:5]:
                    if ay > CONTENT_BOT - 40:
                        break
                    uid = it.get("uid", "")
                    _txt(canvas, _trunc(it.get("sender", "?"), 22),
                         46, ay, 12, WARN if it.get("important") else TEXT, True)
                    _txt(canvas, _trunc(it.get("subject", "(no subject)"), 44),
                         260, ay, 12, DIM)
                    if SELECTED_EMAIL == uid:
                        cv2.rectangle(canvas, (40, ay - 4), (dash_x1 - 24, ay + 18),
                                      ACCENT_DIM, -1)
                    _CLICKS.append((40, ay - 4, dash_x1 - 24, ay + 18, "email", uid))
                    ay += 26
    except Exception:
        _txt(canvas, "dashboard unavailable", 46, dy, 13, FAINT)
    # notes card
    nx0 = dash_x1 + 16
    ny = _card(canvas, nx0, CONTENT_Y, W - 28, CONTENT_BOT,
               "NOTES  ·  ops.md" + ("  ·  EDITING" if NOTES_FOCUS else ""))
    _txt(canvas, "[T] type   ·   autosaves", nx0 + 18, CONTENT_BOT - 30, 11, FAINT)
    lines = NOTES_BUF.splitlines() or ["(empty — press T to type)"]
    row_h, top = 21, ny
    vis = (CONTENT_BOT - 44 - top) // row_h
    max_scroll = max(0, len(lines) - vis)
    if SCROLL["day"] > max_scroll:
        SCROLL["day"] = max_scroll
    start = SCROLL["day"]
    for i, ln in enumerate(lines[start:start + vis]):
        _txt(canvas, _trunc(ln, 52), nx0 + 18, top + i * row_h, 13, TEXT)
    if NOTES_FOCUS and int(time.time() * 2) % 2 == 0:
        # caret at end of last visible line
        vis_lines = lines[start:start + vis]
        last = vis_lines[-1] if vis_lines else ""
        cx = nx0 + 18 + _tw(_trunc(last, 52), 13)
        cyy = top + (len(vis_lines) - 1) * row_h
        cv2.line(canvas, (cx, cyy), (cx, cyy + 16), TEXT, 1)


# ---------------------------------------------------------------- main draw
def draw(canvas, ACC, ACC2, day_draw_fn: Callable) -> None:
    """Draw the whole OPS overlay. day_draw_fn(canvas, ACC, ACC2) draws the
    existing Day dashboard (callback avoids a hud<->ops_screen import cycle)."""
    _CLICKS.clear()
    canvas[:, :] = BG
    if ACTIVE_TAB == "dash":
        _draw_dash(canvas)
    elif ACTIVE_TAB == "log":
        _draw_log(canvas)
    elif ACTIVE_TAB == "system":
        _draw_system(canvas)
    elif ACTIVE_TAB == "day":
        _draw_day(canvas)
        if SELECTED_EMAIL:
            _draw_email_reader(canvas)
    _draw_tab_bar(canvas)
    _draw_status(canvas)


# ---------------------------------------------------------------- input
def handle_click(x: int, y: int) -> bool:
    global SELECTED_EVENT, SELECTED_TASK, SELECTED_EMAIL
    for (x0, y0, x1, y1, kind, arg) in _CLICKS:
        if x0 <= x <= x1 and y0 <= y <= y1:
            if kind == "tab":
                set_tab(TABS.index(arg))
            elif kind == "filter":
                global LOG_FILTER
                LOG_FILTER = arg
                SCROLL["log"] = 0
                save_state()
            elif kind == "searchbox":
                focus_search()
                from aria import hud as _hud
                _hud.TYPING_ACTIVE = True
            elif kind == "logrow":
                SELECTED_EVENT = None if SELECTED_EVENT == ("log", arg) else ("log", arg)
            elif kind == "task":
                SELECTED_TASK = arg if SELECTED_TASK != arg else None
            elif kind == "jobcancel":
                _cancel_job(arg)
            elif kind == "email":
                SELECTED_EMAIL = None if SELECTED_EMAIL == arg else arg
            elif kind == "emailclose":
                SELECTED_EMAIL = None
            elif kind == "emaillisten":
                items, _ = _inbox_items()
                meta = next((it for it in items if it.get("uid") == arg), {})
                _listen_email(arg, meta.get("sender", "?"), meta.get("subject", ""))
            elif kind == "emailsummarize":
                items, _ = _inbox_items()
                meta = next((it for it in items if it.get("uid") == arg), {})
                _summarize_email(arg, meta.get("sender", "?"), meta.get("subject", ""))
            elif kind == "ctl":
                _ctl_action(arg)
            elif kind == "script":
                _run_script_file(arg)
            return True
    return False
