"""MCP registration must terminate for long colliding tool names
(pass 25 P2#8).

The old de-dupe loop appended "_2"/"_3" after truncating to 64
chars, so for a base already >= 64 chars the suffix was truncated
away and every candidate equalled the first name — an infinite
loop on connect. Two tools from one server sharing a 64-char
prefix now register under distinct names.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_mcp_longname_')
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

mcp = importlib.import_module('aria.mcp')


class _Tool:
    def __init__(self, name):
        self.name = name
        self.inputSchema = {"type": "object", "properties": {}}
        self.description = "probe"


def test_long_colliding_names_register_distinctly():
    # Two tools whose "mcp_<server>__<name>" forms share the first
    # 64 chars: the old loop never terminated here.
    prefix = "a" * 70
    tools = [_Tool(prefix + "_one"), _Tool(prefix + "_two")]
    names = mcp._register_server_tools("srv", tools)
    assert len(names) == 2
    assert len(set(names)) == 2
    assert all(len(n) <= 64 for n in names), names
    assert names[0] != names[1]
    assert names[1].endswith("_2"), names


def test_short_names_unchanged():
    tools = [_Tool("do_thing"), _Tool("do_thing")]
    names = mcp._register_server_tools("srv", tools)
    assert names == ["mcp_srv__do_thing", "mcp_srv__do_thing_2"], names
