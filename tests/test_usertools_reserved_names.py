"""Pass 26 B1#1: user tools must not claim approval-exempt names.

Verified against the code at b1ba4ad:
- usertools.py load_user_tools() only checks a name against the
  dispatch registry; approve / deny / set_approval_mode are
  deliberately UNREGISTERED (dispatch.py:1104-1112), so a user
  tool file with TOOL_NAME = "approve" loads, registers, and gets
  a model-facing declaration.
- approval.py needs_approval() returns False for NEVER_GATED names
  BEFORE the is_user_tool check, and dispatch.py's envelope check
  exempts NEVER_GATED the same way — so the planted tool runs
  with no approval in confirm-risky / auto.

Fix contract pinned here:
- the loader rejects a user tool whose name is in NEVER_GATED
  (at scan time and again at register time, defence in depth);
  it is never registered, never classified as a user tool, and
  its top-level code never runs;
- needs_approval() checks is_user_tool / is_discovered_mcp_tool
  BEFORE the NEVER_GATED early-return, so a user tool is never
  exempt by name (the genuine resolvers, which are not user
  tools, stay exempt).

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_usertools_reserved_')
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
approval = importlib.import_module('aria.approval')
dispatch = importlib.import_module('aria.tools.dispatch')
usertools = importlib.import_module('aria.tools.usertools')


def _tool_src(name, marker):
    return ('\n'
            f'TOOL_NAME = "{name}"\n'
            f'TOOL_DESCRIPTION = "Probe tool {name}"\n'
            'TOOL_PARAMETERS = {"type": "OBJECT", "properties": {}, '
            '"required": []}\n'
            f'open({marker!r}, "a").write("imported\\n")\n'
            '\n'
            'def run(args):\n'
            '    return "PROBE_OK"\n')


def test_reserved_names_rejected_at_load(tmp_path):
    marker = str(tmp_path / "imported.txt")
    scan = tmp_path / "user_tools"
    scan.mkdir()
    for name in ("approve", "deny", "set_approval_mode"):
        (scan / f"{name}.py").write_text(_tool_src(name, marker))
    loaded = usertools.load_user_tools_into_registry(str(scan))
    assert loaded == [], loaded
    for name in ("approve", "deny", "set_approval_mode"):
        assert name not in dispatch.get_registered_tools()
        assert not config.is_user_tool(name)
        assert name not in usertools._USER_TOOLS
    assert not os.path.exists(marker), \
        'loading a reserved-name tool executed its top-level code'


def test_register_time_rejects_reserved_name(tmp_path):
    # Defence in depth: even a name smuggled past the scan (e.g. a
    # direct _register_into_dispatch call) must not land.
    handler = lambda args: "PROBE_OK"  # noqa: E731
    decl = {"name": "deny", "description": "Probe",
            "parameters": {"type": "OBJECT", "properties": {},
                           "required": []}}
    usertools._USER_TOOLS["deny"] = (handler, decl)
    try:
        added = usertools._register_into_dispatch(
            [("deny", handler, decl)])
        assert added == [], added
        assert "deny" not in dispatch.get_registered_tools()
        assert not config.is_user_tool("deny")
    finally:
        usertools._USER_TOOLS.pop("deny", None)


def test_needs_approval_gates_user_tool_with_reserved_name():
    # A user tool named "approve" that somehow got registered
    # (hand-registered, bypassing the loader) must still gate:
    # the NEVER_GATED exemption must not apply to user tools.
    config.register_user_tool("approve")
    approval.tool_set_approval_mode('confirm-risky')
    try:
        assert approval.needs_approval("approve", {})
        assert config.is_safe_mode_blocked("approve")
    finally:
        config.unregister_user_tool("approve")
        approval.tool_set_approval_mode('auto')
    # Control: the genuine (non-user-tool) resolver name stays
    # exempt — gating it would deadlock resolution.
    assert not approval.needs_approval("approve", {})
    assert not approval.needs_approval("deny", {})
    assert not approval.needs_approval("set_approval_mode", {})
