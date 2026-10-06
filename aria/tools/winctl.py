"""
ARIA window/app control expansion (Module 3 — ARIA ULTIMATE).
Existing list_windows/focus_window/minimize_window/close_window stay in
aria.tools.builtins — do NOT duplicate them here.

Imports: stdlib + aria.config only (no dispatch cycles). All win32 work is
lazy inside functions; on non-Windows platforms the tools degrade gracefully
and NEVER crash.
"""

import fnmatch
import os
import sys
from typing import Optional

from aria.config import add_log, redact
from aria import config as _config_silent

_UNAVAILABLE = "[window control unavailable on this platform]"
_SNAP_POSITIONS = ("left", "right", "maximize", "minimize", "center")


def _is_windows() -> bool:
    return sys.platform == "win32"


def _win32():
    """Lazy pywin32 import. Returns (win32gui, win32con) or None."""
    try:
        import win32gui
        import win32con
        return win32gui, win32con
    except Exception:
        return None


def _find_hwnd(win32gui, title: str) -> Optional[int]:
    """Find the first visible top-level window whose title contains `title`
    (case-insensitive)."""
    needle = (title or "").strip().lower()
    found = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            text = win32gui.GetWindowText(hwnd) or ""
            if text and (not needle or needle in text.lower()):
                found.append((hwnd, text))
        except Exception as _e_silent:
            _config_silent.log_silent("_cb", _e_silent)
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return None
    return found[0][0] if found else None


def tool_window_snap(title: str, position: str) -> str:
    """Snap/move a window: position in {left, right, maximize, minimize, center}.
    Windows via lazy pywin32; elsewhere returns the platform-unavailable message.
    Graceful — never raises."""
    try:
        pos = (position or "").strip().lower()
        if pos in ("fullscreen", "fullscreen-ish"):
            pos = "maximize"
        if pos not in _SNAP_POSITIONS:
            return f"[Invalid position '{position}'. Choose one of: {', '.join(_SNAP_POSITIONS)}]"

        if not _is_windows():
            return _UNAVAILABLE
        w32 = _win32()
        if not w32:
            return _UNAVAILABLE
        win32gui, win32con = w32

        hwnd = _find_hwnd(win32gui, title)
        if hwnd is None:
            return f"[No window matching '{title}']"

        try:
            import win32api
            sw, sh = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
        except Exception:
            sw, sh = 1920, 1080

        add_log(f"window_snap: {redact(title)} -> {pos}")
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            if pos == "maximize":
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                return f"Maximized: {title}"
            if pos == "minimize":
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                return f"Minimized: {title}"
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            w, h = right - left, bottom - top
            if pos == "left":
                win32gui.SetWindowPos(hwnd, 0, 0, 0, sw // 2, sh, 0)
            elif pos == "right":
                win32gui.SetWindowPos(hwnd, 0, sw // 2, 0, sw // 2, sh, 0)
            elif pos == "center":
                win32gui.SetWindowPos(hwnd, 0, (sw - w) // 2, (sh - h) // 2, w, h, 0)
            return f"Snapped '{title}' to {pos}."
        except Exception as e:
            return f"[Snap failed: {e}]"
    except Exception as e:
        return f"[window_snap error: {e}]"


_START_MENU_DIRS = [
    r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
]


def _resolve_start_menu(name: str) -> Optional[str]:
    """Find a Start Menu shortcut matching `name` (case-insensitive substring)."""
    dirs = list(_START_MENU_DIRS)
    try:
        dirs.append(os.path.join(os.path.expanduser("~"),
                                 r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs"))
    except Exception as _e_silent:
        _config_silent.log_silent("_resolve_start_menu", _e_silent)
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for base in dirs:
        if not os.path.isdir(base):
            continue
        try:
            for root, _ds, files in os.walk(base):
                for f in files:
                    if f.lower().endswith((".lnk", ".exe")) and needle in f.lower():
                        return os.path.join(root, f)
        except Exception:
            continue
    return None


def tool_launch_app(name: str) -> str:
    """Friendly wrapper for launching installed Windows apps.
    Resolves Start Menu names (via os.startfile on common paths); if nothing
    matches, tells the model to use open_app_or_url instead. No-ops gracefully
    on Linux — never crashes."""
    try:
        app = (name or "").strip()
        if not app:
            return "[launch_app: provide an app name]"
        if not _is_windows() or not hasattr(os, "startfile"):
            return ("[launch_app is Windows-only on this platform — "
                    "use open_app_or_url to open the app or URL instead.]")
        add_log(f"launch_app: {redact(app)}")
        target = None
        try:
            if os.path.exists(app):
                target = app
            else:
                target = _resolve_start_menu(app)
        except Exception as e:
            return f"[launch_app resolution failed: {e} — use open_app_or_url instead.]"
        if not target:
            return (f"[Could not find '{app}' in the Start Menu. "
                    "Use open_app_or_url with the full path or URL instead.]")
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return f"Launched: {target}"
        except Exception as e:
            return f"[Launch failed: {e} — use open_app_or_url instead.]"
    except Exception as e:
        return f"[launch_app error: {e}]"
