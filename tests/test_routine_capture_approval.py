"""Routine recording: approved replays + project scoping (pass 25 P2#5).

(a) An approval-gated action executed via approve_token's replay
while a recording was open was never captured — the dispatch hook
runs only for non-preauthorized calls and the parked call never
reached it. (b) A recording had no project field, so a project
switch mid-recording silently mixed another project's actions
into the routine.
"""
import json
import os
import re
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_capture_')
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
routines = importlib.import_module('aria.routines')
dispatch = importlib.import_module('aria.tools.dispatch')
workspaces = importlib.import_module('aria.workspaces')
dispatch._REGISTRY['test_echo'] = lambda a: 'echo:' + str(a.get('text', ''))
dispatch._REGISTRY['test_destructive'] = lambda a: 'DESTROYED:' + str(a.get('target', ''))
approval.DESTRUCTIVE_TOOLS = frozenset(
    set(approval.DESTRUCTIVE_TOOLS) | {'test_destructive'})


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval.tool_set_approval_mode('auto')


def _token(awaiting_str):
    m = re.search(r'token=([0-9a-f]{8})', awaiting_str)
    assert m, f'no token in: {awaiting_str[:200]}'
    return m.group(1)


def _saved_steps(name):
    path = os.path.join(routines._routines_dir(), f'{name}.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def test_approved_replay_is_captured():
    _fresh()
    approval.tool_set_approval_mode('confirm-all')
    out = routines.tool_routine_record_start('cap_test')
    assert "Recording routine 'cap_test'" in out, out
    res, _ = dispatch.execute_tool('test_destructive', {'target': 'X'})
    assert '[AWAITING_APPROVAL token=' in res, res
    assert routines._RECORDING['steps'] == []  # parked != executed
    approved = approval.approve_token(_token(res))
    assert 'DESTROYED:X' in approved, approved
    assert routines._RECORDING['steps'] == [
        {'tool': 'test_destructive', 'args': {'target': 'X'}}], \
        routines._RECORDING['steps']
    summary = routines.tool_routine_record_stop()
    assert '1 step' in summary, summary
    saved = _saved_steps('cap_test')
    assert saved['steps'] == [
        {'tool': 'test_destructive', 'args': {'target': 'X'}}]
    assert saved['project'] == workspaces.current()
    routines.tool_delete_routine('cap_test')


def test_denied_approval_is_not_captured():
    _fresh()
    approval.tool_set_approval_mode('confirm-all')
    routines.tool_routine_record_start('cap_deny')
    res, _ = dispatch.execute_tool('test_destructive', {'target': 'Y'})
    assert '[AWAITING_APPROVAL token=' in res, res
    approval.deny_token(_token(res))
    assert routines._RECORDING['steps'] == []
    routines.tool_routine_record_stop()
    routines.tool_delete_routine('cap_deny')


def test_capture_is_scoped_to_recording_project():
    _fresh()
    approval.tool_set_approval_mode('auto')
    home_project = workspaces.current()
    other = workspaces.create('Capture Other')
    routines.tool_routine_record_start('cap_scope')
    assert routines._RECORDING['project'] == home_project
    with workspaces.use(other):
        dispatch.execute_tool('test_echo', {'text': 'other-project'})
    assert routines._RECORDING['steps'] == [], routines._RECORDING['steps']
    dispatch.execute_tool('test_echo', {'text': 'home-project'})
    assert routines._RECORDING['steps'] == [
        {'tool': 'test_echo', 'args': {'text': 'home-project'}}]
    routines.tool_routine_record_stop()
    routines.tool_delete_routine('cap_scope')
