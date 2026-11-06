"""Dead MCP sessions must not report "connected" or block reconnect,
and disconnect must remove the dispatch handler (pass 26 B4#2).

connect() returned "already connected" for a dead entry with no
liveness check; status() reported connected on mere existence;
_unregister_server_tools removed only the schema declaration —
the dispatch handler registered via register_tool stayed in
dispatch._REGISTRY, dispatchable after disconnect.
"""

import importlib
import os
import sys
import tempfile
import types
from types import SimpleNamespace

_TMPHOME = tempfile.mkdtemp(prefix="aria_mcp_dead_session_")
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


def _call_tool(name, args=None):
    if name not in dispatch._REGISTRY:
        raise KeyError(f"unknown tool: {name}")
    return dispatch._REGISTRY[name](args or {})


dispatch.call_tool = _call_tool

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
                         inputSchema={"type": "object"})]


class _TaskStub:
    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done

    def cancel(self):
        self._done = True


def _fresh_bridge():
    schemas.registered.clear()
    schemas.unregistered.clear()
    dispatch._REGISTRY.clear()
    bridge = mcp.MCPBridge()
    mcp.get_servers = lambda: {"dead": dict(CFG)}
    return bridge


def _register_echo(bridge):
    names = mcp._register_server_tools("dead", TOOLS)
    assert names == ["mcp_dead__echo"]
    assert "mcp_dead__echo" in dispatch._REGISTRY
    return names


def test_status_reports_dead_session_disconnected():
    bridge = _fresh_bridge()
    names = _register_echo(bridge)
    bridge._sessions["dead"] = {
        "config": dict(CFG), "task": _TaskStub(done=True),
        "queue": None, "tools": TOOLS, "aria_names": names}
    rows = bridge.status()
    assert rows == [{"name": "dead", "transport": "http", "enabled": True,
                     "connected": False, "tools": 0}], rows
    print("ok status_dead_disconnected")


def test_connect_reconnects_dead_session():
    bridge = _fresh_bridge()
    names = _register_echo(bridge)
    bridge._sessions["dead"] = {
        "config": dict(CFG), "task": _TaskStub(done=True),
        "queue": None, "tools": TOOLS, "aria_names": names}
    spawned = []

    def fake_run(coro, timeout=None):
        coro.close()
        spawned.append(True)
        return (_TaskStub(done=False), object(), TOOLS)

    bridge._run = fake_run
    ok, msg = bridge.connect("dead")
    assert ok, msg
    assert "already connected" not in msg, msg
    assert spawned, "connect must spawn a fresh actor for a dead entry"
    assert "mcp_dead__echo" in schemas.unregistered, \
        "stale tools must be unregistered before reconnect"
    print("ok connect_reconnects_dead")


def test_connect_live_session_still_already_connected():
    bridge = _fresh_bridge()
    names = _register_echo(bridge)
    bridge._sessions["dead"] = {
        "config": dict(CFG), "task": _TaskStub(done=False),
        "queue": None, "tools": TOOLS, "aria_names": names}
    spawned = []

    def fake_run(coro, timeout=None):
        coro.close()
        spawned.append(True)
        return (_TaskStub(done=False), object(), TOOLS)

    bridge._run = fake_run
    ok, msg = bridge.connect("dead")
    assert ok and "already connected" in msg, msg
    assert not spawned, "a live session must not respawn"
    print("ok connect_live_already_connected")


def test_disconnect_removes_dispatch_handler():
    bridge = _fresh_bridge()
    names = _register_echo(bridge)
    bridge._sessions["dead"] = {
        "config": dict(CFG), "task": _TaskStub(done=False),
        "queue": None, "tools": TOOLS, "aria_names": names}

    def fake_run(coro, timeout=None):
        coro.close()
        return None

    bridge._run = fake_run
    ok, msg = bridge.disconnect("dead")
    assert ok, msg
    assert "mcp_dead__echo" not in dispatch._REGISTRY
    assert "mcp_dead__echo" in schemas.unregistered
    try:
        dispatch.call_tool("mcp_dead__echo", {})
    except KeyError as exc:
        assert "unknown tool" in str(exc)
    else:
        raise AssertionError("disconnected tool still dispatchable")
    print("ok disconnect_removes_handler")
