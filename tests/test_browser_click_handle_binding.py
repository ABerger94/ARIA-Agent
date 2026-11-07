"""Pass 28 #2: a browser click must land on the node the label was
read from — never on a replacement resolved afterwards.

_resolve read the label through a Playwright Locator and returned
that Locator; click_resolved then called locator.element_handle(),
which re-resolves the selector at click time. A DOM swap between
the two steps clicked the replacement under the old label. The fix
binds the element handle inside _resolve, reads the label from
that handle, and clicks the same handle — a detached handle
refuses instead of clicking.
"""
import importlib
import os
import sys
import types

import pytest

pkg = types.ModuleType("aria")
pkg.__path__ = [os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")]
sys.modules["aria"] = pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
sys.modules["aria.speech"] = types.ModuleType("aria.speech")

ba = importlib.import_module("aria.browser_agent")


class _Node:
    def __init__(self, text, value=""):
        self.text = text
        self.value = value
        self.clicked = False
        self.connected = True
        self.filled = None

    def inner_text(self, timeout=None):
        return self.text

    def get_attribute(self, name):
        return {"value": self.value}.get(name)

    def click(self):
        if not self.connected:
            raise RuntimeError("clicked a detached node")
        self.clicked = True

    def fill(self, text):
        self.filled = text

    def is_visible(self):
        return True


class _Handle:
    """A Playwright ElementHandle: bound to ONE node for life."""

    def __init__(self, node):
        self._node = node

    def inner_text(self, timeout=None):
        return self._node.inner_text(timeout)

    def get_attribute(self, name):
        return self._node.get_attribute(name)

    def click(self):
        self._node.click()

    def fill(self, text):
        self._node.fill(text)

    def is_visible(self):
        return True

    def evaluate(self, expr):
        if "isConnected" in expr:
            return self._node.connected
        if "parentElement" in expr:
            return None
        raise ValueError(f"unsupported expression {expr!r}")


class _Locator:
    """A Playwright Locator: re-resolves its selector on every call."""

    def __init__(self, page, key, node=None, nodes=None):
        self._page = page
        self._key = key
        self._node = node
        self._nodes = nodes

    def _current(self):
        if self._node is not None:
            return self._node
        return self._page._nodes[self._key]

    @property
    def first(self):
        return self

    def inner_text(self, timeout=None):
        return self._current().inner_text(timeout)

    def get_attribute(self, name):
        return self._current().get_attribute(name)

    def element_handle(self):
        return _Handle(self._current())

    def click(self):
        self._current().click()

    def fill(self, text):
        self._current().fill(text)

    def is_visible(self):
        return True

    def count(self):
        return len(self._nodes) if self._nodes is not None else 1

    def nth(self, i):
        return _Locator(self._page, self._key, node=self._nodes[i])


class _LocatorNoHandle(_Locator):
    """A locator double with no handle API (old test doubles)."""

    element_handle = None


class _Page:
    def __init__(self):
        self._nodes = {}
        self._union_nodes = []
        self.url = "https://shop.example/"

    def locator(self, selector):
        if selector == ba._UNION:
            return _Locator(self, selector, nodes=self._union_nodes)
        return _Locator(self, selector)

    def get_by_text(self, query, exact=False):
        return _Locator(self, ("text", query))

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return "Fake"


def _session_with(page):
    session = ba.BrowserSession()
    session.page = page
    return session


def test_swap_after_resolve_clicks_labelled_node():
    page = _Page()
    node_a = _Node("Continue shopping")
    node_b = _Node("Delete account")
    page._nodes["#next"] = node_a
    session = _session_with(page)
    bound, label = session._resolve("#next")
    assert label == "Continue shopping"
    # DOM swap between validation and click.
    page._nodes["#next"] = node_b
    out = session.click_resolved(bound, label)
    assert out == "Continue shopping"
    assert node_a.clicked, "the click must land on the labelled node"
    assert not node_b.clicked, "the replacement must never be clicked"


def test_detached_handle_refuses_instead_of_clicking():
    page = _Page()
    node_a = _Node("Continue shopping")
    page._nodes["#next"] = node_a
    session = _session_with(page)
    bound, label = session._resolve("#next")
    node_a.connected = False
    with pytest.raises(ValueError, match="detached|disappeared"):
        session.click_resolved(bound, label)
    assert not node_a.clicked


def test_benign_click_still_clicks_the_same_node():
    page = _Page()
    node_a = _Node("Continue shopping")
    page._nodes["#next"] = node_a
    session = _session_with(page)
    out = session.click("#next")
    assert out == "Continue shopping"
    assert node_a.clicked


def test_text_target_binds_the_matched_node():
    page = _Page()
    node_a = _Node("Send message")
    node_b = _Node("Delete account")
    page._nodes[("text", "Send")] = node_a
    session = _session_with(page)
    bound, label = session._resolve("text=Send")
    assert label == "Send message"
    page._nodes[("text", "Send")] = node_b
    session.click_resolved(bound, label)
    assert node_a.clicked
    assert not node_b.clicked


def test_locator_without_handle_api_still_clicks():
    page = _Page()
    node_a = _Node("Continue shopping")
    page._nodes["#next"] = node_a
    orig_locator = page.locator

    def _no_handle_locator(selector):
        return _LocatorNoHandle(page, selector)

    page.locator = _no_handle_locator
    session = _session_with(page)
    bound, label = session._resolve("#next")
    assert label == "Continue shopping"
    session.click_resolved(bound, label)
    assert node_a.clicked


def test_type_text_still_fills_the_resolved_node():
    page = _Page()
    node_a = _Node("", value="Search")
    page._nodes["#q"] = node_a
    session = _session_with(page)
    out = session.type_text("#q", "hello")
    assert "typed 5 chars" in out
    assert node_a.filled == "hello"
