"""Browser text= targets must be judged by the matched
element's real label (pass-15 runner-up, closed in pass 18).

_resolve('text=...') used to return the QUERY STRING as the
label while Playwright matched with exact=False (substring).
The guarded click therefore judged a click by what the model
searched for, not by what it was about to click: 'text=account'
matched a 'Delete account' control, reported the label
'account' (not irreversible), and clicked straight through the
exact approval the guard exists to require. Now the text=
branch reads the matched element's live text with the same
extraction chain as read()/index: and the guard sees the real
label. The real BrowserSession is used; only the page is faked.
"""

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


class FakeTextLocator:
    """Playwright's get_by_text(exact=False): substring match,
    .first is the first matching element."""

    def __init__(self, page, query):
        self._page = page
        self._query = query

    @property
    def first(self):
        for i, (_tag, label) in enumerate(self._page.elements):
            if self._query.lower() in label.lower():
                return FakeElement(self._page, i)
        raise RuntimeError("no element matches the text query")


class FakePage:
    def __init__(self, elements):
        self.elements = list(elements)
        self.clicks = []
        self.url = "https://shop.example/"

    def title(self):
        return "Fake shop"

    def inner_text(self, selector):
        return "page text"

    def locator(self, selector):
        return FakeLocator(self)

    def get_by_text(self, text, exact=True):
        return FakeTextLocator(self, text)

    def wait_for_timeout(self, ms):
        pass


def _install_fake_page(elements):
    page = FakePage(elements)
    session = ba.BrowserSession()
    session.page = page
    runner = ba._Runner(session_factory=lambda: session)
    ba.set_runner_for_tests(runner)
    runner.session = session
    return page, session


def _fake_approval():
    stashed = []
    mod = types.ModuleType("aria.approval")
    mod.request_approval = lambda tool, args, desc=None: \
        stashed.append((tool, args, desc)) or \
        "[AWAITING_APPROVAL token=<redacted>"
    mod.in_approve_replay = lambda tool, args: False
    mod.get_mode = lambda: "confirm-risky"
    sys.modules["aria.approval"] = mod
    return stashed


def test_resolve_text_returns_the_matched_elements_real_label():
    _page, session = _install_fake_page([("button", "Delete account")])
    _loc, label = session._resolve("text=account")
    assert label == "Delete account"


def test_benign_query_matching_dangerous_control_parks():
    page, _session = _install_fake_page([("button", "Delete account")])
    stashed = _fake_approval()
    out = ba.tool_browser_agent(
        {"action": "click", "target": "text=account"})
    assert page.clicks == []  # NOT clicked
    assert len(stashed) == 1
    _tool, args, _desc = stashed[0]
    assert args["label"] == "Delete account"
    assert "AWAITING_APPROVAL" in out


def test_benign_control_still_clicks_through():
    page, _session = _install_fake_page(
        [("button", "Your account settings")])
    stashed = _fake_approval()
    ba.tool_browser_agent({"action": "click", "target": "text=account"})
    assert page.clicks == [0]  # clicked live, no park
    assert stashed == []
