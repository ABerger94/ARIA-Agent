"""A routine stops at an explicitly failed step (pass 25 P2#6).

The replay loop only stopped for approval pauses, so a failed
step was logged and every later step still ran on top of it.
"""
import importlib
import json
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_stop_')
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
from aria.outcome import FAILED, ResultText  # noqa: E402

_MARKER_CALLS = []


def _stub_marker(args):
    _MARKER_CALLS.append(dict(args or {}))
    return 'STUB_MARKER'


def _stub_failer(args):
    return ResultText('step blew up', FAILED)


dispatch._REGISTRY['test_marker'] = _stub_marker
dispatch._REGISTRY['test_failer'] = _stub_failer


def _write_routine(name, steps):
    path = routines._routine_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"name": name, "trusted": True, "steps": steps}, f)


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _MARKER_CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def test_routine_stops_after_failed_step():
    _fresh()
    _write_routine('stopper', [
        {"tool": "test_failer", "args": {}},
        {"tool": "test_marker", "args": {}},
    ])
    out = routines.run_routine_impl('stopper')
    assert 'STOPPED at step 1' in out, out
    assert 'later steps not run' in out, out
    assert _MARKER_CALLS == [], _MARKER_CALLS


def test_routine_runs_fully_when_steps_succeed():
    _fresh()
    _write_routine('okrun', [
        {"tool": "test_marker", "args": {"n": 1}},
        {"tool": "test_marker", "args": {"n": 2}},
    ])
    out = routines.run_routine_impl('okrun')
    assert 'STOPPED' not in out, out
    assert len(_MARKER_CALLS) == 2
