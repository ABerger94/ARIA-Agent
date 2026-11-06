"""Reloading user tools must keep their model-facing schemas (pass 25 P2#9).

tool_reload_user_tools unregistered the declarations and then
re-registered only the dispatch handlers, so a reloaded user tool
stayed dispatch-callable but invisible to the model until restart.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_usertools_reload_')
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

config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')
schemas = importlib.import_module('aria.tools.schemas')
usertools = importlib.import_module('aria.tools.usertools')

_TOOL_SRC = '''
TOOL_NAME = "probe_tool"
TOOL_DESCRIPTION = "Probe tool for reload test"
TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, "required": []}

def run(args):
    return "PROBE_OK"
'''


def test_reload_keeps_declaration(tmp_path, monkeypatch):
    scan = tmp_path / "user_tools"
    scan.mkdir()
    (scan / "probe_tool.py").write_text(_TOOL_SRC)
    monkeypatch.setattr(usertools, "_SCAN_DIR", str(scan))
    out = usertools.tool_reload_user_tools()
    assert "probe_tool" in out, out
    assert "probe_tool" in dispatch.get_registered_tools()
    decl = schemas._DYNAMIC_DECLS.get("probe_tool")
    assert decl is not None, "declaration missing after reload"
    assert decl["description"] == "Probe tool for reload test"
    # A second reload (the unregister-then-register path) keeps it too.
    out = usertools.tool_reload_user_tools()
    assert schemas._DYNAMIC_DECLS.get("probe_tool") is not None
    assert "probe_tool" in dispatch.get_registered_tools()
