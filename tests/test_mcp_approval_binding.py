"""Pass 27 #19: MCP approvals must be bound to the server config
and tool registration they were granted against.

Before: a pending mcp_connect stored only the server name and
replay re-read the CURRENT config — a command swapped after the
park (e.g. npx -> a malicious binary) executed under the old
approval. Likewise a discovered tool's approval survived a
reconnect that changed the server's tool set.

Fix contract pinned here:
- freeze_mcp_connect_args binds the current config digest into
  parked mcp_connect args (ValueError for an unknown server);
  connect() checks it BEFORE spawning the server command and
  refuses on mismatch;
- connect() stores a registration digest (tool names +
  descriptions + input schemas) on the session;
- freeze_discovered_tool_args binds that digest into parked
  discovered-tool args (ValueError when not connected);
- MCPBridge.call_tool verifies the digest before invoking the
  actor, refuses typed FAILED on mismatch/unavailable, and never
  forwards the binding key to the server;
- calls with no digest (auto mode) run as before.
"""
import asyncio
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_mcp_bind19_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir
_data_dir._ROOT = os.path.join(_TMPHOME, 'ARIA')
tools_pkg = types.ModuleType('aria.tools')
tools_pkg.__path__ = [PKG + '/tools']
sys.modules['aria.tools'] = tools_pkg
agent_pkg = types.ModuleType('aria.agent')
agent_pkg.__path__ = [PKG + '/agent']
sys.modules['aria.agent'] = agent_pkg
_ps = types.ModuleType('psutil')
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules['psutil'] = _ps
for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware',
              'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)

outcome = importlib.import_module('aria.outcome')
config = importlib.import_module('aria.config')
mcp = importlib.import_module('aria.mcp')
dispatch = importlib.import_module('aria.tools.dispatch')

CFG = {"transport": "http", "url": "http://127.0.0.1:9/x",
       "headers": {"Authorization": "Bearer SECRET"}}
TOOLS = [types.SimpleNamespace(
    name="do_thing", description="Do a thing",
    inputSchema={"type": "object",
                 "properties": {"q": {"type": "string"}}})]


class _FakeTask:
    def done(self):
        return False


def _servers():
    return {"srv": dict(CFG)}


def _bridge_with_session(monkeypatch):
    """A bridge whose connect() is stubbed at the spawn boundary."""
    bridge = mcp.MCPBridge()
    monkeypatch.setattr(mcp, "get_servers", _servers)
    monkeypatch.setattr(mcp, "get_bridge", lambda: bridge)
    spawns = []

    def _fake_run(coro, timeout=30.0):
        coro.close()
        spawns.append(True)
        return (_FakeTask(), None, list(TOOLS))

    monkeypatch.setattr(bridge, "_run", _fake_run)
    monkeypatch.setattr(mcp, "_register_server_tools",
                        lambda server, tools: ["mcp_srv__do_thing"])
    ok, msg = bridge.connect("srv")
    assert ok, msg
    return bridge, spawns


def test_config_digest_stable_and_sensitive(monkeypatch):
    monkeypatch.setattr(mcp, "get_servers", _servers)
    d1 = mcp.server_config_digest("srv")
    assert d1 == mcp.server_config_digest("srv")
    changed = dict(CFG)
    changed["headers"] = {"Authorization": "Bearer OTHER"}
    assert mcp.server_config_digest("srv", changed) != d1
    # A hash only: the secret never appears in the digest.
    assert "SECRET" not in d1


def test_freeze_connect_binds_digest_and_rejects_unknown(monkeypatch):
    monkeypatch.setattr(mcp, "get_servers", _servers)
    bound = mcp.freeze_mcp_connect_args("srv")
    assert bound["_config_digest"] == mcp.server_config_digest("srv")
    try:
        mcp.freeze_mcp_connect_args("nope")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown server")


