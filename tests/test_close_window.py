"""Pass 28 #36: tool_close_window must not close an ambiguous target
and must not claim success when the window stays open.

Bug: _find_window returned the FIRST window whose title contained
the query, so "Chrome" closed whichever Chrome window happened to
be first; and tool_close_window returned "Closed: ..." after
w.close() without checking — an app showing an unsaved-changes
dialog stays open while the result claims it closed.

Fix contract:
- an exact (case-insensitive) title match wins over substring
  matches; otherwise a unique substring match closes; several
  substring matches refuse with a typed failure listing them, and
  no window is closed;
- after w.close() the window list is re-polled: the window must
  actually be gone, or the result is a typed failure saying it
  is still open (it may be waiting on unsaved changes).
"""
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg

from aria.outcome import normalize  # noqa: E402
from aria.tools import builtins  # noqa: E402


class _FakeWindow:
    def __init__(self, title, gw, closes=True):
        self.title = title
        self._gw = gw
        self.closes = closes
        self.close_called = False

    def close(self):
        self.close_called = True
        if self.closes:
            self._gw.windows.remove(self)


class _FakeGW:
    def __init__(self, windows):
        self.windows = list(windows)

    def getAllWindows(self):
        return list(self.windows)


def _make_gw(monkeypatch, titles, closes=None):
    gw = _FakeGW([])
    closes = closes or {}
    for t in titles:
        gw.windows.append(_FakeWindow(t, gw, closes=closes.get(t, True)))
    monkeypatch.setattr(builtins, "_gw", lambda: gw)
    monkeypatch.setattr(builtins, "_CLOSE_POLL_SECONDS", 0)
    return gw


def test_close_unique_substring_match_still_works(monkeypatch):
    gw = _make_gw(monkeypatch, ["Google Chrome"])
    result = builtins.tool_close_window("Chrome")
    assert normalize(result).status == "done", result
    assert "Closed: Google Chrome" in result
    assert gw.windows == []


def test_close_exact_match_wins_over_substring(monkeypatch):
    gw = _make_gw(monkeypatch,
                   ["Untitled - Notepad", "Untitled - Notepad (2)"])
    first, second = gw.windows
    result = builtins.tool_close_window("Untitled - Notepad")
    assert normalize(result).status == "done", result
    assert first.close_called
    assert not second.close_called
    assert gw.windows == [second]


def test_close_ambiguous_refuses_and_closes_nothing(monkeypatch):
    gw = _make_gw(monkeypatch, ["Doc - Notepad", "Doc2 - Notepad"])
    result = builtins.tool_close_window("Doc")
    assert normalize(result).status == "failed", result
    assert "Doc - Notepad" in result and "Doc2 - Notepad" in result
    assert all(not w.close_called for w in gw.windows)
    assert len(gw.windows) == 2


def test_close_still_open_is_a_failure(monkeypatch):
    # The app shows an unsaved-changes dialog: close() runs but the
    # window stays in the list — the old code reported "Closed".
    gw = _make_gw(monkeypatch, ["Report - Notepad"],
                   closes={"Report - Notepad": False})
    result = builtins.tool_close_window("Report")
    assert normalize(result).status == "failed", result
    assert "still open" in result
    assert len(gw.windows) == 1


def test_close_no_match_unchanged(monkeypatch):
    _make_gw(monkeypatch, ["Google Chrome"])
    result = builtins.tool_close_window("Calculator")
    assert normalize(result).status == "failed", result
    assert "No window matching" in result


def test_close_without_pygetwindow(monkeypatch):
    monkeypatch.setattr(builtins, "_gw", lambda: None)
    result = builtins.tool_close_window("Chrome")
    assert normalize(result).status == "failed", result
    assert "pygetwindow" in result
