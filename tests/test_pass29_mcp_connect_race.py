"""Concurrent MCP connects must not leak a server actor (pass 29 #32).

connect()'s existence check, spawn, and store used to run
unsynchronized — two concurrent connects both spawned an actor
and the second overwrote _sessions[key], leaking the first
actor with no entry able to stop it. The check -> spawn ->
store -> register sequence now runs under a per-server lock,
and a registration failure stops the actor and drops the entry
instead of leaking it.
"""

import asyncio
import importlib
import os
import sys
import tempfile
import threading
import time
import types
from types import SimpleNamespace

_TMPHOME = tempfile.mkdtemp(prefix="aria_pass29_mcp_connect_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir  # noqa: E402
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

# --- fake aria.tools.schemas / aria.tools.dispatch -----------------------
schemas = types.ModuleType("aria.tools.schemas")
schemas.registered = {}
schemas.unregistered = []


def _reg_decl(name, decl, toolkit=None):
    schemas.registered[name] = decl


def _unreg_decl(name, toolkit=None):
    schemas.unregistered.append(name)
    schemas.registered.pop(name, None)


schemas.register_dynamic_tool_declaration = _reg_decl
schemas.unregister_dynamic_tool_declaration = _unreg_decl

dispatch = types.ModuleType("aria.tools.dispatch")
dispatch._REGISTRY = {}


def _register_tool(name, handler, **kwargs):
    dispatch._REGISTRY[name] = handler


dispatch.register_tool = _register_tool

tools_pkg = types.ModuleType("aria.tools")
tools_pkg.__path__ = [os.path.join(PKG, "tools")]
tools_pkg.schemas = schemas
tools_pkg.dispatch = dispatch
sys.modules["aria.tools"] = tools_pkg
sys.modules["aria.tools.schemas"] = schemas
sys.modules["aria.tools.dispatch"] = dispatch

mcp = importlib.import_module("aria.mcp")

CFG = {"transport": "http", "url": "http://127.0.0.1:9/x", "headers": {}}
TOOLS = [SimpleNamespace(name="echo", description="echo",
                         inputSchema={"type": "object", "properties": {}})]


class SlowClient:
    """One instance per actor spawn; initialize sleeps so two
    concurrent connects overlap inside the spawn."""
    instances = []

    def __init__(self, url, headers=None):
        SlowClient.instances.append(self)

    def initialize(self):
        time.sleep(0.5)

    def list_tools(self):
        return list(TOOLS)


async def _live_task_names():
    await asyncio.sleep(0.1)
    return [t.get_name() for t in asyncio.all_tasks()
            if not t.done()]


def _fresh_bridge():
    schemas.registered.clear()
    schemas.unregistered.clear()
    dispatch._REGISTRY.clear()
    SlowClient.instances.clear()
    mcp.HTTPMCPClient = SlowClient
    mcp.get_servers = lambda: {"srv": dict(CFG)}
    return mcp.MCPBridge()


def test_concurrent_connects_spawn_one_actor():
    bridge = _fresh_bridge()
    barrier = threading.Barrier(2)
    results = []

    def _connect():
        barrier.wait(timeout=10)
        results.append(bridge.connect("srv"))

    threads = [threading.Thread(target=_connect) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads), "connect threads hung"
    assert len(results) == 2, results
    assert all(ok for ok, _msg in results), results
    msgs = [msg for _ok, msg in results]
    assert sum("already connected" in m for m in msgs) == 1, msgs
    assert sum(m.startswith("Connected to") for m in msgs) == 1, msgs
    assert len(SlowClient.instances) == 1, \
        f"expected one actor spawn, got {len(SlowClient.instances)}"
    assert list(bridge._sessions) == ["srv"], bridge._sessions
    names = bridge._run(_live_task_names(), timeout=30)
    assert names.count("aria-mcp-srv") == 1, names
    # Disconnect must reach the one actor that was spawned.
    ok, msg = bridge.disconnect("srv")
    assert ok, msg
    assert "srv" not in bridge._sessions
    names = bridge._run(_live_task_names(), timeout=30)
    assert "aria-mcp-srv" not in names, names
    print("ok concurrent_connects_one_actor")


def test_registration_failure_cleans_up_actor():
    bridge = _fresh_bridge()
    orig_register = mcp._register_server_tools

    def _boom(server, tools):
        raise RuntimeError("boom in registration")

    mcp._register_server_tools = _boom
    try:
        ok, msg = bridge.connect("srv")
    finally:
        mcp._register_server_tools = orig_register
    assert not ok, msg
    assert "Could not register tools" in msg, msg
    assert len(SlowClient.instances) == 1, \
        "the failed connect still spawned exactly one actor"
    assert "srv" not in bridge._sessions, bridge._sessions
    names = bridge._run(_live_task_names(), timeout=30)
    assert "aria-mcp-srv" not in names, names
    print("ok registration_failure_cleans_up_actor")
