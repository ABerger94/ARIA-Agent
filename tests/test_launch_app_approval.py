"""Regression tests (2026-10-30, ChatGPT x ARIA loop finding #1,
queued since pass 4): launch_app was an ungated local-program
launch path — tool_launch_app os.startfile()s any existing path
or Start Menu entry (aria/tools/winctl.py), while neither
launch_app nor its sibling open_app_or_url's policy applied:
launch_app was in neither DESTRUCTIVE_TOOLS nor
SAFE_MODE_BLOCKED_TOOLS, so the dispatcher checked the outer
name and waved it through under confirm-risky and in safe mode.

Fix contract pinned here, through the REAL dispatcher (the
winctl handler itself is stubbed: on this Linux host the real
handler no-ops as Windows-only, so the stub is what proves the
gate — zero stub calls means zero launches):
- launch_app is blocked in safe mode (zero handler calls, no
  pending approval created);
- under confirm-risky it is held for approval (zero handler
  calls, one pending entry with the exact call and a banner
  description naming the requested app), and an approve_token
  replay executes the exact held call;
- in auto mode it reaches the handler (control);
- the Shell Home / dashboard launcher is a separate user-click
  path (aria/app_launcher.py) and is not the model tool gated
  here.

Run per-file (own process), like every suite in this repo.
"""
import os
import re
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_launch_app_approval_')
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
winctl = importlib.import_module('aria.tools.winctl')

_LAUNCH_CALLS = []
_ORIG_LAUNCH = winctl.tool_launch_app


def _stub_launch(name=''):
    _LAUNCH_CALLS.append({'name': name})
    return 'STUB_LAUNCH'


winctl.tool_launch_app = _stub_launch


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _LAUNCH_CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    winctl.tool_launch_app = _ORIG_LAUNCH


def test_policy_sets_contain_launch_app():
    assert 'launch_app' in approval.DESTRUCTIVE_TOOLS
    assert 'launch_app' in config.SAFE_MODE_BLOCKED_TOOLS
    # Sibling stays gated (untouched by this fix).
    assert 'open_app_or_url' in approval.DESTRUCTIVE_TOOLS
    assert 'open_app_or_url' in config.SAFE_MODE_BLOCKED_TOOLS
    # The held banner names the requested app, not a generic line.
    assert approval._describe_tool(
        'launch_app', {'name': 'Notepad'}) == "launch app 'Notepad'"


def test_safe_mode_blocks_launch_app():
    _fresh()
    os.environ['ARIA_SAFE_MODE'] = '1'
    res, _ok = dispatch.execute_tool(
        'launch_app', {'name': 'C:\\Temp\\probe.cmd'})
    assert res.startswith("[Safe mode: 'launch_app' is disabled."), res
    assert _LAUNCH_CALLS == []
    assert approval._PENDING == {}, 'safe-mode block created a pending approval'


def test_confirm_risky_holds_and_approve_replay_executes_it():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    args = {'name': 'C:\\Temp\\probe.cmd'}
    res, _ok = dispatch.execute_tool('launch_app', args)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _LAUNCH_CALLS == [], 'held launch executed without approval'
    held = [e for e in approval._PENDING.values()
            if e['tool'] == 'launch_app']
    assert held and held[0]['args'] == args, \
        'held entry is not the exact launch_app call'
    assert held[0]['desc'] == "launch app 'C:\\Temp\\probe.cmd'", held[0]
    token = re.search(r'token=(\S+)\]', res).group(1)
    out = approval.approve_token(token)
    assert _LAUNCH_CALLS == [{'name': 'C:\\Temp\\probe.cmd'}], _LAUNCH_CALLS
    assert 'STUB_LAUNCH' in out, out


def test_confirm_risky_holds_start_menu_name():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    res, _ok = dispatch.execute_tool('launch_app', {'name': 'Notepad'})
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _LAUNCH_CALLS == [], 'held launch executed without approval'
    held = [e for e in approval._PENDING.values()
            if e['tool'] == 'launch_app']
    assert held and held[0]['args'] == {'name': 'Notepad'}


def test_auto_mode_reaches_handler():
    _fresh()
    res, _ok = dispatch.execute_tool('launch_app', {'name': 'Notepad'})
    assert 'STUB_LAUNCH' in res, res
    assert len(_LAUNCH_CALLS) == 1
    assert approval._PENDING == {}, 'auto launch created a pending approval'
