"""Pass 27 #4: an empty discovered MCP tool name must not escape
approval and safe-mode classification.

A server advertising a tool with an empty remote name used to
register as mcp_<server>__, which the policy regex (requiring a
non-empty tool part) did not classify as discovered — the handler
then ran ungated in safe mode. Registration now refuses invalid
remote names, and the classifier treats a bare mcp_<server>__
name as discovered anyway (defense in depth).
"""
import importlib
import os
import sys
import tempfile
import types
from types import SimpleNamespace

_TMPHOME = tempfile.mkdtemp(prefix="aria_mcp_empty_name_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir  # noqa: E402
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

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
config = importlib.import_module("aria.config")


def _fresh():
    schemas.registered.clear()
    schemas.unregistered.clear()
    dispatch._REGISTRY.clear()


def _tool(name):
    return SimpleNamespace(name=name, description="probe",
                            inputSchema={"type": "object"})


def test_empty_remote_name_is_not_registered():
    _fresh()
    names = mcp._register_server_tools(
        "probe", [_tool(""), _tool("echo")])
    assert names == ["mcp_probe__echo"], names
    assert "mcp_probe__" not in dispatch._REGISTRY
    assert "mcp_probe__" not in schemas.registered
    assert "mcp_probe__echo" in dispatch._REGISTRY


def test_illegal_remote_names_are_not_registered():
    _fresh()
    names = mcp._register_server_tools(
        "probe", [_tool("do thing!"), _tool("a/b"), _tool(None),
                  _tool("ok_name-1")])
    assert names == ["mcp_probe__ok_name-1"], names


def test_bare_server_name_still_classifies_as_discovered():
    assert config.parse_discovered_mcp_tool("mcp_probe__") == ("probe", "")
    assert config.is_discovered_mcp_tool("mcp_probe__")
    assert config.is_safe_mode_blocked("mcp_probe__")
    # Built-in management names are still not discovered.
    for name in ("mcp_setup", "mcp_connect", "mcp_list_servers"):
        assert config.parse_discovered_mcp_tool(name) is None, name
        assert not config.is_discovered_mcp_tool(name), name
