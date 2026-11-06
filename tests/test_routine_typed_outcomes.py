"""Routine outcomes are typed, not prose (pass 26 B3#3).

run_routine_impl returned plain prose for every ending — "PAUSED
at step N...", "STOPPED at step N...", "replayed (N steps...)" —
so outcome.normalize mapped all of them to DONE: a paused routine
looked finished, a failed one looked finished, and a step that
came back unverified / ask / started fell through and the next
step ran anyway. Safe-mode-blocked steps were skipped silently
and the routine still closed with a clean "replayed" summary.

Pinned here:
- a paused routine normalizes to ASK, a stopped one to FAILED,
  with the human-readable body kept as text;
- a step returning unverified (or ask / started) STOPS the
  routine — later steps do not run;
- a routine with safe-mode-skipped steps says so in its
  summary instead of reading as a clean replay.
"""
import importlib
import json
import os
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_typed_')
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
from aria.outcome import ASK, FAILED, STARTED, UNVERIFIED, ResultText, normalize  # noqa: E402

_MARKER_CALLS = []


def _stub_marker(args):
    _MARKER_CALLS.append(dict(args or {}))
    return 'STUB_MARKER'


def _stub_failer(args):
    return ResultText('step blew up', FAILED)


def _stub_unverified(args):
    return ResultText('could not confirm the effect', UNVERIFIED)


def _stub_started(args):
    return ResultText('work under way', STARTED)


def _stub_ask(args):
    return ResultText('need input first', ASK)


dispatch._REGISTRY['test_marker'] = _stub_marker
dispatch._REGISTRY['test_failer'] = _stub_failer
dispatch._REGISTRY['test_unverified'] = _stub_unverified
dispatch._REGISTRY['test_started'] = _stub_started
dispatch._REGISTRY['test_ask'] = _stub_ask
dispatch._REGISTRY['test_destructive'] = lambda a: 'DESTROYED'
approval.DESTRUCTIVE_TOOLS = frozenset(
    set(approval.DESTRUCTIVE_TOOLS) | {'test_destructive'})


def _write_routine(name, steps, trusted=True):
    path = routines._routine_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"name": name, "trusted": trusted, "steps": steps}, f)


def _fresh(mode='auto'):
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _MARKER_CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode(mode)


def test_paused_routine_normalizes_to_ask():
    _fresh('confirm-risky')
    _write_routine('typed_pause', [
        {"tool": "test_destructive", "args": {}},
        {"tool": "test_marker", "args": {}},
    ], trusted=False)
    out = routines.run_routine_impl('typed_pause')
    assert normalize(out).status == ASK, out
    assert 'PAUSED at step 1' in out, out
    assert _MARKER_CALLS == [], 'later step ran before approval'


def test_failed_routine_normalizes_to_failed():
    _fresh()
    _write_routine('typed_fail', [
        {"tool": "test_failer", "args": {}},
        {"tool": "test_marker", "args": {}},
    ])
    out = routines.run_routine_impl('typed_fail')
    assert normalize(out).status == FAILED, out
    assert 'STOPPED at step 1' in out, out
    assert _MARKER_CALLS == []


def test_unverified_step_stops_routine():
    _fresh()
    _write_routine('typed_unverified', [
        {"tool": "test_unverified", "args": {}},
        {"tool": "test_marker", "args": {}},
    ])
    out = routines.run_routine_impl('typed_unverified')
    assert normalize(out).status == FAILED, out
    assert 'STOPPED at step 1' in out, out
    assert 'unverified' in out.lower(), out
    assert _MARKER_CALLS == [], 'later step ran on an unverified step'


def test_started_and_ask_steps_stop_routine():
    for tool in ('test_started', 'test_ask'):
        _fresh()
        _write_routine('typed_partial', [
            {"tool": tool, "args": {}},
            {"tool": "test_marker", "args": {}},
        ])
        out = routines.run_routine_impl('typed_partial')
        assert normalize(out).status == FAILED, (tool, out)
        assert 'STOPPED at step 1' in out, (tool, out)
        assert _MARKER_CALLS == [], (tool, 'later step ran')


def test_final_step_unverified_completes_as_unverified():
    # A one-step routine whose step ran but came back unverified
    # (a browser launch is the standing case): there is nothing
    # left to protect, so the routine completes — typed
    # UNVERIFIED, not DONE and not FAILED — and still reads as
    # replayed.
    _fresh()
    _write_routine('typed_final_unverified', [
        {"tool": "test_unverified", "args": {}},
    ])
    out = routines.run_routine_impl('typed_final_unverified')
    assert normalize(out).status == UNVERIFIED, out
    assert 'replayed' in out, out


def test_final_step_ask_still_stops():
    # ASK is different: the step did NOT do the thing, so even as
    # the final step the routine is stopped, not completed.
    _fresh()
    _write_routine('typed_final_ask', [
        {"tool": "test_ask", "args": {}},
    ])
    out = routines.run_routine_impl('typed_final_ask')
    assert normalize(out).status == FAILED, out
    assert 'STOPPED at step 1' in out, out


def test_safe_mode_skips_are_recorded_in_summary():
    _fresh()
    dispatch._REGISTRY['send_email'] = lambda a: 'EMAIL_SENT'
    _write_routine('typed_safe', [
        {"tool": "send_email", "args": {}},
        {"tool": "test_marker", "args": {}},
    ])
    os.environ['ARIA_SAFE_MODE'] = '1'
    try:
        out = routines.run_routine_impl('typed_safe')
    finally:
        os.environ.pop('ARIA_SAFE_MODE', None)
    assert len(_MARKER_CALLS) == 1, 'non-blocked step should still run'
    assert "Safe mode: 'send_email' is disabled" in out, out
    assert 'skipped in safe mode' in out.lower(), out
    assert 'PARTIALLY replayed' in out, out
