"""Regression tests (2026-10-30, ChatGPT x ARIA loop pass 9
runner-up): discovered MCP tools executed ungated.

When an MCP server connects, aria/mcp.py registers each remote
tool under a generated name, mcp_<server>__<tool>. Those names do
not exist when the static DESTRUCTIVE_TOOLS and
SAFE_MODE_BLOCKED_TOOLS sets are defined, so neither set can list
them. The dispatcher therefore waved subsequent discovered-tool
calls through under confirm-risky and in safe mode, even though
the remote tool can have arbitrary external effects.

Fix contract pinned here, through the REAL dispatcher:
- config owns the canonical discovered-name classifier and a
  safe-mode helper combining the static set with classification;
- discovered MCP tools are held under confirm-risky, and an
  approve_token replay executes exactly the held call;
- discovered MCP tools are hard-blocked in safe mode, including
  as routine steps, with zero handler calls and no pending entry;
- auto mode still reaches the handler;
- built-in MCP management names are not captured by the classifier
  (setup/connect retain their separate static gates, while
  list/disconnect stay ungated).

Run per-file (own process), like every suite in this repo.
"""
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_mcp_discovered_')
os.environ['HOME'] = _TMPHOME
PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
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

approval = importlib.import_module('aria.approval')
config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')
routines = importlib.import_module('aria.routines')
schemas = importlib.import_module('aria.tools.schemas')

TOOL = 'mcp_probe__do_thing'
_CALLS = []


def _stub_discovered(args):
    args = dict(args or {})
    _CALLS.append(args)
    return 'STUB_DISCOVERED'


dispatch.register_tool(TOOL, _stub_discovered)
schemas.register_dynamic_tool_declaration(
    TOOL,
    {'name': TOOL,
     'description': '[MCP:probe] Do a thing.',
     'parameters': {'type': 'OBJECT',
                    'properties': {'query': {'type': 'STRING'}}}},
    toolkit='mcp')


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    dispatch._REGISTRY.pop(TOOL, None)
    schemas.unregister_dynamic_tool_declaration(TOOL, toolkit='mcp')
    shutil.rmtree(_TMPHOME, ignore_errors=True)


def _save_routine(name, trusted=False):
    d = routines._routines_dir()
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f'{name}.json'), 'w', encoding='utf-8') as f:
        json.dump({'name': name,
                   'created': '2026-10-30T00:00:00+00:00',
                   'trusted': trusted,
                   'steps': [{'tool': TOOL,
                              'args': {'query': 'routine'}}]}, f, indent=2)


def test_classifier_recognizes_only_discovered_shape():
    assert config.parse_discovered_mcp_tool(TOOL) == ('probe', 'do_thing')
    assert config.parse_discovered_mcp_tool(
        'mcp_my_server__read_file') == ('my_server', 'read_file')
    assert config.is_discovered_mcp_tool(TOOL)
    assert config.is_safe_mode_blocked(TOOL)
    assert TOOL not in config.SAFE_MODE_BLOCKED_TOOLS
    assert TOOL not in approval.DESTRUCTIVE_TOOLS

    # Built-in management names have no discovered-name separator.
    # setup/connect remain gated by their static entries instead.
    for name in ('mcp_setup', 'mcp_connect', 'mcp_list_servers',
                 'mcp_disconnect', 'mcp_remove_server'):
        assert config.parse_discovered_mcp_tool(name) is None, name
        assert not config.is_discovered_mcp_tool(name), name
    assert not approval.needs_approval('mcp_list_servers', {})
    assert not approval.needs_approval('mcp_disconnect', {})


def test_description_names_server_and_remote_tool():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    assert approval.needs_approval(TOOL, {'query': 'x'})
    assert approval._describe_tool(TOOL, {'query': 'x'}) == (
        "call MCP tool 'do_thing' on server 'probe' "
        "(tool 'mcp_probe__do_thing')")


def test_safe_mode_blocks_discovered_tool():
    _fresh()
    os.environ['ARIA_SAFE_MODE'] = '1'
    res, _ok = dispatch.execute_tool(TOOL, {'query': 'x'})
    assert res.startswith(f"[Safe mode: '{TOOL}' is disabled."), res
    assert _CALLS == []
    assert approval._PENDING == {}, 'safe-mode block created a pending approval'


def test_confirm_risky_holds_and_approve_replay_executes_exactly():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    args = {'query': 'exact thing'}
    res, _ok = dispatch.execute_tool(TOOL, args)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _CALLS == [], 'held discovered tool executed without approval'
    held = [e for e in approval._PENDING.values() if e['tool'] == TOOL]
    assert held and held[0]['args'] == args, held
    assert held[0]['desc'] == (
        "call MCP tool 'do_thing' on server 'probe' "
        "(tool 'mcp_probe__do_thing')"), held[0]
    token = re.search(r'token=(\S+)\]', res).group(1)
    out = approval.approve_token(token)
    assert _CALLS == [args], _CALLS
    assert 'STUB_DISCOVERED' in out, out


def test_auto_mode_reaches_discovered_handler():
    _fresh()
    res, _ok = dispatch.execute_tool(TOOL, {'query': 'auto'})
    assert 'STUB_DISCOVERED' in res, res
    assert _CALLS == [{'query': 'auto'}]
    assert approval._PENDING == {}, 'auto call created a pending approval'


def test_routine_safe_mode_blocks_discovered_step():
    _fresh()
    _save_routine('mcp_safe', trusted=True)
    os.environ['ARIA_SAFE_MODE'] = '1'
    approval.tool_set_approval_mode('auto')
    out = routines.tool_run_routine('mcp_safe', '{}')
    assert f"Safe mode: '{TOOL}' is disabled" in out, out
    assert _CALLS == [], 'safe-mode routine executed a discovered MCP tool'
    assert approval._PENDING == {}, 'safe-mode routine created a pending approval'


def test_untrusted_routine_confirm_risky_pauses_on_discovered_step():
    _fresh()
    _save_routine('mcp_pause', trusted=False)
    approval.tool_set_approval_mode('confirm-risky')
    out = routines.tool_run_routine('mcp_pause', '{}')
    assert 'PAUSED at step 1' in out, out
    assert _CALLS == [], 'untrusted routine executed a discovered MCP tool'
    held = [e for e in approval._PENDING.values() if e['tool'] == TOOL]
    assert held and held[0]['args'] == {'query': 'routine'}, held
