"""Trust/activation freezes hold at every park path (pass 26 B1#3).

The revision freeze for trust_routine lived only in the normal
approval gate: a trust_routine call parked through the mission
envelope branch rode to the banner with unfrozen args, so a
routine edited between park and tap could be trusted on the
original token. And freeze_trust_args returned revisionless args
when the routine did not exist at all, so "request trust, create
the routine later, confirm" trusted whatever existed at tap
time. Same shape for skill activation: freeze_activation_args
returned unfrozen args when the skill was missing or not a draft.

Pinned here:
- a trust_routine parked through the mission envelope carries
  the revision frozen at park time;
- a trust request for a routine that does not exist is REFUSED,
  not parked revisionless (normal gate and envelope branch);
- a skill_activate for a missing skill is refused the same way,
  and both freeze helpers raise ValueError fail-closed.
"""
import importlib
import json
import os
import sys
import tempfile
import types

import pytest

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_freeze_')
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
runtime = importlib.import_module('aria.runtime')
dispatch = importlib.import_module('aria.tools.dispatch')
routines = importlib.import_module('aria.routines')
skill_gate = importlib.import_module('aria.tools.skill_gate')

ENV = {"allowed_tools": ["test_marker"],
       "pre_approved_irreversible": [],
       "mission_id": 9,
       "goal": "freeze proof"}


def _write_routine(name, steps):
    path = routines._routine_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"name": name, "trusted": False, "steps": steps}, f)


def _fresh(mode='confirm-risky'):
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode(mode)


def test_envelope_parked_trust_routine_carries_revision():
    _fresh()
    _write_routine('freezeme', [{"tool": "test_marker", "args": {}}])
    with runtime.execution('mission', budget_scope='mission:9',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ = dispatch.execute_tool(
            'trust_routine', {'name': 'freezeme'})
    assert res.startswith('[AWAITING_APPROVAL'), res
    assert len(approval._PENDING) == 1
    entry = next(iter(approval._PENDING.values()))
    data, _ = routines._load_routine('freezeme')
    assert entry['args'].get('revision') == \
        routines._routine_revision(data), entry['args']


def test_missing_routine_trust_is_refused_not_parked():
    _fresh()
    res, _ = dispatch.execute_tool(
        'trust_routine', {'name': 'ghost-routine'})
    assert not res.startswith('[AWAITING_APPROVAL'), res
    assert 'does not exist' in res, res
    assert approval._PENDING == {}, 'missing routine was parked'
    with pytest.raises(ValueError, match='does not exist'):
        routines.freeze_trust_args({'name': 'ghost-routine'})


def test_envelope_parked_missing_trust_is_refused():
    _fresh()
    with runtime.execution('mission', budget_scope='mission:9',
                           spend_cap=5.0, envelope=dict(ENV)):
        res, _ = dispatch.execute_tool(
            'trust_routine', {'name': 'ghost-routine'})
    assert not res.startswith('[AWAITING_APPROVAL'), res
    assert 'does not exist' in res, res
    assert approval._PENDING == {}, 'missing routine was parked'


def test_missing_skill_activation_is_refused_not_parked():
    _fresh('confirm-all')
    res, _ = dispatch.execute_tool(
        'skill_activate', {'name': 'ghost-skill'})
    assert not res.startswith('[AWAITING_APPROVAL'), res
    assert 'does not exist' in res, res
    assert approval._PENDING == {}, 'missing skill was parked'
    with pytest.raises(ValueError, match='does not exist'):
        skill_gate.freeze_activation_args({'name': 'ghost-skill'})
