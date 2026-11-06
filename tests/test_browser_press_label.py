"""Press-guard approvals are bound to the probed label
(pass-16 residual, closed in pass 18).

_guarded_click has bound its approval to the live label since
pass 16; _guarded_press still matched its approve-replay
marker on {action, key} alone. A press confirmed while a
'Place order' button was focused would therefore execute even
if focus had moved to a 'Delete account' control by replay
time. The press args now carry the probed label in both the
stash and the replay check — the click pattern, unchanged:
same label replays, a different label parks again.

_Runner/_guarded_press only need a session with focused_info()
and press(), so the session here is a duck-typed fake; the
approval module is faked with marker semantics matching the
real in_approve_replay (equality against the stashed args).
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


def _button_focus(label):
    return {"tag": "button", "type": "submit", "role": "",
            "contenteditable": False, "label": label,
            "activatable": True, "is_form_field": False,
            "form_submit_labels": []}


class FakeSession:
    def __init__(self, label):
        self.focus_label = label
        self.presses = []
        self.url = "https://shop.example/"
        self.nonce = "fake-session-1"

    def context(self):
        return {"url": self.url, "session": self.nonce}

    def focused_info(self):
        return _button_focus(self.focus_label)

    def press(self, key):
        self.presses.append(key)
        return f"pressed {key}"


def _install(session, stashed, replay_args):
    runner = ba._Runner(session_factory=lambda: session)
    ba.set_runner_for_tests(runner)
    runner.session = session
    mod = types.ModuleType("aria.approval")
    mod.request_approval = lambda tool, args, desc=None: \
        stashed.append((tool, args, desc)) or \
        "[AWAITING_APPROVAL token=<redacted>"
    state = {"replay_args": replay_args}
    mod.in_approve_replay = lambda tool, args: \
        state["replay_args"] is not None and args == state["replay_args"]
    mod.get_mode = lambda: "confirm-risky"
    sys.modules["aria.approval"] = mod
    return state


def test_press_confirmed_for_one_label_does_not_execute_under_another():
    session = FakeSession("Place order")
    stashed = []
    state = _install(session, stashed, None)

    out = ba._guarded_press("Enter")
    assert "AWAITING_APPROVAL" in out
    assert session.presses == []
    (_tool, confirmed_args, _desc) = stashed[0]
    assert confirmed_args["label"] == "Place order"

    # The user confirms; by replay time focus has moved to a
    # different irreversible control. The marker must NOT match.
    state["replay_args"] = confirmed_args
    session.focus_label = "Delete account"
    out = ba._guarded_press("Enter")
    assert "AWAITING_APPROVAL" in out
    assert session.presses == []
    assert stashed[-1][1]["label"] == "Delete account"

    # Focus back on the confirmed control: the replay presses.
    session.focus_label = "Place order"
    out = ba._guarded_press("Enter")
    assert session.presses == ["Enter"]
    assert "pressed Enter" in out
