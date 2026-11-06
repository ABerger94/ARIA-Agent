"""Browser error returns must not certify a mission as done
(pass 24 runner-up #2, fixed in this pass).

Before the fix, tool_browser_agent returned its refusal and
exception strings as plain text; outcome.normalize read
unrecognized nonempty strings as DONE, so missions._is_failure
was False and a refused localhost navigation marked the step
(and the mission) done. Now the deterministic no-effect errors
are typed FAILED at the handler boundary and exceptions from
effect actions (click/type/press, which may have partially
happened) are UNVERIFIED, so a mission parks instead of
retrying blind. Success strings stay DONE.

Harness: the fake session from tests/test_browser_agent.py,
reduced to what this suite needs; URL blocking, the guard and
the tool actions are real.
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
from aria.outcome import FAILED, UNVERIFIED, DONE, normalize  # noqa: E402


class FakeSession:
    def __init__(self):
        self.clicks = []
        self.url = "https://shop.example/"
        self.nonce = "fake-session-1"
        self.raise_on = {}

    def context(self):
        return {"url": self.url, "session": self.nonce}

    def start(self):
        pass

    def close(self):
        pass

    def open(self, url):
        if "open" in self.raise_on:
            raise self.raise_on["open"]
        return f"opened {url}"

    def read(self):
        if "read" in self.raise_on:
            raise self.raise_on["read"]
        return "Page: Fake (https://shop.example/)"

    def _resolve(self, target):
        return object(), target

    def click(self, target):
        if "click" in self.raise_on:
            raise self.raise_on["click"]
        self.clicks.append(target)
        return target

    def type_text(self, target, text):
        if "type" in self.raise_on:
            raise self.raise_on["type"]
        return f"typed {len(text)} chars"

    def focused_info(self):
        return {"tag": "body", "type": "", "role": "",
                "contenteditable": False, "label": "",
                "activatable": False, "is_form_field": False,
                "form_submit_labels": []}

    def press(self, key):
        if "press" in self.raise_on:
            raise self.raise_on["press"]
        return f"pressed {key}"

    def scroll(self, direction):
        return f"scrolled {direction}"

    def back(self):
        return self.read()

    def screenshot(self):
        return "/fake/last.png"


def _install_fake(raise_on=None):
    fake = FakeSession()
    fake.raise_on = raise_on or {}
    runner = ba._Runner(session_factory=lambda: fake)
    ba.set_runner_for_tests(runner)
    return fake


def _status(result):
    return normalize(result).status


def _rendered(result):
    # What the dispatcher hands onward: str(normalize(r)).
    return str(normalize(result))


def test_refused_open_is_failed():
    _install_fake()
    result = ba.tool_browser_agent(
        {"action": "open", "url": "http://localhost:8080/admin"})
    assert "refused" in result
    assert _status(result) == FAILED
    assert _rendered(result).startswith("status=failed ")


def test_missing_arg_and_unknown_action_are_failed():
    _install_fake()
    assert _status(ba.tool_browser_agent({"action": "open"})) == FAILED
    assert _status(ba.tool_browser_agent(
        {"action": "click"})) == FAILED
    assert _status(ba.tool_browser_agent(
        {"action": "dance"})) == FAILED


def test_click_exception_is_unverified_not_failed():
    _install_fake(raise_on={"click": RuntimeError("boom")})
    result = ba.tool_browser_agent(
        {"action": "click", "target": "index:1"})
    assert "failed" in result
    assert _status(result) == UNVERIFIED
    assert _rendered(result).startswith("status=unverified ")


def test_press_exception_is_unverified():
    _install_fake(raise_on={"press": RuntimeError("boom")})
    result = ba.tool_browser_agent({"action": "press", "key": "a"})
    assert _status(result) == UNVERIFIED


def test_read_exception_is_failed():
    _install_fake(raise_on={"read": RuntimeError("boom")})
    result = ba.tool_browser_agent({"action": "read"})
    assert _status(result) == FAILED


def test_success_stays_done():
    fake = _install_fake()
    result = ba.tool_browser_agent(
        {"action": "click", "target": "index:1"})
    assert _status(result) == DONE
    assert fake.clicks == ["index:1"]
    assert _status(ba.tool_browser_agent(
        {"action": "open", "url": "https://example.com/"})) == DONE
