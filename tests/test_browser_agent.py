"""Supervised browser agent (Alek, approved 2026-10-28): a real
driver with her own profile, and an irreversible-action guard that
stashes an approval instead of clicking. Playwright is faked at
the session seam; URL blocking, the label guard, the approval
routing and the tool actions are real."""

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


class FakeSession:
    LABELS = {"index:0": "Place order",
              "index:1": "Continue shopping",
              "text=Send": "Send message"}

    def __init__(self):
        self.clicks = []
        self.typed = []
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def open(self, url):
        return f"opened {url}\n" + self.read()

    def read(self):
        return "Page: Fake (https://shop.example/)\n[0] Place order"

    def _resolve(self, target):
        return object(), self.LABELS.get(target, target)

    def click(self, target):
        self.clicks.append(target)
        return self.LABELS.get(target, target)

    def type_text(self, target, text):
        self.typed.append((target, text))
        return f"typed {len(text)} chars"

    def press(self, key):
        return f"pressed {key}"

    def scroll(self, direction):
        return f"scrolled {direction}"

    def back(self):
        return self.read()

    def screenshot(self):
        return "/fake/last.png"


def _install_fake():
    fake = FakeSession()
    runner = ba._Runner(session_factory=lambda: fake)
    ba.set_runner_for_tests(runner)
    return fake


def test_url_blocking():
    assert ba._url_blocked("https://example.com/page") == ""
    assert "private" in ba._url_blocked("http://192.168.1.1/admin")
    assert "private" in ba._url_blocked("https://localhost:8777/x")
    assert "scheme" in ba._url_blocked("file:///etc/passwd")
    assert "private" in ba._url_blocked("http://10.0.0.5/")
    print("ok url_blocking")


def test_irreversible_labels():
    for label in ("Place order", "Pay now", "Send message",
                  "Delete item", "Book now", "Complete order"):
        assert ba._is_irreversible(label), label
    for label in ("Continue shopping", "Add to cart", "Search", ""):
        assert not ba._is_irreversible(label), label
    print("ok irreversible_labels")


def test_open_read_type_close_round_trip():
    fake = _install_fake()
    out = ba.tool_browser_agent({"action": "open",
                                 "url": "https://shop.example/"})
    assert "opened https://shop.example/" in out
    assert fake.started
    out = ba.tool_browser_agent({"action": "type", "target": "index:1",
                                 "text": "hello"})
    assert "typed 5 chars" in out and fake.typed == [("index:1", "hello")]
    assert "pressed Enter" in ba.tool_browser_agent(
        {"action": "press", "key": "Enter"})
    assert "Screenshot saved" in ba.tool_browser_agent(
        {"action": "screenshot"})
    assert "closed" in ba.tool_browser_agent({"action": "close"})
    assert fake.closed
    assert "unknown action" in ba.tool_browser_agent({"action": "bogus"})
    print("ok round_trip")


def test_benign_click_executes():
    fake = _install_fake()
    ba.tool_browser_agent({"action": "open", "url": "https://shop.example/"})
    out = ba.tool_browser_agent({"action": "click", "target": "index:1"})
    assert fake.clicks == ["index:1"]
    assert "Clicked 'Continue shopping'" in out
    print("ok benign_click")


def test_irreversible_click_is_stashed_not_executed():
    fake = _install_fake()
    stashed = []
    fake_approval = types.ModuleType("aria.approval")
    fake_approval.request_approval = lambda tool, args, desc=None: \
        stashed.append((tool, args, desc)) or \
        "[AWAITING_APPROVAL token=t1] confirm?"
    sys.modules["aria.approval"] = fake_approval
    ba.tool_browser_agent({"action": "open", "url": "https://shop.example/"})
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert fake.clicks == []                      # never clicked
    assert "AWAITING_APPROVAL" in out
    assert stashed and stashed[0][0] == "browser_agent"
    assert stashed[0][1] == {"action": "click", "target": "index:0"}
    assert "Place order" in stashed[0][2]
    print("ok irreversible_stashed")


