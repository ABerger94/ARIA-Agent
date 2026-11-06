"""A trust_routine approval binds the routine revision (pass 25 P1#8).

The approval stored only the routine name, and _save_trusted
re-read the routine file fresh at execution — so a routine
edited (or re-recorded) between park and tap got trusted on the
original token. The parked entry now freezes the content hash,
and the grant refuses when the live routine differs.
"""
import importlib
import json
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_trust_')
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
routines = importlib.import_module('aria.routines')


def _stub_marker(args):
    return 'STUB_MARKER'


dispatch._REGISTRY['test_marker'] = _stub_marker


def _write_routine(name, steps):
    path = routines._routine_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"name": name, "trusted": False, "steps": steps}, f)


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('confirm-risky')


def _trusted(name):
    data, _ = routines._load_routine(name)
    return bool(data.get('trusted'))


def test_trust_token_refuses_edited_routine():
    _fresh()
    _write_routine('trustme', [{"tool": "test_marker", "args": {}}])
    res, _ = dispatch.execute_tool('trust_routine', {'name': 'trustme'})
    assert res.startswith('[AWAITING_APPROVAL'), res
    assert len(approval._PENDING) == 1
    token, entry = next(iter(approval._PENDING.items()))
    assert entry['args'].get('revision'), entry['args']
    # Edit the routine after the approval parked.
    _write_routine('trustme', [
        {"tool": "test_marker", "args": {}},
        {"tool": "test_marker", "args": {"n": 2}},
    ])
    out = approval.approve_token(token)
    assert 'changed since' in out, out
    assert _trusted('trustme') is False
    # A fresh trust call on the new revision grants normally.
    res, _ = dispatch.execute_tool('trust_routine', {'name': 'trustme'})
    assert res.startswith('[AWAITING_APPROVAL'), res
    token2, _ = next(iter(approval._PENDING.items()))
    out = approval.approve_token(token2)
    assert 'now trusted' in out, out
    assert _trusted('trustme') is True
