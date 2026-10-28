"""Console window show/hide (Alek, 2026-10-27): ARIA launches
with console python, so a command window opens with her. It is
hidden by default ('show_console' setting, default False) and
the Settings -> General toggle brings it back.

The Win32 layer is faked end to end: a stand-in kernel32 /
user32 pair records every call. Pins: hide/show issue the right
ShowWindow command on this process's console handle, 'show'
with no console allocates one first, 'hide' with no console is
an honest no-op, non-Windows never touches Win32, and startup
application follows the setting.

Run per-file (own process), like every suite in this repo.
"""
import importlib.util
import sys
import types
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'console_window_test',
    Path(__file__).parents[1] / 'aria' / 'console_window.py')
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)


class _FakeKernel32:
    def __init__(self, hwnd=1234):
        self._hwnd = hwnd
        self.alloc_calls = 0

    def GetConsoleWindow(self):
        return self._hwnd

    def AllocConsole(self):
        self.alloc_calls += 1
        self._hwnd = 777
        return True


class _FakeUser32:
    def __init__(self):
        self.shown = []

    def ShowWindow(self, hwnd, cmd):
        self.shown.append((hwnd, cmd))
        return True


class _Win:
    """Patch cw._win32 + sys.platform for one test."""

    def __init__(self, kernel32):
        self.kernel32 = kernel32
        self.user32 = _FakeUser32()

    def __enter__(self):
        self._old_win32 = cw._win32
        self._old_platform = sys.platform
        cw._win32 = lambda: (self.kernel32, self.user32)
        sys.platform = 'win32'
        return self

    def __exit__(self, *exc):
        cw._win32 = self._old_win32
        sys.platform = self._old_platform
        return False


def test_hide_and_show_use_this_process_console():
    k = _FakeKernel32(hwnd=1234)
    with _Win(k) as w:
        assert cw.console_hwnd() == 1234
        assert cw.set_console_visible(False) is True
        assert w.user32.shown == [(1234, cw.SW_HIDE)]
        assert cw.set_console_visible(True) is True
        assert w.user32.shown[-1] == (1234, cw.SW_SHOW)
        assert k.alloc_calls == 0


def test_show_without_console_allocates_one():
    k = _FakeKernel32(hwnd=0)
    with _Win(k) as w:
        assert cw.set_console_visible(True) is True
        assert k.alloc_calls == 1
        assert w.user32.shown == [(777, cw.SW_SHOW)]


def test_hide_without_console_is_a_noop():
    k = _FakeKernel32(hwnd=0)
    with _Win(k) as w:
        assert cw.set_console_visible(False) is False
        assert k.alloc_calls == 0
        assert w.user32.shown == []


def test_non_windows_never_touches_win32():
    def _boom():
        raise AssertionError('Win32 consulted off Windows')
    old = cw._win32
    cw._win32 = _boom
    try:
        assert sys.platform != 'win32'  # the harness itself
        assert cw.console_hwnd() == 0
        assert cw.set_console_visible(True) is False
        assert cw.set_console_visible(False) is False
    finally:
        cw._win32 = old


def _install_config(show_value):
    aria_mod = types.ModuleType('aria')
    config_mod = types.ModuleType('aria.config')
    config_mod.get_setting = lambda key, default=None: (
        show_value if key == 'show_console' else default)
    aria_mod.config = config_mod
    sys.modules['aria'] = aria_mod
    sys.modules['aria.config'] = config_mod


def _remove_config():
    sys.modules.pop('aria', None)
    sys.modules.pop('aria.config', None)


def test_apply_from_settings_hides_by_default():
    _install_config(False)
    try:
        k = _FakeKernel32(hwnd=1234)
        with _Win(k) as w:
            assert cw.apply_from_settings() is True
            assert w.user32.shown == [(1234, cw.SW_HIDE)]
    finally:
        _remove_config()


def test_apply_from_settings_leaves_shown_console_alone():
    _install_config(True)
    try:
        k = _FakeKernel32(hwnd=1234)
        with _Win(k) as w:
            assert cw.apply_from_settings() is False
            assert w.user32.shown == []
    finally:
        _remove_config()