def test_connect_refuses_stale_config_before_spawning(monkeypatch):
    bridge, spawns = _bridge_with_session(monkeypatch)
    bridge.disconnect("srv")
    before = len(spawns)
    # Reconfigure after the approval was granted.
    monkeypatch.setattr(mcp, "get_servers",
                        lambda: {"srv": {**CFG, "url": "http://evil.test/x"}})
    stale = mcp.server_config_digest("srv", CFG)
    ok, msg = bridge.connect("srv", expected_config_digest=stale)
    assert not ok, msg
    assert "changed since approval" in msg, msg
    assert len(spawns) == before, "stale approval spawned the server"
    assert "srv" not in bridge._sessions


def test_connect_with_matching_digest_proceeds(monkeypatch):
    bridge, spawns = _bridge_with_session(monkeypatch)
    assert len(spawns) == 1
    digest = bridge.registration_digest("srv")
    assert digest == mcp._registration_digest(TOOLS)
    assert bridge.registration_digest("nope") is None


def test_registration_digest_sensitive_to_schema(monkeypatch):
    d1 = mcp._registration_digest(TOOLS)
    changed = [types.SimpleNamespace(
        name="do_thing", description="Do a thing",
        inputSchema={"type": "object",
                     "properties": {"q": {"type": "integer"}}})]
    assert mcp._registration_digest(changed) != d1


def test_freeze_discovered_binds_registration(monkeypatch):
    bridge, _spawns = _bridge_with_session(monkeypatch)
    bound = mcp.freeze_discovered_tool_args(
        "mcp_srv__do_thing", {"q": "z"})
    assert bound["_registration_digest"] == \
        bridge.registration_digest("srv")
    assert bound["q"] == "z"
    # Not connected: no registration to bind — the args pass
    # through unbound rather than refusing to park.
    unbound = mcp.freeze_discovered_tool_args("mcp_ghost__x", {"q": "z"})
    assert "_registration_digest" not in unbound and unbound["q"] == "z"


def test_call_tool_refuses_stale_registration(monkeypatch):
    bridge, _spawns = _bridge_with_session(monkeypatch)
    runs = []

    async def _fake_actor(name, tool, args, timeout):
        runs.append(dict(args))
        return types.SimpleNamespace(
            isError=False,
            content=[types.SimpleNamespace(type="text", text="CALLED")])

    monkeypatch.setattr(bridge, "_actor_call", _fake_actor)
    monkeypatch.setattr(bridge, "_run",
                        lambda coro, timeout=130.0: asyncio.run(coro))
    good = mcp.freeze_discovered_tool_args("mcp_srv__do_thing", {})
    # Matching digest: executes, and the binding key never reaches
    # the actor.
    out = bridge.call_tool("srv", "do_thing", good)
    assert "CALLED" in out, out
    assert runs and "_registration_digest" not in runs[0], runs
    # Stale digest: refused typed FAILED, actor never invoked.
    stale = {"_registration_digest": "0" * 64, "q": "z"}
    out = bridge.call_tool("srv", "do_thing", stale)
    assert outcome.normalize(out).status == "failed", out
    assert "changed since approval" in out, out
    assert len(runs) == 1, runs


def test_dispatch_freeze_binds_both(monkeypatch):
    bridge, _spawns = _bridge_with_session(monkeypatch)
    gate = dispatch._freeze_park_args("mcp_connect", {"name": "srv"})
    assert gate["_config_digest"] == mcp.server_config_digest("srv")
    gate = dispatch._freeze_park_args("mcp_srv__do_thing", {"q": "z"})
    assert gate["_registration_digest"] == \
        bridge.registration_digest("srv")
    # Connect-all has no single config to bind.
    gate = dispatch._freeze_park_args("mcp_connect", {})
    assert "_config_digest" not in gate


def test_freeze_discovered_binds_config_even_when_disconnected(monkeypatch):
    # Pass 28 #5: a discovered-tool approval must bind the server
    # CONFIG, not only the tool registration — a reconnect to a
    # reconfigured server with the same tool set used to ride the
    # old approval. The config lives on disk, so it binds even
    # when the server is not connected.
    bridge, _spawns = _bridge_with_session(monkeypatch)
    bound = mcp.freeze_discovered_tool_args(
        "mcp_srv__do_thing", {"q": "z"})
    assert bound["_config_digest"] == mcp.server_config_digest("srv")
    assert bound["_registration_digest"] == \
        bridge.registration_digest("srv")
    bridge.disconnect("srv")
    assert bridge.registration_digest("srv") is None
    parked_offline = mcp.freeze_discovered_tool_args(
        "mcp_srv__do_thing", {"q": "z"})
    assert parked_offline["_config_digest"] == \
        mcp.server_config_digest("srv")
    assert "_registration_digest" not in parked_offline


