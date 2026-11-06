"""Timed-out MCP calls must never execute later (pass 26 B3#9).

_actor_call awaited the future behind asyncio.shield, so on
timeout the future was NOT cancelled and nothing removed the
enqueued item: the actor loop later executed the timed-out
call and settled the orphaned future. The caller saw a
timeout failure while the EFFECT still happened.
"""

import importlib
import os
import sys
import tempfile
import threading
import time
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_mcp_timeout_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir  # noqa: E402
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

mcp = importlib.import_module("aria.mcp")

CALLS = []
_CALLS_LOCK = threading.Lock()


class FakeHTTPClient:
    def __init__(self, url, headers=None):
        self.url = url

    def initialize(self):
        return {"result": {}}

    def list_tools(self):
        return []

    def call_tool(self, name, arguments):
        with _CALLS_LOCK:
            CALLS.append(name)
        if name == "slow":
            time.sleep(1.0)
        return {"result": {"content": [{"text": name}]}}


def _bridge_with_fake_actor():
    mcp.HTTPMCPClient = FakeHTTPClient
    CALLS.clear()
    return mcp.MCPBridge()


async def _scenario(bridge):
    task, queue, tools = await bridge._spawn_actor(
        "t", {"transport": "http", "url": "http://127.0.0.1:9/x",
              "headers": {}})
    entry = {"config": {"transport": "http"}, "task": task,
             "queue": queue, "tools": [], "aria_names": []}
    bridge._sessions["t"] = entry
    import asyncio
    slow = asyncio.create_task(
        bridge._actor_call("t", "slow", {}, timeout=10))
    await asyncio.sleep(0.2)  # let the actor pick up "slow"
    timed_out = False
    try:
        await bridge._actor_call("t", "fast", {}, timeout=0.2)
    except asyncio.TimeoutError:
        timed_out = True
    assert timed_out, "the waiter behind the slow call must time out"
    await slow
    await asyncio.sleep(0.5)  # actor would execute "fast" here if not cancelled
    await bridge._stop_actor(entry)
    return list(CALLS)


def test_timed_out_call_never_executes():
    bridge = _bridge_with_fake_actor()
    calls = bridge._run(_scenario(bridge), timeout=30)
    assert calls == ["slow"], calls
    print("ok timed_out_call_never_executes")


def test_slow_call_still_completes():
    # The fix must cancel only the timed-out waiter, not the
    # call already executing in the actor.
    bridge = _bridge_with_fake_actor()
    calls = bridge._run(_scenario(bridge), timeout=30)
    assert "slow" in calls
    print("ok slow_call_completes")
