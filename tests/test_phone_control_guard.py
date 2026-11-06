"""Phone control tools refuse in BOTH non-live scopes.

Pass-16 runner-up, closed in pass 18: phone_node._control_guard
checked only dispatch.in_preauthorized_scope(). Delegated tasks
run execute_tool(preauthorized=False) wrapped in
dispatch.delegated_scope(), so a delegated phone tap / type /
swipe / key sailed past the guard and acted on whatever was on
the phone's screen — while the refusal text (and the module
docstring) claimed delegated calls were covered. shelltool and
restart_program have always checked both scopes; the phone
guard now matches them.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMPHOME = tempfile.mkdtemp(prefix='aria_phone_guard_')

pkg = types.ModuleType("aria")
pkg.__path__ = [str(Path(__file__).parents[1] / "aria")]
sys.modules.setdefault("aria", pkg)

for _name in ("aria.scheduler", "aria.speech", "aria.hud",
              "aria.frame", "aria.vision", "aria.hardware",
              "aria.agent.taskqueue"):
    sys.modules.setdefault(_name, types.ModuleType(_name))

import pytest

phone_node = importlib.import_module("aria.phone_node")
dispatch = importlib.import_module("aria.tools.dispatch")
approval = importlib.import_module("aria.approval")
config = importlib.import_module("aria.config")
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')

SENT = []


def _fresh():
    SENT.clear()
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


@pytest.fixture(autouse=True)
def hub(monkeypatch):
    SENT.clear()
    hub = types.SimpleNamespace(
        send_command=lambda node, action, args: SENT.append(
            (action, args)) or {"ok": True})
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    yield
    SENT.clear()


def test_live_tap_sends():
    out = phone_node.tool_phone_tap(x=10, y=20)
    assert "Tapped" in out
    assert SENT and SENT[0][0] == "tap"


def test_delegated_scope_refuses_all_control_tools():
    with dispatch.delegated_scope():
        for out in (
            phone_node.tool_phone_tap(x=10, y=20),
            phone_node.tool_phone_swipe(x1=0, y1=0, x2=5, y2=5),
            phone_node.tool_phone_type(text="hello"),
            phone_node.tool_phone_key(key="home"),
        ):
            assert "never runs inside a routine replay" in out
    assert SENT == []  # nothing reached the phone


def test_preauthorized_scope_still_refuses():
    dispatch._SCOPE.preauthorized = True
    try:
        out = phone_node.tool_phone_tap(x=10, y=20)
        assert "never runs inside a routine replay" in out
    finally:
        dispatch._SCOPE.preauthorized = False
    assert SENT == []


# ── Pass 25, P1#2: phone input gates like the desktop GUI tools ──

def test_policy_sets_contain_phone_input():
    for tool in ("phone_tap", "phone_swipe", "phone_type", "phone_key"):
        assert tool in approval.DESTRUCTIVE_TOOLS, tool
        assert tool in config.SAFE_MODE_BLOCKED_TOOLS, tool
    # phone_open_app stays ungated (a discrete, describable action);
    # the read side stays ungated too.
    for tool in ("phone_open_app", "phone_list_apps", "phone_status",
                 "phone_read_screen", "phone_notifications"):
        assert tool not in approval.DESTRUCTIVE_TOOLS, tool
        assert tool not in config.SAFE_MODE_BLOCKED_TOOLS, tool
    # The held banner names the action, not a bare tool name.
    assert approval._describe_tool(
        "phone_tap", {"ref": "ok-btn"}) == "tap phone element 'ok-btn'"
    assert approval._describe_tool(
        "phone_tap", {"x": 10, "y": 20}) == \
        "tap phone at coordinates (10, 20)"
    assert "hello" in approval._describe_tool(
        "phone_type", {"text": "hello"})
    assert approval._describe_tool(
        "phone_key", {"key": "home"}) == "press phone key 'home'"


def test_safe_mode_blocks_phone_type():
    _fresh()
    os.environ['ARIA_SAFE_MODE'] = '1'
    res, _ok = dispatch.execute_tool('phone_type', {'text': 'hello'})
    assert res.startswith("[Safe mode: 'phone_type' is disabled."), res
    assert SENT == []
    assert approval._PENDING == {}, \
        'safe-mode block created a pending approval'


def test_confirm_risky_holds_phone_tap():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    args = {'x': 10, 'y': 20}
    res, _ok = dispatch.execute_tool('phone_tap', args)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert SENT == [], 'held tap reached the phone without approval'
    held = [e for e in approval._PENDING.values()
            if e['tool'] == 'phone_tap']
    assert held and held[0]['args'] == args, \
        'held entry is not the exact phone_tap call'
    assert held[0]['desc'] == "tap phone at coordinates (10, 20)", held[0]


def test_confirm_all_approve_replay_executes_tap():
    # The pass-25 bug: under confirm-all the approved tap replayed
    # preauthorized and the guard refused it — the approval was
    # spent, the action died, and the token logged "executed".
    _fresh()
    approval.tool_set_approval_mode('confirm-all')
    args = {'x': 10, 'y': 20}
    res, _ok = dispatch.execute_tool('phone_tap', args)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert SENT == []
    token = res.split('token=', 1)[1].split(']', 1)[0]
    out = approval.approve_token(token)
    assert SENT == [('tap', {'x': 10, 'y': 20})], SENT
    assert 'Tapped' in out, out


def test_approve_replay_marker_is_exact():
    # A marker for another tool does not exempt the guard, and a
    # marker for this tool with different args does not either —
    # only the approved call runs in a preauthorized scope.
    dispatch._SCOPE.preauthorized = True
    try:
        approval._replay_local.current = ('gui_click', {})
        out = phone_node.tool_phone_tap(
            x=10, y=20, _raw_args={'x': 10, 'y': 20})
        assert "never runs inside a routine replay" in out
        approval._replay_local.current = ('phone_tap', {'x': 99, 'y': 99})
        out = phone_node.tool_phone_tap(
            x=10, y=20, _raw_args={'x': 10, 'y': 20})
        assert "never runs inside a routine replay" in out
        approval._replay_local.current = ('phone_tap', {'x': 10, 'y': 20})
        out = phone_node.tool_phone_tap(
            x=10, y=20, _raw_args={'x': 10, 'y': 20})
        assert "Tapped" in out, out
    finally:
        approval._replay_local.current = None
        dispatch._SCOPE.preauthorized = False
    assert SENT == [('tap', {'x': 10, 'y': 20})], SENT


def test_auto_mode_live_tap_still_sends():
    _fresh()
    res, _ok = dispatch.execute_tool('phone_tap', {'x': 10, 'y': 20})
    assert 'Tapped' in res, res
    assert SENT == [('tap', {'x': 10, 'y': 20})], SENT
    assert approval._PENDING == {}, 'auto tap created a pending approval'