def test_call_tool_refuses_stale_config(monkeypatch):
    # Pass 28 #5: the call-time check enforces the config digest
    # alongside the registration digest; the binding keys never
    # reach the actor.
    bridge, _spawns = _bridge_with_session(monkeypatch)
    runs = []

    async def _fake_actor(name, tool, args, timeout):
        runs.append(dict(args))
        return types.SimpleNamespace(
            isError=False,
            content=[types.SimpleNamespace(type="text", text="CALLED")])

    monkeypatch.setattr(bridge, "_actor_call", _fake_actor)
    monkeypatch.setattr(bridge, "_run",
                        lambda coro, timeout=130.0: asyncio.run(coro))
    good = mcp.freeze_discovered_tool_args("mcp_srv__do_thing", {})
    out = bridge.call_tool("srv", "do_thing", good)
    assert "CALLED" in out, out
    assert runs and "_config_digest" not in runs[0], runs
    assert "_registration_digest" not in runs[0], runs
    # Reconfigure the server after the approval was granted; the
    # tool registration is unchanged, so only the config check
    # can refuse.
    monkeypatch.setattr(mcp, "get_servers",
                        lambda: {"srv": {**CFG, "url": "http://evil.test/x"}})
    out = bridge.call_tool("srv", "do_thing", good)
    assert outcome.normalize(out).status == "failed", out
    assert "changed since approval" in out, out
    assert len(runs) == 1, runs


def test_freeze_connect_all_binds_server_set(monkeypatch):
    # Pass 28 #9: a connect-all approval binds the ENABLED server
    # SET, not just one config — a server added, removed,
    # disabled, or reconfigured after the park changes the digest.
    bridge, _spawns = _bridge_with_session(monkeypatch)
    gate = dispatch._freeze_park_args("mcp_connect", {})
    assert gate["_servers_digest"] == mcp.servers_digest()
    assert "_config_digest" not in gate
    changed = {"srv": {**CFG, "url": "http://evil.test/x"}}
    assert mcp.servers_digest(changed) != mcp.servers_digest()
    added = {"srv": dict(CFG), "other": dict(CFG)}
    assert mcp.servers_digest(added) != mcp.servers_digest()
    disabled = {"srv": {**CFG, "enabled": False}}
    assert mcp.servers_digest(disabled) != mcp.servers_digest()
    assert mcp.servers_digest({}) != mcp.servers_digest()


def test_connect_all_refuses_changed_server_set(monkeypatch):
    # Pass 28 #9: the replay verifies the set BEFORE spawning
    # anything — a stale approval connects nothing.
    bridge, spawns = _bridge_with_session(monkeypatch)
    gate = dispatch._freeze_park_args("mcp_connect", {})
    digest = gate["_servers_digest"]
    # Matching set: proceeds (srv is already connected by the
    # harness; connect-all reports it).
    out, _ok = dispatch.execute_tool(
        "mcp_connect", {"_servers_digest": digest}, preauthorized=True)
    assert "srv" in out, out
    before = len(spawns)
    # A server added after the park: refused, nothing spawned.
    # (Reset the per-turn duplicate guard: the second call carries
    # the same args, and the guard would otherwise return the
    # first call's result without reaching the handler.)
    dispatch.reset_turn_state()
    monkeypatch.setattr(mcp, "get_servers",
                        lambda: {"srv": dict(CFG), "evil": dict(CFG)})
    out, _ok = dispatch.execute_tool(
        "mcp_connect", {"_servers_digest": digest}, preauthorized=True)
    assert outcome.normalize(out).status == "failed", out
    assert "changed since approval" in out, out
    assert len(spawns) == before, "stale approval spawned a server"
    assert "evil" not in bridge._sessions
