"""Auto means auto for the browser agent's guards (Alek's
ruling, 2026-10-31 — the extension of the shell ruling).

aria/browser_agent.py's two handler-side gates — the
irreversible-label click gate (_guarded_click) and the keyboard
activation gate (_guarded_press, the Fix 2 guard) — parked EVERY
guarded activation behind aria.approval.request_approval
regardless of approval mode, the same always-confirm pattern
run_shell_command had. Alek ruled "make auto mean auto" for
shell commands (db712871) and extended it the same day: "It
should cover her browser clicks too."

So both gates now consult the SAME mode source dispatch's
needs_approval consults — aria.approval.get_mode() — and:

  - mode 'auto': a guarded click / activation press executes
    immediately. No pending entry, no banner, no approval
    state of any kind.
  - modes 'confirm-risky' / 'confirm-all': unchanged — the
    activation parks, and the user's Confirm executes it
    exactly once (the 37fa5fce approve-replay chain underneath).
  - safe mode: the auto lane is closed — a guarded activation
    parks in every mode, exactly as before, so auto never
    becomes safe mode's unattended-execution hole.
  - an unreadable mode (get_mode raising, or an approval
    module without one) reads as not-auto and the activation
    parks — the pre-ruling behaviour, fail-safe.

Playwright is faked at the session seam and aria.approval is a
fake module in sys.modules, the seams tests/test_browser_
agent.py already uses; only the mode/safe-mode reads are real
(aria.config.is_safe_mode reads the ARIA_SAFE_MODE env var).
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


class FakeSession:
    LABELS = {"index:0": "Place order",
              "index:1": "Continue shopping",
              "text=Send": "Send message"}

    def __init__(self):
        self.clicks = []
        self.presses = []
        self.focused = {"tag": "body", "type": "", "role": "",
                        "contenteditable": False, "label": "",
                        "activatable": False, "is_form_field": False,
                        "form_submit_labels": []}
        self.started = False

    def start(self):
        self.started = True

    def open(self, url):
        return f"opened {url}\n" + self.read()

    def read(self):
        return "Page: Fake (https://shop.example/)\n[0] Place order"

    def _resolve(self, target):
        return object(), self.LABELS.get(target, target)

    def click(self, target):
        self.clicks.append(target)
        return self.LABELS.get(target, target)

    def focused_info(self):
        info = dict(self.focused)
        info["form_submit_labels"] = list(
            info.get("form_submit_labels") or [])
        return info

    def press(self, key):
        self.presses.append(key)
        return f"pressed {key}"


def _install_fake():
    fake = FakeSession()
    runner = ba._Runner(session_factory=lambda: fake)
    ba.set_runner_for_tests(runner)
    return fake


def _fake_approval(mode="confirm-risky", replay=False,
                   get_mode_raises=False):
    """A fake aria.approval carrying the seams the guards use:
    request_approval (stash recorder), in_approve_replay (the
    marker predicate) and — unless mode is None — get_mode.
    Returns (module, stashed list)."""
    stashed = []
    mod = types.ModuleType("aria.approval")
    mod.request_approval = lambda tool, args, desc=None: \
        stashed.append((tool, args, desc)) or \
        "[AWAITING_APPROVAL token=tX] confirm?"
    if callable(replay):
        mod.in_approve_replay = replay
    else:
        mod.in_approve_replay = lambda tool, args: bool(replay)
    if get_mode_raises:
        def _boom():
            raise RuntimeError("mode store unreadable")
        mod.get_mode = _boom
    elif mode is not None:
        mod.get_mode = lambda: mode
    return mod, stashed


class _swap_approval:
    def __init__(self, mod):
        self.mod = mod

    def __enter__(self):
        self.prev = sys.modules.get("aria.approval")
        sys.modules["aria.approval"] = self.mod
        return self.mod

    def __exit__(self, *exc):
        if self.prev is not None:
            sys.modules["aria.approval"] = self.prev
        else:
            sys.modules.pop("aria.approval", None)


class _safe_mode_env:
    def __enter__(self):
        self.prev = os.environ.get("ARIA_SAFE_MODE")
        os.environ["ARIA_SAFE_MODE"] = "1"

    def __exit__(self, *exc):
        if self.prev is None:
            os.environ.pop("ARIA_SAFE_MODE", None)
        else:
            os.environ["ARIA_SAFE_MODE"] = self.prev


def _button_focus(label):
    return {"tag": "button", "type": "", "role": "",
            "contenteditable": False, "label": label,
            "activatable": True, "is_form_field": False,
            "form_submit_labels": []}


def _field_focus(submit_labels):
    return {"tag": "input", "type": "text", "role": "",
            "contenteditable": False, "label": "Card number",
            "activatable": False, "is_form_field": True,
            "form_submit_labels": list(submit_labels)}


def _open(fake):
    ba.tool_browser_agent({"action": "open",
                           "url": "https://shop.example/"})


# ---------------- auto: the lane is open ----------------

def test_auto_irreversible_click_executes_no_approval_state():
    fake = _install_fake()
    mod, stashed = _fake_approval(mode="auto")
    with _swap_approval(mod):
        _open(fake)
        out = ba.tool_browser_agent({"action": "click",
                                     "target": "index:0"})
    assert fake.clicks == ["index:0"]
    assert "Clicked 'Place order'" in out
    assert stashed == []                      # zero approval state
    print("ok auto_click_executes")


def test_auto_irreversible_press_executes_no_approval_state():
    fake = _install_fake()
    fake.focused = _button_focus("Place order")
    mod, stashed = _fake_approval(mode="auto")
    with _swap_approval(mod):
        _open(fake)
        out = ba.tool_browser_agent({"action": "press",
                                     "key": "Enter"})
    assert fake.presses == ["Enter"]
    assert "pressed Enter" in out
    assert stashed == []
    print("ok auto_press_executes")


def test_auto_enter_in_field_with_irreversible_submit_executes():
    fake = _install_fake()
    fake.focused = _field_focus(["Place order"])
    mod, stashed = _fake_approval(mode="auto")
    with _swap_approval(mod):
        _open(fake)
        ba.tool_browser_agent({"action": "press", "key": "Enter"})
    assert fake.presses == ["Enter"]
    assert stashed == []
    print("ok auto_enter_in_field_executes")


def test_auto_unlabelled_control_press_executes():
    # Fail-closed in the confirm modes (no label = cannot be
    # proven safe); in auto the lane covers it like any other
    # guarded activation — auto changes what a live call does.
    fake = _install_fake()
    fake.focused = _button_focus("")
    mod, stashed = _fake_approval(mode="auto")
    with _swap_approval(mod):
        _open(fake)
        ba.tool_browser_agent({"action": "press", "key": "Enter"})
    assert fake.presses == ["Enter"]
    assert stashed == []
    print("ok auto_unlabelled_press_executes")


# ---------------- confirm modes: unchanged ----------------

def test_confirm_risky_click_parks_and_replay_executes_once():
    fake = _install_fake()
    mod, stashed = _fake_approval(mode="confirm-risky")
    with _swap_approval(mod):
        _open(fake)
        out = ba.tool_browser_agent({"action": "click",
                                     "target": "index:0"})
        assert fake.clicks == []
        assert "AWAITING_APPROVAL" in out
        assert stashed and stashed[0][1] == {
            "action": "click", "target": "index:0"}
        # The user's Confirm: approve_token replays the exact
        # call with the marker set — it executes, and does NOT
        # stash a second approval (exactly once).
        mod.in_approve_replay = lambda tool, args: \
            tool == "browser_agent" and args == {
                "action": "click", "target": "index:0"}
        out = ba.tool_browser_agent({"action": "click",
                                     "target": "index:0"})
    assert fake.clicks == ["index:0"]
    assert "Clicked 'Place order'" in out
    assert len(stashed) == 1
    print("ok confirm_risky_click_park_replay_once")


def test_confirm_risky_press_parks_and_replay_executes_once():
    fake = _install_fake()
    fake.focused = _button_focus("Place order")
    mod, stashed = _fake_approval(mode="confirm-risky")
    with _swap_approval(mod):
        _open(fake)
        out = ba.tool_browser_agent({"action": "press",
                                     "key": "Enter"})
        assert fake.presses == []
        assert "AWAITING_APPROVAL" in out
        assert stashed and stashed[0][1] == {
            "action": "press", "key": "Enter"}
        mod.in_approve_replay = lambda tool, args: \
            tool == "browser_agent" and args == {
                "action": "press", "key": "Enter"}
        ba.tool_browser_agent({"action": "press", "key": "Enter"})
    assert fake.presses == ["Enter"]
    assert len(stashed) == 1
    print("ok confirm_risky_press_park_replay_once")


def test_confirm_all_click_parks():
    fake = _install_fake()
    mod, stashed = _fake_approval(mode="confirm-all")
    with _swap_approval(mod):
        _open(fake)
        out = ba.tool_browser_agent({"action": "click",
                                     "target": "index:0"})
    assert fake.clicks == []
    assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 1
    print("ok confirm_all_click_parks")


# ---------------- safe mode closes the auto lane ----------------

def test_safe_mode_parks_irreversible_click_in_auto():
    fake = _install_fake()
    mod, stashed = _fake_approval(mode="auto")
    with _swap_approval(mod), _safe_mode_env():
        _open(fake)
        out = ba.tool_browser_agent({"action": "click",
                                     "target": "index:0"})
    assert fake.clicks == []
    assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 1
    print("ok safe_mode_parks_click_in_auto")


def test_safe_mode_parks_irreversible_press_in_auto():
    fake = _install_fake()
    fake.focused = _button_focus("Place order")
    mod, stashed = _fake_approval(mode="auto")
    with _swap_approval(mod), _safe_mode_env():
        _open(fake)
        out = ba.tool_browser_agent({"action": "press",
                                     "key": "Enter"})
    assert fake.presses == []
    assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 1
    print("ok safe_mode_parks_press_in_auto")


# ---------------- unreadable mode fails safe (parks) ----------------

def test_unreadable_mode_parks_click_and_press():
    fake = _install_fake()
    fake.focused = _button_focus("Place order")
    mod, stashed = _fake_approval(mode="auto", get_mode_raises=True)
    with _swap_approval(mod):
        _open(fake)
        out = ba.tool_browser_agent({"action": "click",
                                     "target": "index:0"})
        assert fake.clicks == []
        assert "AWAITING_APPROVAL" in out
        out = ba.tool_browser_agent({"action": "press",
                                     "key": "Enter"})
        assert fake.presses == []
        assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 2
    print("ok unreadable_mode_parks")


def test_missing_get_mode_parks():
    # An approval module without get_mode at all (the shape the
    # older test fakes carry) must read as not-auto.
    fake = _install_fake()
    mod, stashed = _fake_approval(mode=None)
    assert not hasattr(mod, "get_mode")
    with _swap_approval(mod):
        _open(fake)
        out = ba.tool_browser_agent({"action": "click",
                                     "target": "index:0"})
    assert fake.clicks == []
    assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 1
    print("ok missing_get_mode_parks")
