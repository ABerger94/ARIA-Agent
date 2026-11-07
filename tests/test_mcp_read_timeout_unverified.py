"""Real HTTP MCP read timeouts must not report definite failure
(pass 28 #10).

requests.ReadTimeout is an OSError, NOT builtin TimeoutError, so
MCPBridge.call_tool's `except TimeoutError` (pass 27 #30) missed
it: a read timeout — the request was sent, the server may have
acted, only the reply was lost — fell to the generic handler and
came back FAILED, inviting a blind retry that could double-execute.
The fix converts ReadTimeout to builtin TimeoutError at the HTTP
transport boundary (_post), so the existing UNVERIFIED path
catches it. ConnectTimeout is deliberately NOT converted: the
request was never sent, so a definite failure is correct there.

Run per-file (own process), like every suite in this repo.
"""

import importlib
import os
import sys
import tempfile
import types

import pytest
import requests

_TMPHOME = tempfile.mkdtemp(prefix="aria_mcp_read_timeout_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir  # noqa: E402
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

mcp = importlib.import_module("aria.mcp")
from aria import outcome  # noqa: E402

_RealHTTPMCPClient = mcp.HTTPMCPClient

_RAISE = {"exc": None}


class FakeHTTPClient:
    """Mirrors test_mcp_timeout_cancel's fake, but the tool call
    goes through the REAL _post with a raising session, so the
    ReadTimeout -> TimeoutError conversion at the transport
    boundary is exercised end to end (client -> actor -> bridge)."""

    def __init__(self, url, headers=None):
        self.url = url
        self._real = _RealHTTPMCPClient(url, headers=headers)
        self._real._get_session = lambda: _RaisingSession(_RAISE["exc"])

    def initialize(self):
        return {"result": {}}

    def list_tools(self):
        return []

    def call_tool(self, name, arguments):
        self._real._post({"jsonrpc": "2.0", "id": 1,
                          "method": "tools/call",
                          "params": {"name": name,
                                     "arguments": arguments}})
        return {"result": {"content": [{"text": name}]}}


class _RaisingSession:
    def __init__(self, exc):
        self._exc = exc

    def post(self, *a, **k):
        raise self._exc


def _bridge_with_actor():
    mcp.HTTPMCPClient = FakeHTTPClient
    bridge = mcp.MCPBridge()
    task, queue, tools = bridge._run(
        bridge._spawn_actor(
            "t", {"transport": "http", "url": "http://127.0.0.1:9/x",
                  "headers": {}}),
        timeout=30)
    entry = {"config": {"transport": "http"}, "task": task,
             "queue": queue, "tools": [], "aria_names": []}
    bridge._sessions["t"] = entry
    return bridge, entry


def _stop(bridge, entry):
    bridge._run(bridge._stop_actor(entry), timeout=20)


def test_post_read_timeout_converts_to_timeout_error():
    client = _RealHTTPMCPClient("http://127.0.0.1:9/x")
    client._get_session = lambda: _RaisingSession(
        requests.ReadTimeout("HTTPConnectionPool: Read timed out."))
    with pytest.raises(TimeoutError):
        client._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})


def test_post_connect_timeout_is_not_converted():
    # The request was never sent — a definite failure, not an
    # ambiguous timeout. _post must leave it alone.
    client = _RealHTTPMCPClient("http://127.0.0.1:9/x")
    client._get_session = lambda: _RaisingSession(
        requests.ConnectTimeout("Connection timed out."))
    with pytest.raises(requests.ConnectTimeout):
        client._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})


def test_bridge_read_timeout_is_unverified_not_failed():
    _RAISE["exc"] = requests.ReadTimeout(
        "HTTPConnectionPool: Read timed out.")
    bridge, entry = _bridge_with_actor()
    try:
        res = bridge.call_tool("t", "do_thing", {}, timeout=10)
    finally:
        _stop(bridge, entry)
    assert outcome.normalize(res).status == "unverified", res
    assert "may have acted" in res, res


def test_bridge_connect_timeout_is_still_failed():
    _RAISE["exc"] = requests.ConnectTimeout("Connection timed out.")
    bridge, entry = _bridge_with_actor()
    try:
        res = bridge.call_tool("t", "do_thing", {}, timeout=10)
    finally:
        _stop(bridge, entry)
    assert outcome.normalize(res).status == "failed", res
