"""tool_mcp_connect must not report a failed connect as success
(pass 28 #31).

The named path discarded bridge.connect's ok flag and returned
the message bare — "Could not connect to MCP server 'x': ..."
normalized as DONE. The connect-all path joined per-server
"FAIL ..." lines the same way, so connecting to nothing at all
also read as success.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix="aria_mcp_connect_fail_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
_pkg = types.ModuleType("aria")
_pkg.__path__ = [PKG]
sys.modules["aria"] = _pkg
_tools_pkg = types.ModuleType("aria.tools")
_tools_pkg.__path__ = [PKG + "/tools"]
sys.modules["aria.tools"] = _tools_pkg
_ps = types.ModuleType("psutil")
_ps.cpu_percent = lambda interval=0: 0.0
_ps.virtual_memory = lambda: types.SimpleNamespace(percent=0.0)
_ps.disk_usage = lambda p: types.SimpleNamespace(percent=0.0)
_ps.sensors_battery = lambda: None
sys.modules.setdefault("psutil", _ps)
from aria import data_dir as _data_dir  # noqa: E402
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

builtins = importlib.import_module("aria.tools.builtins")
mcp = importlib.import_module("aria.mcp")
outcome = importlib.import_module("aria.outcome")


class _FailingBridge:
    def connect(self, name, expected_config_digest=None):
        return False, (f"Could not connect to MCP server '{name}': "
                       "connection refused")


class _MixedBridge:
    def connect(self, name, expected_config_digest=None):
        if name == "good":
            return True, f"Connected to MCP server '{name}' (stdio); loaded 1 tools: t"
        return False, (f"Could not connect to MCP server '{name}': "
                       "connection refused")


def test_named_connect_failure_is_typed_failed(monkeypatch):
    monkeypatch.setattr(mcp, "get_bridge", lambda: _FailingBridge())
    res = builtins.tool_mcp_connect("ghost")
    assert outcome.normalize(res).status == "failed", res
    assert "connection refused" in res
    print("ok named_connect_failure_is_typed_failed")


def test_connect_all_failure_is_typed_failed(monkeypatch):
    monkeypatch.setattr(mcp, "get_bridge", lambda: _FailingBridge())
    monkeypatch.setattr(mcp, "get_servers", lambda: {
        "a": {"transport": "stdio", "enabled": True},
        "b": {"transport": "http", "enabled": True}})
    res = builtins.tool_mcp_connect("")
    assert outcome.normalize(res).status == "failed", res
    assert "FAIL" in res
    print("ok connect_all_failure_is_typed_failed")


def test_connect_all_partial_success_stays_reported(monkeypatch):
    monkeypatch.setattr(mcp, "get_bridge", lambda: _MixedBridge())
    monkeypatch.setattr(mcp, "get_servers", lambda: {
        "good": {"transport": "stdio", "enabled": True},
        "bad": {"transport": "http", "enabled": True}})
    res = builtins.tool_mcp_connect("")
    assert "OK" in res and "FAIL" in res, res
    print("ok connect_all_partial_success_stays_reported")


def test_named_connect_success_stays_done(monkeypatch):
    class _OkBridge:
        def connect(self, name, expected_config_digest=None):
            return True, (f"Connected to MCP server '{name}' (stdio); "
                          "loaded 1 tools: t")

    monkeypatch.setattr(mcp, "get_bridge", lambda: _OkBridge())
    res = builtins.tool_mcp_connect("good")
    assert outcome.normalize(res).status == "done", res
    print("ok named_connect_success_stays_done")
