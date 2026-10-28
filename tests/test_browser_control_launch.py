"""Regression tests (2026-10-28, ChatGPT x ARIA loop finding #1):
browser_control go_to used to hand its argument straight to
tool_open_app_or_url, which os.startfile()s ANY target — so a tool in
neither SAFE_MODE_BLOCKED_TOOLS nor DESTRUCTIVE_TOOLS launched local
programs (.cmd / .exe / UNC paths) with no safe-mode block and no
approval, including code dev_agent had just saved.

Fix contract pinned here, at the handler level (the dispatcher-level
proof lives in tests/test_dispatch_browser_control.py):
- only absolute http(s) URLs with a hostname reach the launcher;
  every local path, UNC path, and non-web scheme fails BEFORE the
  dispatch helper is called (zero launch attempts);
- a successful launch is reported unverified, as before;
- safe-mode / held / duplicate / argument-error results from the
  dispatcher pass through UNCHANGED, so the AWAITING_APPROVAL token
  text the user taps on is never mangled.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import types

import pytest

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg

gap = importlib.import_module("aria.tools.gaptools")


class _DispatchFake:
    def __init__(self, result="Opened URL: https://example.com"):
        self.calls = []
        self.result = result

    def __call__(self, url):
        self.calls.append(url)
        return self.result, False


@pytest.fixture
def dispatch_fake(monkeypatch):
    fake = _DispatchFake()
    monkeypatch.setattr(gap, "_open_url_via_dispatch", fake)
    return fake


BAD_URLS = [
    "C:\\evil.cmd",
    "C:/evil.cmd",
    "evil.exe",
    "\\\\server\\share\\evil.cmd",
    "file:///C:/evil.cmd",
    "steam://validate/222",
    "javascript:alert(1)",
    "ftp://example.com/x",
    "example.com/page",
    "//example.com/page",
    "https://",
    "http://",
    "https:///path",
]


@pytest.mark.parametrize("bad", BAD_URLS)
def test_go_to_refuses_non_web_targets_before_any_launch(
        dispatch_fake, bad):
    out = gap.tool_browser_control({"action": "go_to", "url": bad})
    assert out.startswith("status=failed"), out
    assert dispatch_fake.calls == [], \
        f"refused target still reached the launcher: {dispatch_fake.calls}"


def test_go_to_allows_http_and_https_with_hostname(dispatch_fake):
    for url in ("https://example.com",
                "http://example.com/page?q=1",
                "HTTPS://Example.COM/path"):
        out = gap.tool_browser_control({"action": "go_to", "url": url})
        assert out.startswith("status=unverified"), out
    assert dispatch_fake.calls == ["https://example.com",
                                   "http://example.com/page?q=1",
                                   "HTTPS://Example.COM/path"]


def test_go_to_passes_held_and_blocked_results_through_unchanged(
        monkeypatch):
    for held in ("[AWAITING_APPROVAL token=abcd1234] open the URL. "
                 "Tell the user what you are about to do and wait.",
                 "[Safe mode: 'open_app_or_url' is disabled. "
                 "Disable safe mode to use shell, GUI control, or email.]",
                 "[Duplicate call blocked: open_app_or_url already ran "
                 "with these arguments this turn. Earlier result: x]",
                 "[Argument error: open_app_or_url requires target. "
                 "Call it again with it included.]"):
        monkeypatch.setattr(
            gap, "_open_url_via_dispatch",
            lambda url, _held=held: (_held, False))
        out = gap.tool_browser_control({"action": "go_to",
                                        "url": "https://example.com"})
        assert out == held, out
