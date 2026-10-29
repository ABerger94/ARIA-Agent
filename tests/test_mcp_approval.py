"""Regression tests (2026-10-28, ChatGPT x ARIA loop finding #1):
MCP was an unapproved command-execution path — its handler spawned
a model-chosen process through stdio_client before any MCP handshake
(aria/mcp.py), setup persisted the server enabled so startup
autoconnect re-ran it, and neither mcp_setup nor mcp_connect was in
DESTRUCTIVE_TOOLS or SAFE_MODE_BLOCKED_TOOLS, so the dispatcher
checked the outer name and waved both through.

Fix contract pinned here, through the REAL dispatcher:
- mcp_setup / mcp_connect are blocked in safe mode (zero handler
  calls, no pending approval created);
- under confirm-risky both are held for approval (zero handler
  calls, one pending entry with the exact call), and an
  approve_token replay executes the exact held call;
- in auto mode both reach the handler (control);
- mcp_list_servers stays ungated (read-only path, scope pin).

Run per-file (own process), like every suite in this repo.
"""
import os
import re
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_mcp_approval_')
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
import importlib
approval = importlib.import_module('aria.approval')
config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')
builtins = importlib.import_module('aria.tools.builtins')

_SETUP_CALLS = []
_CONNECT_CALLS = []
_LIST_CALLS = []
_ORIG_SETUP = builtins.tool_mcp_setup
_ORIG_CONNECT = builtins.tool_mcp_connect
_ORIG_LIST = builtins.tool_mcp_list_servers


def _stub_setup(name='', command='', args='', url='', env='', headers=''):
    _SETUP_CALLS.append({'name': name, 'command': command, 'args': args,
                         'url': url, 'env': env, 'headers': headers})
    return 'STUB_SETUP'


def _stub_connect(name=''):
    _CONNECT_CALLS.append({'name': name})
    return 'STUB_CONNECT'


def _stub_list():
    _LIST_CALLS.append(True)
    return 'STUB_LIST'


builtins.tool_mcp_setup = _stub_setup
builtins.tool_mcp_connect = _stub_connect
builtins.tool_mcp_list_servers = _stub_list


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _SETUP_CALLS.clear()
    _CONNECT_CALLS.clear()
    _LIST_CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    builtins.tool_mcp_setup = _ORIG_SETUP
    builtins.tool_mcp_connect = _ORIG_CONNECT
    builtins.tool_mcp_list_servers = _ORIG_LIST


def test_policy_sets_contain_setup_and_connect_only():
    assert 'mcp_setup' in approval.DESTRUCTIVE_TOOLS
    assert 'mcp_connect' in approval.DESTRUCTIVE_TOOLS
    assert 'mcp_setup' in config.SAFE_MODE_BLOCKED_TOOLS
    assert 'mcp_connect' in config.SAFE_MODE_BLOCKED_TOOLS
    # Scope pin: the read-only / teardown MCP paths stay ungated.
    assert 'mcp_list_servers' not in approval.DESTRUCTIVE_TOOLS
    assert 'mcp_disconnect' not in approval.DESTRUCTIVE_TOOLS


def test_safe_mode_blocks_setup_and_connect():
    _fresh()
    os.environ['ARIA_SAFE_MODE'] = '1'
    res, _ok = dispatch.execute_tool(
        'mcp_setup', {'name': 'probe', 'command': 'cmd.exe',
                      'args': '["/c", "echo PROBE"]'})
    assert res.startswith("[Safe mode: 'mcp_setup' is disabled."), res
    res, _ok = dispatch.execute_tool('mcp_connect', {'name': 'probe'})
    assert res.startswith("[Safe mode: 'mcp_connect' is disabled."), res
    assert _SETUP_CALLS == [] and _CONNECT_CALLS == []
    assert approval._PENDING == {}, 'safe-mode block created a pending approval'


def test_confirm_risky_holds_setup_and_approve_replay_executes_it():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    args = {'name': 'probe', 'command': 'cmd.exe',
            'args': '["/c", "echo PROBE"]'}
    res, _ok = dispatch.execute_tool('mcp_setup', args)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _SETUP_CALLS == [], 'held setup executed without approval'
    held = [e for e in approval._PENDING.values()
            if e['tool'] == 'mcp_setup']
    assert held and held[0]['args'] == args, \
        'held entry is not the exact mcp_setup call'
    token = re.search(r'token=(\S+)\]', res).group(1)
    out = approval.approve_token(token)
    assert _SETUP_CALLS == [{'name': 'probe', 'command': 'cmd.exe',
                             'args': '["/c", "echo PROBE"]', 'url': '',
                             'env': '', 'headers': ''}], _SETUP_CALLS
    assert 'STUB_SETUP' in out, out


def test_confirm_risky_holds_connect():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    res, _ok = dispatch.execute_tool('mcp_connect', {'name': 'probe'})
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _CONNECT_CALLS == [], 'held connect executed without approval'
    held = [e for e in approval._PENDING.values()
            if e['tool'] == 'mcp_connect']
    assert held and held[0]['args'] == {'name': 'probe'}


def test_auto_mode_reaches_handlers_and_list_stays_ungated():
    _fresh()
    res, _ok = dispatch.execute_tool(
        'mcp_setup', {'name': 'probe', 'command': 'cmd.exe', 'args': '[]'})
    assert 'STUB_SETUP' in res, res
    assert len(_SETUP_CALLS) == 1
    approval.tool_set_approval_mode('confirm-risky')
    res, _ok = dispatch.execute_tool('mcp_list_servers', {})
    assert 'STUB_LIST' in res, res
    assert _LIST_CALLS == [True]
    assert approval._PENDING == {}, 'read-only list created a pending approval'
