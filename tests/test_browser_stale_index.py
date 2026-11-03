"""Stale browser element indices (ChatGPT x ARIA loop, 2026-11-02).

read() caches one label per element position, but the page can
change after the read: the control at a cached position can be
replaced by a different, irreversible control. The guard must
judge the LIVE label at click time, and an approval confirmed
for one label must not execute once that label has changed.
The real BrowserSession is used here; only the Playwright page
is faked, so _resolve's live re-read is the code under test."""

import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "aria")


def _pkg(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    sys.modules[name] = m
    return m


_pkg("aria", PKG)
_pkg("aria.tools", os.path.join(PKG, "tools"))
_pkg("aria.agent", os.path.join(PKG, "agent"))
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
sys.modules["aria.speech"] = types.ModuleType("aria.speech")

ba = importlib.import_module("aria.browser_agent")


class FakeElement:
    def __init__(self, page, pos):
        self._page = page
        self._pos = pos

    def is_visible(self):
        return True

    def inner_text(self, timeout=None):
        return self._page.elements[self._pos][1]

    def get_attribute(self, name):
        return None

    def evaluate(self, script):
        return self._page.elements[self._pos][0]

    def click(self):
        self._page.clicks.append(self._pos)


class FakeLocator:
    def __init__(self, page):
        self._page = page

    def count(self):
        return len(self._page.elements)

    def nth(self, i):
        return FakeElement(self._page, i)


class FakePage:
    def __init__(self, elements):
        # elements: live list of (tag, label); tests mutate it
        # in place to simulate the page changing after read().
        self.elements = list(elements)
        self.clicks = []
        self.url = "https://shop.example/"

    def title(self):
        return "Fake shop"

    def inner_text(self, selector):
        return "page text"

    def locator(self, selector):
        return FakeLocator(self)

    def wait_for_timeout(self, ms):
        pass


def _install_fake_page(elements):
    page = FakePage(elements)
    session = ba.BrowserSession()
    session.page = page
    runner = ba._Runner(session_factory=lambda: session)
    ba.set_runner_for_tests(runner)
    runner.session = session
    return page


def _fake_approval(replay_pred):
    stashed = []
    mod = types.ModuleType("aria.approval")
    mod.request_approval = lambda tool, args, desc=None: \
        stashed.append((tool, args, desc)) or \
        "[AWAITING_APPROVAL token=t9] confirm?"
    mod.in_approve_replay = replay_pred
    mod.get_mode = lambda: "confirm-risky"
    sys.modules["aria.approval"] = mod
    return stashed


def _open_and_read(page):
    out = ba.tool_browser_agent({"action": "read"})
    assert "[0]" in out


def test_stale_index_click_parks_under_live_label():
    page = _install_fake_page([("button", "Continue shopping"),
                               ("button", "Search")])
    stashed = _fake_approval(lambda tool, args: False)
    _open_and_read(page)
    # The page changes after read(): position 0 is now an
    # irreversible control, while the cached label is benign.
    page.elements[0] = ("button", "Delete account")
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert page.clicks == []                      # never clicked
    assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 1
    assert stashed[0][1] == {"action": "click", "target": "index:0",
                             "label": "Delete account"}
    assert "Delete account" in stashed[0][2]
    print("ok stale_index_parks_live_label")


def test_unchanged_index_click_executes():
    page = _install_fake_page([("button", "Continue shopping"),
                               ("button", "Search")])
    _fake_approval(lambda tool, args: False)
    _open_and_read(page)
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert page.clicks == [0]
    assert "Clicked 'Continue shopping'" in out
    print("ok unchanged_index_executes")


def test_approved_click_does_not_execute_after_label_change():
    page = _install_fake_page([("button", "Place order"),
                               ("button", "Search")])
    stashed = _fake_approval(lambda tool, args: False)
    _open_and_read(page)
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert page.clicks == [] and "AWAITING_APPROVAL" in out
    assert stashed[0][1]["label"] == "Place order"
    # The user confirmed 'Place order'; the page then changes.
    page.elements[0] = ("button", "Delete account")
    mod = sys.modules["aria.approval"]
    mod.in_approve_replay = lambda tool, args: \
        tool == "browser_agent" and args == {
            "action": "click", "target": "index:0",
            "label": "Place order"}
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert page.clicks == []          # confirmed label no longer there
    assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 2
    assert stashed[1][1]["label"] == "Delete account"
    print("ok approved_click_blocked_after_change")


def test_approved_click_executes_when_label_unchanged():
    page = _install_fake_page([("button", "Place order"),
                               ("button", "Search")])
    stashed = _fake_approval(lambda tool, args: False)
    _open_and_read(page)
    ba.tool_browser_agent({"action": "click", "target": "index:0"})
    mod = sys.modules["aria.approval"]
    mod.in_approve_replay = lambda tool, args: \
        tool == "browser_agent" and args == {
            "action": "click", "target": "index:0",
            "label": "Place order"}
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert page.clicks == [0]
    assert "Clicked 'Place order'" in out
    assert len(stashed) == 1          # exactly once, no re-stash
    print("ok approved_click_executes_unchanged")


def test_removed_element_click_fails_without_clicking():
    page = _install_fake_page([("button", "Continue shopping")])
    _fake_approval(lambda tool, args: False)
    _open_and_read(page)
    page.elements = []                # the element is gone
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert page.clicks == []
    assert "stale" in out
    print("ok removed_element_no_click")