def test_private_url_refused_before_any_session():
    fake = _install_fake()
    out = ba.tool_browser_agent({"action": "open",
                                 "url": "http://192.168.1.1/admin"})
    assert "refused" in out
    assert not fake.started                       # refused pre-launch
    print("ok private_refused")


def _fake_approval_with_replay(replay_pred):
    """A fake aria.approval carrying both seams _guarded_click
    uses: request_approval (stash recorder) and in_approve_replay
    (the marker approve_token sets while replaying a confirmed
    call). Returns (module, stashed list)."""
    stashed = []
    mod = types.ModuleType("aria.approval")
    mod.request_approval = lambda tool, args, desc=None: \
        stashed.append((tool, args, desc)) or \
        "[AWAITING_APPROVAL token=t2] confirm?"
    mod.in_approve_replay = replay_pred
    sys.modules["aria.approval"] = mod
    return mod, stashed


def test_irreversible_click_executes_on_approve_replay():
    # The bug (2026-10-28): _guarded_click never checked the
    # approve-replay marker, so approve_token's replay of a held
    # click re-probed the irreversible label and stashed a FRESH
    # approval — a confirmed irreversible click could never
    # complete. With the marker set for this exact call, the
    # click goes through and nothing is re-stashed.
    fake = _install_fake()
    _mod, stashed = _fake_approval_with_replay(
        lambda tool, args: tool == "browser_agent"
        and args == {"action": "click", "target": "index:0"})
    ba.tool_browser_agent({"action": "open",
                           "url": "https://shop.example/"})
    out = ba.tool_browser_agent({"action": "click",
                                 "target": "index:0"})
    assert fake.clicks == ["index:0"]
    assert "Clicked 'Place order'" in out
    assert stashed == []
    print("ok irreversible_replay_executes")


def test_irreversible_click_replay_for_other_args_still_stashed():
    # The marker is bound to the exact (tool, args): a replay of
    # some OTHER call must not open this click's guard.
    fake = _install_fake()
    _mod, stashed = _fake_approval_with_replay(
        lambda tool, args: False)
    ba.tool_browser_agent({"action": "open",
                           "url": "https://shop.example/"})
    out = ba.tool_browser_agent({"action": "click",
                                 "target": "index:0"})
    assert fake.clicks == []
    assert "AWAITING_APPROVAL" in out
    assert stashed and stashed[0][1] == {"action": "click",
                                          "target": "index:0"}
    print("ok irreversible_replay_mismatch_stashed")


def test_irreversible_click_replay_with_real_approval_module():
    # End to end against the REAL aria/approval.py: set the exact
    # thread-local marker approve_token() sets around its replay
    # and the held click completes; clear it and the same click
    # is stashed by the real request_approval instead.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aria.approval", os.path.join(PKG, "approval.py"))
    real = importlib.util.module_from_spec(spec)
    prev = sys.modules.get("aria.approval")
    sys.modules["aria.approval"] = real
    spec.loader.exec_module(real)
    try:
        fake = _install_fake()
        ba.tool_browser_agent({"action": "open",
                               "url": "https://shop.example/"})
        real._replay_local.current = (
            "browser_agent", {"action": "click", "target": "index:0"})
        try:
            out = ba.tool_browser_agent({"action": "click",
                                         "target": "index:0"})
        finally:
            real._replay_local.current = None
        assert fake.clicks == ["index:0"]
        assert "Clicked 'Place order'" in out
        assert real.pending_entries() == []
        out = ba.tool_browser_agent({"action": "click",
                                     "target": "index:0"})
        assert fake.clicks == ["index:0"]          # not clicked again
        assert "AWAITING_APPROVAL" in out
        held = real.pending_entries()
        assert held and held[0]["tool"] == "browser_agent"
        assert "Place order" in held[0]["desc"]
    finally:
        if prev is not None:
            sys.modules["aria.approval"] = prev
        else:
            sys.modules.pop("aria.approval", None)
    print("ok irreversible_replay_real_module")
