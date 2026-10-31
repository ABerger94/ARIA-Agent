"""Regression tests (2026-10-28, ChatGPT x ARIA loop finding #1):
browser_control go_to launched local programs through the back door
— its handler called tool_open_app_or_url directly, and neither safe
mode nor approval ever saw the launch because both check the OUTER
tool name, and browser_control is in neither policy set.

Fix contract pinned here, through the REAL dispatcher (the handler
level lives in tests/test_browser_control_launch.py):
- a local path / UNC path / non-web scheme via browser_control
  never reaches the launcher, in safe mode AND under confirm-risky,
  and creates no pending approval;
- an https URL via browser_control IS gated like a direct
  open_app_or_url call: blocked in safe mode, held for approval
  under confirm-risky (a pending open_app_or_url entry, zero
  launches), launched in auto mode.

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_browser_control_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir_v20
_data_dir_v20._ROOT = os.path.join(_TMPHOME, 'ARIA')  # V20: state stores resolve via data_dir (install root), not HOME

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
dispatch = importlib.import_module('aria.tools.dispatch')

_CALLS = []
_ORIG_LAUNCHER = dispatch._REGISTRY.get('open_app_or_url')


def _stub_launcher(args):
    _CALLS.append(dict(args or {}))
    return 'STUB_LAUNCHED'


dispatch._REGISTRY['open_app_or_url'] = _stub_launcher


def _fresh():
    dispatch._TURN_CALLS.clear()
    _CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    if _ORIG_LAUNCHER is not None:
        dispatch._REGISTRY['open_app_or_url'] = _ORIG_LAUNCHER
    else:
        dispatch._REGISTRY.pop('open_app_or_url', None)


def _go(url):
    return dispatch.execute_tool(
        'browser_control', {'action': 'go_to', 'url': url})[0]


def test_local_path_never_launches_under_confirm_risky():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    before = len(approval._PENDING)
    for bad in ("C:\\evil.cmd", "\\\\server\\share\\evil.cmd",
                "file:///C:/evil.cmd", "steam://validate/222"):
        res = _go(bad)
        assert res.startswith('status=failed'), res
    assert _CALLS == [], f'local target reached the launcher: {_CALLS}'
    assert len(approval._PENDING) == before, \
        'refused launch still created a pending approval'


def test_local_path_never_launches_in_safe_mode():
    _fresh()
    os.environ['ARIA_SAFE_MODE'] = '1'
    res = _go("C:\\evil.cmd")
    assert res.startswith('status=failed'), res
    assert _CALLS == [], f'local target reached the launcher: {_CALLS}'


def test_https_url_is_safe_mode_blocked_like_direct_launch():
    _fresh()
    os.environ['ARIA_SAFE_MODE'] = '1'
    res = _go('https://example.com')
    assert res.startswith("[Safe mode: 'open_app_or_url' is disabled."), res
    assert _CALLS == [], 'safe-mode launch reached the launcher'


def test_https_url_is_held_for_approval_under_confirm_risky():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    res = _go('https://example.com')
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _CALLS == [], 'held launch executed without approval'
    held = [e for e in approval._PENDING.values()
            if e['tool'] == 'open_app_or_url']
    assert held and held[0]['args'] == {'target': 'https://example.com'}, \
        'held entry is not the gated open_app_or_url call'


def test_https_url_launches_in_auto_mode():
    _fresh()
    res = _go('https://example.com')
    assert res.startswith('status=unverified'), res
    assert 'STUB_LAUNCHED' in res, res
    assert _CALLS == [{'target': 'https://example.com'}], _CALLS
