"""Browser approvals are bound to the page and the session
(ChatGPT x ARIA loop, pass 24).

_guarded_click/_guarded_press used to bind a confirmed
irreversible activation to {action, target, label} only. A
confirmation for 'Place order' on shop A therefore replayed
cleanly on shop B when the same target carried the same
label there — the replay re-probed the label on whatever page
was currently loaded, and a dropped/recreated browser
session was indistinguishable from the original one. The
stashed args now carry the page URL and a per-session nonce,
and the approved replay re-checks label + URL + session
inside one session-thread call with the click/press, so a
moved context parks again for a fresh review instead of
executing the old confirmation against the new page.

Playwright is faked at the session seam; URL/session
binding, the label guard, the approval routing and the tool
actions are real."""

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

SHOP_A = "https://shop-a.example/checkout"
SHOP_B = "https://shop-b.example/checkout"


class FakeSession:
    LABELS = {"index:0": "Place order",
              "index:1": "Continue shopping"}

    def __init__(self, url=SHOP_A, nonce="session-a"):
        self.url = url
        self.nonce = nonce
        self.clicks = []
        self.presses = []
        self.resolve_calls = 0
        self.flip_url_on_resolve = None
        self.focused = {"tag": "button", "type": "", "role": "",
                        "contenteditable": False, "label": "Place order",
                        "activatable": True, "is_form_field": False,
                        "form_submit_labels": []}
        self.started = False

    def start(self):
        self.started = True

    def close(self):
        pass

    def context(self):
        return {"url": self.url, "session": self.nonce}

    def open(self, url):
        self.url = url
        return self.read()

    def read(self):
        return f"Page: Fake ({self.url})\n[0] Place order"

    def _resolve(self, target):
        self.resolve_calls += 1
        if (self.flip_url_on_resolve is not None
                and self.resolve_calls == self.flip_url_on_resolve):
            self.url = SHOP_B
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


def _install(session):
    runner = ba._Runner(session_factory=lambda: session)
    ba.set_runner_for_tests(runner)
    runner.session = session
    stashed = []
    state = {"replay_args": None}
    mod = types.ModuleType("aria.approval")
    mod.request_approval = lambda tool, args, desc=None: \
        stashed.append((tool, args, desc)) or \
        "[AWAITING_APPROVAL token=<redacted>"
    mod.in_approve_replay = lambda tool, args: \
        state["replay_args"] is not None and args == state["replay_args"]
    mod.get_mode = lambda: "confirm-risky"
    sys.modules["aria.approval"] = mod
    return stashed, state


def _park_click(session):
    stashed, state = _install(session)
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert "AWAITING_APPROVAL" in out
    assert session.clicks == []
    assert len(stashed) == 1
    return stashed, state


def test_click_stash_carries_page_and_session():
    session = FakeSession()
    stashed, _state = _park_click(session)
    args = stashed[0][1]
    assert args["action"] == "click"
    assert args["target"] == "index:0"
    assert args["label"] == "Place order"
    assert args["url"] == SHOP_A
    assert args["session"] == "session-a"
    assert "shop-a" in stashed[0][2]
    print("ok click_stash_carries_page_and_session")


def test_click_confirmed_for_one_page_does_not_execute_on_another():
    # ChatGPT's reproduction, verbatim shape: shop A parks the
    # 'Place order' click; the page navigates to shop B, where
    # index:0 also says 'Place order'; resolving the original
    # approval must NOT click shop B.
    session = FakeSession()
    stashed, state = _park_click(session)
    state["replay_args"] = stashed[0][1]
    session.url = SHOP_B
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert session.clicks == []             # shop B never clicked
    assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 2
    assert stashed[1][1]["label"] == "Place order"
    assert stashed[1][1]["url"] == SHOP_B
    assert stashed[1][1]["session"] == "session-a"
    print("ok click_other_page_parks_again")


def test_click_replay_executes_on_unchanged_page():
    session = FakeSession()
    stashed, state = _park_click(session)
    state["replay_args"] = stashed[0][1]
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert session.clicks == ["index:0"]
    assert "Clicked 'Place order'" in out
    assert len(stashed) == 1               # exactly once, no re-stash
    print("ok click_replay_unchanged_page")


def test_click_replay_after_session_recreated_parks_again():
    session = FakeSession()
    stashed, _state = _park_click(session)
    confirmed = stashed[0][1]
    # The browser session is closed and reopened on the SAME
    # page: a fresh nonce means the old confirmation is not
    # this session's to spend.
    fresh = FakeSession(url=SHOP_A, nonce="session-b")
    stashed2, state2 = _install(fresh)
    state2["replay_args"] = confirmed
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert fresh.clicks == []
    assert "AWAITING_APPROVAL" in out
    assert len(stashed2) == 1
    assert stashed2[0][1]["url"] == SHOP_A
    assert stashed2[0][1]["session"] == "session-b"
    print("ok click_recreated_session_parks_again")


def test_click_replay_refuses_mid_replay_navigation():
    # The page navigates between the replay's probe and its
    # click: the probe still sees shop A (marker matches), but
    # the atomic validate+click inside one session-thread call
    # sees shop B and refuses instead of clicking it.
    session = FakeSession()
    stashed, state = _park_click(session)
    state["replay_args"] = stashed[0][1]
    # Resolves so far: 1 (the park). The replay probe is #2,
    # the checked click is #3 — flip on #3.
    session.flip_url_on_resolve = 3
    out = ba.tool_browser_agent({"action": "click", "target": "index:0"})
    assert session.clicks == []
    assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 2
    assert stashed[1][1]["url"] == SHOP_B
    print("ok click_mid_replay_navigation_refused")


def test_press_confirmed_for_one_page_does_not_execute_on_another():
    session = FakeSession()
    stashed, state = _install(session)
    out = ba.tool_browser_agent({"action": "press", "key": "Enter"})
    assert "AWAITING_APPROVAL" in out
    assert session.presses == []
    assert stashed[0][1]["url"] == SHOP_A
    assert stashed[0][1]["session"] == "session-a"
    state["replay_args"] = stashed[0][1]
    session.url = SHOP_B
    out = ba.tool_browser_agent({"action": "press", "key": "Enter"})
    assert session.presses == []           # shop B never pressed
    assert "AWAITING_APPROVAL" in out
    assert len(stashed) == 2
    assert stashed[1][1]["label"] == "Place order"
    assert stashed[1][1]["url"] == SHOP_B
    print("ok press_other_page_parks_again")


def test_press_replay_executes_on_unchanged_page():
    session = FakeSession()
    stashed, state = _install(session)
    ba.tool_browser_agent({"action": "press", "key": "Enter"})
    state["replay_args"] = stashed[0][1]
    out = ba.tool_browser_agent({"action": "press", "key": "Enter"})
    assert session.presses == ["Enter"]
    assert "pressed Enter" in out
    assert len(stashed) == 1
    print("ok press_replay_unchanged_page")


def test_press_replay_after_session_recreated_parks_again():
    session = FakeSession()
    stashed, _state = _install(session)
    ba.tool_browser_agent({"action": "press", "key": "Enter"})
    confirmed = stashed[0][1]
    fresh = FakeSession(url=SHOP_A, nonce="session-b")
    stashed2, state2 = _install(fresh)
    state2["replay_args"] = confirmed
    out = ba.tool_browser_agent({"action": "press", "key": "Enter"})
    assert fresh.presses == []
    assert "AWAITING_APPROVAL" in out
    assert len(stashed2) == 1
    assert stashed2[0][1]["session"] == "session-b"
    print("ok press_recreated_session_parks_again")
