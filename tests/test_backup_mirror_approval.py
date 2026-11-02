"""Regression tests (2026-11-01, ChatGPT x ARIA loop pass 14 finding #1):
set_backup_mirror was an unapproved exfiltration path — the handler
persists a model-chosen mirror folder and immediately copies the
memory DB, settings, and chat history there by basename, overwriting
existing files (aria/backup_mirror.py), while the name was in
neither DESTRUCTIVE_TOOLS nor SAFE_MODE_BLOCKED_TOOLS, so the
dispatcher waved it through in every mode including safe mode.
(a synced Drive/OneDrive/NAS folder is a route off the machine;
aria_keys.json is deliberately excluded from the copy, but the
mirrored stores are personal data.)

Fix contract pinned here, through the REAL dispatcher:
- set_backup_mirror is in both policy sets;
- in safe mode a set is blocked (zero handler calls, no pending
  approval) — safe mode blocks the whole tool, status report
  included, because is_safe_mode_blocked() classifies by name
  only (same as manage_background_job);
- under confirm-risky a set and a clear are held for approval
  (zero handler calls, one pending entry with the exact call and
  a description naming the destination), and an approve_token
  replay executes the exact held set;
- under confirm-risky the no-argument status report is NOT held
  (it changes nothing — it only reports the current folder);
- in auto mode a set reaches the handler (control: auto skips
  approvals by design).

Run per-file (own process), like every suite in this repo.
"""
import os
import sys
import types
import tempfile

_TMPHOME = tempfile.mkdtemp(prefix='aria_backup_mirror_approval_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir_v20
_data_dir_v20._ROOT = os.path.join(_TMPHOME, 'ARIA')

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
backup_mirror = importlib.import_module('aria.backup_mirror')

_CALLS = []
_ORIG = backup_mirror.tool_set_backup_mirror


def _stub(args=None):
    _CALLS.append(dict(args or {}))
    return 'STUB_MIRROR'


backup_mirror.tool_set_backup_mirror = _stub


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    backup_mirror.tool_set_backup_mirror = _ORIG


def test_policy_sets_contain_set_backup_mirror():
    assert 'set_backup_mirror' in approval.DESTRUCTIVE_TOOLS
    assert 'set_backup_mirror' in config.SAFE_MODE_BLOCKED_TOOLS


def test_safe_mode_blocks_set_and_status():
    _fresh()
    os.environ['ARIA_SAFE_MODE'] = '1'
    res, _ok = dispatch.execute_tool(
        'set_backup_mirror', {'path': '/tmp/probe-mirror'})
    assert res.startswith("[Safe mode: 'set_backup_mirror' is disabled."), res
    res, _ok = dispatch.execute_tool('set_backup_mirror', {})
    assert res.startswith("[Safe mode: 'set_backup_mirror' is disabled."), res
    assert _CALLS == []
    assert approval._PENDING == {}, 'safe-mode block created a pending approval'


def test_confirm_risky_holds_set_and_approve_replay_executes_it():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    args = {'path': '/tmp/probe-mirror'}
    res, _ok = dispatch.execute_tool('set_backup_mirror', args)
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _CALLS == [], 'held set executed without approval'
    held = [e for e in approval._PENDING.values()
            if e['tool'] == 'set_backup_mirror']
    assert held and held[0]['args'] == args, \
        'held entry is not the exact set_backup_mirror call'
    assert '/tmp/probe-mirror' in held[0]['desc'], held[0]['desc']
    assert 'memory DB' in held[0]['desc'], held[0]['desc']
    token = held[0]['token']
    out = approval.approve_token(token)
    assert _CALLS == [args], _CALLS
    assert 'STUB_MIRROR' in out, out


def test_confirm_risky_holds_clear():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    res, _ok = dispatch.execute_tool('set_backup_mirror', {'clear': True})
    assert res.startswith('[AWAITING_APPROVAL token='), res
    assert _CALLS == [], 'held clear executed without approval'
    held = [e for e in approval._PENDING.values()
            if e['tool'] == 'set_backup_mirror']
    assert held and held[0]['args'] == {'clear': True}


def test_confirm_risky_status_report_stays_ungated():
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    res, _ok = dispatch.execute_tool('set_backup_mirror', {})
    assert 'STUB_MIRROR' in res, res
    assert _CALLS == [{}]
    assert approval._PENDING == {}, 'read-only status created a pending approval'


def test_auto_mode_reaches_handler():
    _fresh()
    res, _ok = dispatch.execute_tool(
        'set_backup_mirror', {'path': '/tmp/probe-mirror'})
    assert 'STUB_MIRROR' in res, res
    assert len(_CALLS) == 1
