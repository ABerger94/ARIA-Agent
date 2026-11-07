"""Pass 27 #8: a validated click must not re-resolve its target.

_guarded_click validated _resolve(target) and then clicked via
session.click(target), which resolved the target a second time —
a DOM swap between the two resolves could substitute a different
control. The guard now clicks the already-resolved locator
through click_resolved (an element-handle click on the exact
node the label was read from); a detached node fails instead of
clicking its replacement.
"""
import importlib
import os
import sys
import types

import pytest

_TMPHOME = os.environ.get("HOME", "/tmp")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


class FakeSession:
    """Each _resolve returns a fresh locator sentinel + the next
    label in sequence, so the test can prove which resolve the
    click used — and whether a third resolve ever happened."""

    def __init__(self, labels):
        self.labels = list(labels)
        self.resolves = 0
        self.locators = []
        self.resolved_clicks = []
        self.clicks = []
        self.url = "https://shop.example/"
        self.nonce = "fake-session-1"

    def context(self):
        return {"url": self.url, "session": self.nonce}

    def start(self):
        pass

    def close(self):
        pass

    def read(self):
        return "Page: Fake (https://shop.example/)\n[0] Place order"

    def _resolve(self, target):
        self.resolves += 1
        label = (self.labels[min(self.resolves - 1,
                                 len(self.labels) - 1)])
        loc = object()
        self.locators.append(loc)
        return loc, label

    def click_resolved(self, loc, label):
        self.resolved_clicks.append((loc, label))
        return label

    def click(self, target):
        self.clicks.append(target)
        _loc, label = self._resolve(target)
        return label


def _fake_approval_with_replay(replay_pred):
    stashed = []
    mod = types.ModuleType("aria.approval")
    mod.request_approval = lambda tool, args, desc=None: \
        stashed.append((tool, args, desc)) or \
        "[AWAITING_APPROVAL token=<redacted>"
    mod.in_approve_replay = replay_pred
    sys.modules["aria.approval"] = mod
    return mod, stashed


def _install(fake):
    ba.set_runner_for_tests(ba._Runner(session_factory=lambda: fake))
    return fake


def test_validated_click_uses_resolved_locator():
    fake = _install(FakeSession(["Continue shopping"] * 3))
    _mod, stashed = _fake_approval_with_replay(lambda *a, **k: False)
    out = ba.tool_browser_agent({"action": "click", "target": "index:1"})
    assert "Clicked 'Continue shopping'" in out, out
    assert fake.resolves == 2, fake.resolves  # probe + re-check only
    assert len(fake.resolved_clicks) == 1
    # The click used the locator the re-check validated — the
    # second resolve's object — not a fresh resolution.
    assert fake.resolved_clicks[0][0] is fake.locators[1]
    assert fake.resolved_clicks[0][1] == "Continue shopping"
    assert fake.clicks == [], "click(target) re-resolved the target"
    assert stashed == []


def test_no_third_resolve_after_validation():
    # The page swaps the control AFTER the guard's re-check: a
    # third resolve would return "Delete account". The validated
    # click must never ask — it clicks the node it already holds.
    fake = _install(FakeSession(["Continue shopping",
                                 "Continue shopping",
                                 "Delete account"]))
    _mod, stashed = _fake_approval_with_replay(lambda *a, **k: False)
    out = ba.tool_browser_agent({"action": "click", "target": "index:1"})
    assert "Clicked 'Continue shopping'" in out, out
    assert fake.resolves == 2, fake.resolves
    assert fake.resolved_clicks[0][1] == "Continue shopping"
    assert stashed == []


def test_approved_replay_clicks_validated_locator():
    fake = _install(FakeSession(["Place order"] * 3))
    _mod, stashed = _fake_approval_with_replay(
        lambda tool, args: tool == "browser_agent"
        and args == {"action": "click", "target": "index:0",
                     "label": "Place order",
                     "url": "https://shop.example/",
                     "session": "fake-session-1"})
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert "Clicked 'Place order'" in out, out
    assert fake.resolves == 2, fake.resolves
    assert fake.resolved_clicks[0][0] is fake.locators[1]
    assert fake.clicks == []
    assert stashed == []


def test_detached_handle_refuses_instead_of_clicking():
    fake = _install(FakeSession(["Continue shopping"] * 3))

    def _gone(loc, label):
        raise ValueError(
            "browser_agent: the control disappeared before the click")

    fake.click_resolved = _gone
    _mod, stashed = _fake_approval_with_replay(lambda *a, **k: False)
    out = ba.tool_browser_agent({"action": "click", "target": "index:1"})
    # The tool boundary types the refusal (UNVERIFIED for effect
    # actions) instead of raising — the point is that nothing
    # was clicked and no re-resolution was attempted.
    assert "disappeared" in out, out
    assert "Clicked" not in out, out
    assert fake.resolved_clicks == []
    assert fake.clicks == []
