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
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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

SENT = []


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
