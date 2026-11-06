"""CSS-selected clicks must be judged by the control's real label
(ChatGPT x ARIA loop, pass 20).

_resolve()'s CSS branch used to read inner_text() only. A submit
input carries its label in its value attribute — inner_text() on
an <input> is empty — so clicking '#delete' on
<input value="Delete account"> produced an empty guard label and
_guarded_click waved it straight through, while the index and
text= branches already chained value / aria-label / placeholder.
The CSS branch now uses the same chain (via _element_label), so
attribute-labelled irreversible controls park for approval like
every other branch, benign attribute-labelled controls still
click through, and a confirmed replay still executes exactly once.
The real BrowserSession is used; only the Playwright page is faked.
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
        el = self._page.elements[self._pos]
        if el.get("inner_raises"):
            raise RuntimeError("inner_text unavailable")
        return el.get("text") or ""

    def get_attribute(self, name):
        el = self._page.elements[self._pos]
        if el.get("attr_raises"):
            raise RuntimeError("attribute unavailable")
        return el.get({"value": "value", "aria-label": "aria_label",
                       "placeholder": "placeholder"}.get(name, name))

    def evaluate(self, script):
        return self._page.elements[self._pos]["tag"]

    def click(self):
        self._page.clicks.append(self._pos)


class FakeUnionLocator:
    def __init__(self, page):
        self._page = page

    def count(self):
        return len(self._page.elements)

    def nth(self, i):
        return FakeElement(self._page, i)


class FakeCSSLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return FakeElement(self._page,
                           self._page.selectors[self._selector])


class FakePage:
    def __init__(self, elements, selectors):
        self.elements = list(elements)
        self.selectors = dict(selectors)
        self.clicks = []
        self.url = "https://shop.example/"

    def title(self):
        return "Fake shop"

    def inner_text(self, selector):
        return "page text"

    def locator(self, selector):
        if selector == ba._UNION:
            return FakeUnionLocator(self)
        return FakeCSSLocator(self, selector)

    def wait_for_timeout(self, ms):
        pass


def _install_fake_page(elements, selectors):
    page = FakePage(elements, selectors)
    session = ba.BrowserSession()
    session.page = page
    runner = ba._Runner(session_factory=lambda: session)
    ba.set_runner_for_tests(runner)
    runner.session = session
    return page


def _fake_approval(replay_pred=None):
    stashed = []
    mod = types.ModuleType("aria.approval")
    mod.request_approval = lambda tool, args, desc=None: \
        stashed.append((tool, args, desc)) or \
        "[AWAITING_APPROVAL token=t9] confirm?"
    mod.in_approve_replay = replay_pred or (lambda tool, args: False)
    mod.get_mode = lambda: "confirm-risky"
    sys.modules["aria.approval"] = mod
    return stashed


def _submit(value=None, aria_label=None, text="", **kw):
    el = {"tag": "input", "text": text, "value": value,
          "aria_label": aria_label, "placeholder": None}
    el.update(kw)
    return el


def test_css_value_label_parks():
    page = _install_fake_page(
        [_submit(value="Delete account")], {"#delete": 0})
    stashed = _fake_approval()
    out = ba.tool_browser_agent({"action": "click", "target": "#delete"})
    assert page.clicks == []  # NOT clicked
    assert len(stashed) == 1
    assert stashed[0][1]["label"] == "Delete account"
    assert "AWAITING_APPROVAL" in out


def test_css_aria_label_parks():
    page = _install_fake_page(
        [_submit(aria_label="Delete account")], {"#delete": 0})
    stashed = _fake_approval()
    ba.tool_browser_agent({"action": "click", "target": "#delete"})
    assert page.clicks == []
    assert len(stashed) == 1
    assert stashed[0][1]["label"] == "Delete account"


def test_css_benign_value_clicks_through():
    page = _install_fake_page(
        [_submit(value="Search")], {"#search": 0})
    stashed = _fake_approval()
    out = ba.tool_browser_agent({"action": "click", "target": "#search"})
    assert page.clicks == [0]
    assert stashed == []
    assert "Clicked 'Search'" in out


def test_css_inner_text_still_wins_when_present():
    page = _install_fake_page(
        [{"tag": "button", "text": "Delete account", "value": None,
          "aria_label": None, "placeholder": None}], {"#btn": 0})
    stashed = _fake_approval()
    ba.tool_browser_agent({"action": "click", "target": "#btn"})
    assert page.clicks == []
    assert stashed[0][1]["label"] == "Delete account"


def test_css_raising_inner_text_does_not_hide_value():
    page = _install_fake_page(
        [_submit(value="Delete account", inner_raises=True)],
        {"#delete": 0})
    stashed = _fake_approval()
    ba.tool_browser_agent({"action": "click", "target": "#delete"})
    assert page.clicks == []
    assert stashed[0][1]["label"] == "Delete account"


def test_css_confirmed_replay_executes_once():
    page = _install_fake_page(
        [_submit(value="Delete account")], {"#delete": 0})
    stashed = _fake_approval()
    ba.tool_browser_agent({"action": "click", "target": "#delete"})
    assert page.clicks == [] and len(stashed) == 1
    mod = sys.modules["aria.approval"]
    mod.in_approve_replay = lambda tool, args: \
        tool == "browser_agent" and args == stashed[0][1]
    out = ba.tool_browser_agent({"action": "click", "target": "#delete"})
    assert page.clicks == [0]
    assert len(stashed) == 1  # no re-stash
    assert "Clicked 'Delete account'" in out
