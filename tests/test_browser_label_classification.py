"""Pass 27 #9: browser classification must use the whole label,
and an unlabelled control must park, not click.

Labels were truncated to 60 chars BEFORE classification, so a
destructive phrase past character 60 waved through — and an
empty label was simply benign. Now _resolve/_element_label
return the full label (display truncation lives only in
_display_label), actionable ancestors lend an aria-label/title,
and an unlabelled click parks for review. A control confirmed
while unlabelled still executes while it stays unlabelled; a
confirmed control that LOSES its label parks again.
"""
import importlib
import os
import sys
import types

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
        label = self.labels[min(self.resolves - 1,
                                len(self.labels) - 1)]
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


def _replay_args(label, target="index:0"):
    return {"action": "click", "target": target, "label": label,
            "url": "https://shop.example/",
            "session": "fake-session-1"}


def test_destructive_phrase_past_char_60_parks():
    long_label = "A" * 70 + " Delete account"
    assert len(long_label) > 60
    fake = _install(FakeSession([long_label] * 3))
    _mod, stashed = _fake_approval_with_replay(lambda *a, **k: False)
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert "AWAITING_APPROVAL" in out, out
    assert fake.resolved_clicks == [] and fake.clicks == []
    assert len(stashed) == 1
    # The stashed label is the WHOLE label — truncation hid it.
    assert stashed[0][1]["label"] == long_label


def test_unlabelled_click_parks():
    fake = _install(FakeSession([""] * 3))
    _mod, stashed = _fake_approval_with_replay(lambda *a, **k: False)
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert "AWAITING_APPROVAL" in out, out
    assert fake.resolved_clicks == [] and fake.clicks == []
    assert len(stashed) == 1
    assert stashed[0][1]["label"] == ""
    assert "unlabelled" in stashed[0][2], stashed[0][2]


def test_control_that_loses_its_label_parks():
    fake = _install(FakeSession(["Continue shopping", ""]))
    _mod, stashed = _fake_approval_with_replay(lambda *a, **k: False)
    out = ba.tool_browser_agent({"action": "click", "target": "index:1"})
    assert "AWAITING_APPROVAL" in out, out
    assert fake.resolved_clicks == [] and fake.clicks == []
    assert stashed[0][1]["label"] == ""


def test_confirmed_unlabelled_click_executes_while_unlabelled():
    fake = _install(FakeSession([""] * 3))
    _mod, stashed = _fake_approval_with_replay(
        lambda tool, args: tool == "browser_agent"
        and args == _replay_args(""))
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert "Clicked" in out, out
    assert len(fake.resolved_clicks) == 1
    assert stashed == []


def test_confirmed_click_parks_when_label_is_lost():
    fake = _install(FakeSession(["Place order", ""]))
    _mod, stashed = _fake_approval_with_replay(
        lambda tool, args: tool == "browser_agent"
        and args == _replay_args("Place order"))
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert "AWAITING_APPROVAL" in out, out
    assert fake.resolved_clicks == [] and fake.clicks == []
    assert stashed[0][1]["label"] == ""


class _StubEl:
    def __init__(self, text="", attrs=None, parent=None):
        self._text = text
        self._attrs = attrs or {}
        self._parent = parent

    def inner_text(self, timeout=None):
        if self._text == "RAISE":
            raise RuntimeError("unavailable")
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def locator(self, selector):
        if selector != "xpath=.." or self._parent is None:
            raise RuntimeError("no such locator")
        return self._parent


def test_element_label_is_whole_and_uses_ancestors():
    long_text = "B" * 80 + " Delete account"
    el = _StubEl(text=long_text)
    assert ba._element_label(el) == long_text
    assert ba._is_irreversible(ba._element_label(el))

    parent = _StubEl(attrs={"aria-label": "Delete account"})
    icon = _StubEl(text="", parent=parent)
    assert ba._element_label(icon) == "Delete account"

    bare = _StubEl(text="")
    assert ba._element_label(bare) == ""
    assert ba._ancestor_label(bare) == ""
    assert not ba._is_irreversible("")


def test_display_label_truncates_only_for_display():
    assert ba._display_label("x" * 100) == "x" * 57 + "..."
    assert ba._display_label("Short") == "Short"
    assert ba._display_label("") == "unlabelled control"
