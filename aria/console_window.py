"""
aria/console_window.py — show/hide ARIA's own console window.

Alek (2026-10-27): ARIA is launched with console python on
purpose (see desktop_shortcut.py), so a command window opens
with her and sits on the desktop. He doesn't need it open —
hide it by default, with a Settings -> General toggle to bring
it back when he wants the live log output.

How: the Win32 console attached to THIS process —
GetConsoleWindow + ShowWindow. Hiding is cosmetic: the process,
the dashboard, and the log prints all keep running; nothing is
closed. If she was launched windowless (pythonw) there is no
console to hide, and asking to SHOW one allocates a fresh
console so the toggle is never dead.

Setting: 'show_console' (default False = hidden). Applied at
the top of aria.main.main() and live from the Settings toggle.
Windows-only; everywhere else every call is a no-op.
"""

from __future__ import annotations

import sys

SW_HIDE = 0
SW_SHOW = 5


def _win32():
    import ctypes
    return ctypes.windll.kernel32, ctypes.windll.user32


def console_hwnd() -> int:
    """The console window handle of this process, or 0."""
    if sys.platform != "win32":
        return 0
    try:
        kernel32, _user32 = _win32()
        return int(kernel32.GetConsoleWindow() or 0)
    except Exception:
        return 0


def set_console_visible(visible: bool) -> bool:
    """Show or hide this process's console window. Returns True
    when a window was actually shown or hidden."""
    if sys.platform != "win32":
        return False
    try:
        kernel32, user32 = _win32()
        hwnd = int(kernel32.GetConsoleWindow() or 0)
        if not hwnd and visible:
            # Windowless launch: allocate a console so 'show'
            # still does something.
            try:
                kernel32.AllocConsole()
                hwnd = int(kernel32.GetConsoleWindow() or 0)
            except Exception:
                hwnd = 0
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, SW_SHOW if visible else SW_HIDE)
        return True
    except Exception:
        return False


def apply_from_settings() -> bool:
    """Startup application of the 'show_console' setting:
    hidden unless the setting says show. Returns True when the
    console was hidden by this call."""
    try:
        from aria import config as _config_cw
        show = bool(_config_cw.get_setting("show_console", False))
    except Exception:
        show = False
    if show:
        return False  # a console that should show is already up
    return set_console_visible(False)
