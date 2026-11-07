"""Pass 27 #30: transport timeouts after delivery are UNVERIFIED, not FAILED.

Before: a phone command that timed out (or whose node disconnected
mid-command) and an MCP call that timed out were both reported as
definite failures — while the phone / server may already have
acted. A FAILED invites a blind retry that could double-execute;
an UNVERIFIED says to check first.

Fix contract pinned here:
PHONE (aria/node_hub.py + aria/phone_node.py):
- a timeout after delivery carries the command id, delivered=True,
  and renders UNVERIFIED with the identity in the text;
- a disconnect while a command is pending likewise renders
  UNVERIFIED;
- a send() rejection (nothing delivered) and no_node stay FAILED;
- a node answering ok=false stays FAILED (the node's own verdict).
MCP (aria/mcp.py):
- call_tool timing out after the request reached the actor renders
  UNVERIFIED with the server + tool identity;
- a call that fails before reaching the server (not connected) or
  that the server itself rejects stays FAILED.
"""
import importlib
import os
import sys
import tempfile
import threading
import time
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_transport_timeout_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir  # noqa: E402
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)

from aria.node_hub import NodeHub, NodeHubError
import aria.phone_node as phone_node
from aria import outcome

mcp = importlib.import_module("aria.mcp")

# ------------------------------------------------------------- phone

def _silent_hub():
    hub = NodeHub()
    hub.register("silent", kind="android", capabilities=[],
                 send=lambda frame: None)
    return hub


def test_phone_timeout_is_unverified_with_command_id(monkeypatch):
    hub = _silent_hub()
    # At the hub level: code + delivered flag + identity.
    try:
        hub.send_command(None, "open_app", {"package": "com.x"},
                         timeout=0.2)
        raise AssertionError("expected timeout")
    except NodeHubError as e:
        assert e.code == "timeout", e.code
        assert e.delivered is True, "the frame WAS delivered"
        assert "command" in str(e) and "open_app" in str(e), str(e)
        assert "may have acted" in str(e), str(e)
    # Through the tool: UNVERIFIED, never FAILED. The tool path
    # uses the hub's 15s default timeout, so wrap send_command
    # to force the short timeout — the rendering path is what is
    # pinned here; the timeout itself is pinned at hub level.
    hub_tool = _silent_hub()
    real_send = hub_tool.send_command
    hub_tool.send_command = lambda node, action, args, timeout=0.2: (
        real_send(node, action, args, timeout=timeout))
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub_tool)
    out = phone_node.tool_phone_list_apps()
    assert outcome.normalize(out).status == "unverified", out
    assert "command" in out and "may have acted" in out, out


def test_phone_disconnect_is_unverified(monkeypatch):
    hub = NodeHub()
    hub.register("phone", kind="android", capabilities=[],
                 send=lambda frame: None)
    results = {}

    def call():
        try:
            hub.send_command(None, "read_screen", {}, timeout=5)
        except NodeHubError as e:
            results["code"] = e.code
            results["delivered"] = e.delivered
            results["text"] = str(e)

    t = threading.Thread(target=call, daemon=True)
    t.start()
    threading.Event().wait(0.2)
    assert hub.unregister("phone") is True
    t.join(3)
    assert results.get("code") == "disconnected", results
    assert results.get("delivered") is True, results
    assert "may have acted" in results.get("text", ""), results
    # Through _send: the same disconnect renders UNVERIFIED.
    hub2 = NodeHub()
    hub2.register("phone", kind="android", capabilities=[],
                  send=lambda frame: None)
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub2)
    send_results = {}

    def call2():
        _data, rendered = phone_node._send(
            "phone_open_app", "open_app", {"package": "com.x"})
        send_results["rendered"] = rendered

    t2 = threading.Thread(target=call2, daemon=True)
    t2.start()
    threading.Event().wait(0.2)
    assert hub2.unregister("phone") is True
    t2.join(3)
    rendered = send_results.get("rendered", "")
    assert outcome.normalize(rendered).status == "unverified", rendered
    assert "may have acted" in rendered, rendered


def test_phone_send_failure_and_no_node_stay_failed(monkeypatch):
    # send() raises: nothing was delivered -> definite FAILED.
    hub = NodeHub()

    def send_boom(frame):
        raise RuntimeError("socket closed")

    hub.register("broken", kind="android", capabilities=[], send=send_boom)
    try:
        hub.send_command(None, "open_app", {})
        raise AssertionError("expected node_error")
    except NodeHubError as e:
        assert e.code == "node_error", e.code
        assert e.delivered is False, "nothing was delivered"
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    out = phone_node.tool_phone_list_apps()
    assert outcome.normalize(out).status == "failed", out
    # No node at all: FAILED.
    monkeypatch.setattr(phone_node.node_hub, "HUB", NodeHub())
    out = phone_node.tool_phone_list_apps()
    assert outcome.normalize(out).status == "failed", out
    assert "no phone node" in out.lower(), out


def test_phone_ok_false_stays_failed(monkeypatch):
    hub = NodeHub()

    def send_no(frame):
        threading.Thread(
            target=lambda: hub.handle_result(
                {"id": frame["id"], "ok": False, "error": "nope"}),
            daemon=True).start()

    hub.register("n", kind="android", capabilities=[], send=send_no)
    monkeypatch.setattr(phone_node.node_hub, "HUB", hub)
    out = phone_node.tool_phone_list_apps()
    assert outcome.normalize(out).status == "failed", out
    assert "nope" in out, out


# ------------------------------------------------------------- MCP

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
        if name == "boom":
            raise RuntimeError("server-side failure")
        return {"result": {"content": [{"text": name}]}}


def _bridge_with_fake_actor():
    mcp.HTTPMCPClient = FakeHTTPClient
    CALLS.clear()
    return mcp.MCPBridge()


def test_mcp_timeout_is_unverified_and_server_failure_is_failed():
    bridge = _bridge_with_fake_actor()
    # Spawn the actor on the bridge loop, then call the SYNC
    # call_tool from this thread (calling it from inside a
    # coroutine on the loop thread would stall the loop itself).
    task, queue, tools = bridge._run(bridge._spawn_actor(
        "t", {"transport": "http", "url": "http://127.0.0.1:9/x",
              "headers": {}}), timeout=25)
    entry = {"config": {"transport": "http"}, "task": task,
             "queue": queue, "tools": [], "aria_names": []}
    bridge._sessions["t"] = entry
    try:
        # Slow call: the actor picks it up and executes it, but
        # the caller times out waiting -> UNVERIFIED, never FAILED.
        slow = bridge.call_tool("t", "slow", {}, timeout=0.2)
        time.sleep(1.2)  # let the actor finish "slow"
        # Server-side rejection: definite FAILED.
        boom = bridge.call_tool("t", "boom", {}, timeout=5)
    finally:
        bridge._run(bridge._stop_actor(entry), timeout=15)
    assert "slow" in CALLS, CALLS  # the server DID act on "slow"
    assert outcome.normalize(slow).status == "unverified", slow
    assert "may have acted" in slow, slow
    assert "slow" in slow and "'t'" in slow, slow
    assert outcome.normalize(boom).status == "failed", boom
    assert "server-side failure" in boom, boom


def test_mcp_not_connected_stays_failed():
    bridge = _bridge_with_fake_actor()
    out = bridge.call_tool("ghost", "anything", {}, timeout=5)
    assert outcome.normalize(out).status == "failed", out
    assert "not connected" in out, out
