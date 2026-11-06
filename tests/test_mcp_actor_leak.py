"""MCP connection timeout must not leak the server actor (pass 26 B4#1).

_spawn_actor awaited readiness with no TimeoutError path, so
on timeout neither the ready future nor the actor task was
cancelled — the actor sat in queue.get() forever holding the
subprocess/session. connect()'s _run timeout likewise left
the coroutine running. Both paths now cancel and await the
actor's exit.
"""

import asyncio
import importlib
import os
import sys
import tempfile
import threading
import time
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_mcp_actor_leak_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir  # noqa: E402
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

mcp = importlib.import_module("aria.mcp")

_NEVER = threading.Event()  # never set: initialize blocks forever


class NeverReadyClient:
    def __init__(self, url, headers=None):
        pass

    def initialize(self):
        _NEVER.wait(5)

    def list_tools(self):
        return []


async def _live_task_names():
    await asyncio.sleep(0.1)
    return [t.get_name() for t in asyncio.all_tasks()
            if not t.done()]


def test_spawn_timeout_cancels_actor():
    mcp.HTTPMCPClient = NeverReadyClient
    bridge = mcp.MCPBridge()

    async def scenario():
        try:
            await bridge._spawn_actor(
                "leak", {"transport": "http",
                         "url": "http://127.0.0.1:9/x", "headers": {}},
                ready_timeout=0.5)
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("spawn should have timed out")
        return await _live_task_names()

    names = bridge._run(scenario(), timeout=30)
    assert "aria-mcp-leak" not in names, names
    print("ok spawn_timeout_cancels_actor")


def test_run_timeout_cancels_coroutine():
    bridge = mcp.MCPBridge()
    state = []

    async def sleeper():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            state.append("cancelled")
            raise

    import concurrent.futures
    try:
        bridge._run(sleeper(), timeout=0.3)
    except concurrent.futures.TimeoutError:
        pass
    else:
        raise AssertionError("_run should have timed out")
    # The cancellation is delivered to the loop thread via
    # call_soon_threadsafe — give it a moment to land before
    # asserting.
    time.sleep(0.5)
    assert state == ["cancelled"], state
    print("ok run_timeout_cancels_coroutine")
