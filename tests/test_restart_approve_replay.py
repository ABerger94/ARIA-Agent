"""restart_program can complete through confirm-all (pass 26 B5#5).

Under confirm-all every tool gates, so restart_program parked;
but approving the token replayed it with preauthorized=True,
and _tool_restart_program refused whenever
in_preauthorized_scope() was true — so the approved restart
never ran. The discriminator already exists:
approval.in_approve_replay(tool, args) is true only for the
exact call approve_token is replaying right now, so the handler
allows that one call and keeps refusing routine replays and
delegated workers.

Pinned here:
- park restart_program under confirm-all, approve the token
  with a stubbed request_program_restart, and the restart runs;
- a routine replay (preauthorized scope, no replay marker) and
  a delegated worker are still refused, and the stub never runs.
"""
import importlib
import json
import os
import re
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_restart_approve_')
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

approval = importlib.import_module('aria.approval')
config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')

_RESTART_CALLS = []


def _stub_request_restart():
    _RESTART_CALLS.append('restart')
    return 'Restarting my program now.'


_brain = types.ModuleType('aria.agent.brain')
_brain.request_program_restart = _stub_request_restart
sys.modules['aria.agent.brain'] = _brain


def _fresh(mode='confirm-all'):
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _RESTART_CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode(mode)


def _token(awaiting_str):
    m = re.search(r'token=([0-9a-f]{8})', awaiting_str)
    assert m, f'no token in: {awaiting_str[:200]}'
    return m.group(1)


def test_approved_restart_runs_under_confirm_all():
    _fresh()
    res, _ = dispatch.execute_tool('restart_program', {})
    assert res.startswith('[AWAITING_APPROVAL'), res
    assert _RESTART_CALLS == [], 'restart ran before approval'
    out = approval.approve_token(_token(res))
    assert 'Restarting my program now.' in out, out
    assert _RESTART_CALLS == ['restart']


def test_routine_replay_still_refused():
    _fresh()
    dispatch._SCOPE.preauthorized = True
    try:
        out = dispatch._tool_restart_program({})
    finally:
        dispatch._SCOPE.preauthorized = False
    assert 'refused' in out, out
    assert _RESTART_CALLS == [], 'routine replay restarted the program'


def test_delegated_worker_still_refused():
    _fresh()
    with dispatch.delegated_scope():
        out = dispatch._tool_restart_program({})
    assert 'refused' in out, out
    assert 'delegated' in out, out
    assert _RESTART_CALLS == []
