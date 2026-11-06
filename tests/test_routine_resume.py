"""Paused routines resume (pass 25 P2#7).

A routine that paused for approval left nothing behind but a
string — its remaining steps never ran unless the user re-ran
the whole routine (re-executing steps that already ran). Now
the pause is a persisted record: approving the pending step
resumes from the next step automatically, and resume_routine()
retries the paused step later.
"""
import os
import re
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_resume_')
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
import json
approval = importlib.import_module('aria.approval')
routines = importlib.import_module('aria.routines')
dispatch = importlib.import_module('aria.tools.dispatch')
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


def _make_routine(name):
    # Record in auto mode: echo, destructive, echo. Untrusted, so
    # the destructive step parks on replay under confirm-all.
    _fresh()
    routines.tool_routine_record_start(name)
    dispatch.execute_tool('test_echo', {'text': 'one'})
    dispatch.execute_tool('test_destructive', {'target': 'X'})
    dispatch.execute_tool('test_echo', {'text': 'three'})
    summary = routines.tool_routine_record_stop()
    assert '3 step' in summary, summary


def _run_paused(name):
    _fresh()
    # Confirm-risky: only the destructive step parks. (Confirm-all
    # would park step 1 too — the echo — and the routine would pause
    # there instead.)
    approval.tool_set_approval_mode('confirm-risky')
    out = routines.tool_run_routine(name)
    assert 'PAUSED at step 2' in out, out
    assert 'echo:three' not in out, out  # step 3 must not have run
    return out


def test_approve_resumes_from_next_step():
    _make_routine('resume_auto')
    out = _run_paused('resume_auto')
    assert len(routines._PAUSED) == 1
    record = next(iter(routines._PAUSED.values()))
    assert record['paused_at'] == 2 and record['next_step'] == 3
    approved = approval.approve_token(_token(out))
    assert 'DESTROYED:X' in approved, approved
    assert 'echo:three' in approved, approved  # step 3 ran on resume
    assert routines._PAUSED == {}
    routines.tool_delete_routine('resume_auto')


def test_pause_survives_restart_then_resumes():
    _make_routine('resume_restart')
    out = _run_paused('resume_restart')
    # Simulate a restart: drop the in-memory pause table; the
    # record must come back from routine_paused.json.
    routines._PAUSED.clear()
    routines._paused_loaded = False
    approved = approval.approve_token(_token(out))
    assert 'echo:three' in approved, approved
    routines.tool_delete_routine('resume_restart')


def test_resume_routine_retries_paused_step_after_deny():
    _make_routine('resume_deny')
    out = _run_paused('resume_deny')
    approval.deny_token(_token(out))
    resumed = routines.tool_resume_routine('resume_deny')
    assert 'PAUSED at step 2' in resumed, resumed  # step 2 re-parked
    assert 'echo:three' not in resumed
    approved = approval.approve_token(_token(resumed))
    assert 'DESTROYED:X' in approved and 'echo:three' in approved
    routines.tool_delete_routine('resume_deny')


def test_resume_routine_refuses_while_approval_pending():
    _make_routine('resume_pending')
    out = _run_paused('resume_pending')
    refused = routines.tool_resume_routine('resume_pending')
    assert 'awaiting approval' in refused and 'approve or deny' in refused, refused
    approval.deny_token(_token(out))
    routines.tool_delete_routine('resume_pending')


def test_changed_routine_refuses_resume():
    _make_routine('resume_changed')
    out = _run_paused('resume_changed')
    path = routines._routine_path('resume_changed')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    data['steps'].append({'tool': 'test_echo', 'args': {'text': 'new'}})
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    approved = approval.approve_token(_token(out))
    assert 'DESTROYED:X' in approved, approved
    assert 'changed since it paused' in approved, approved
    assert 'echo:three' not in approved
    routines.tool_delete_routine('resume_changed')
