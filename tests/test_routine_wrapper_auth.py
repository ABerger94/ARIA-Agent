"""Regression tests (2026-10-30, ChatGPT x ARIA loop finding #1):
an untrusted routine's wrapper step granted preauthorization to its
nested gated effect.

browser_control, code_helper and dev_agent are in neither policy
set, but their handlers re-enter the dispatcher for a gated effect
(open_app_or_url / write_file) and ride the outer call's
preauthorized scope (aria/tools/gaptools.py). run_routine_impl
checked policy against the OUTER name only, so an untrusted
routine step replayed preauthorized and its nested launch/write
skipped both the approval gate and the safe-mode check — even a
step recorded while its nested effect was HELD (the capture hook
records the outer call regardless of the nested gate result, so
the held step landed in the routine file and replayed ungated).

Fix contract pinned here, through the REAL dispatcher + routines:
- an untrusted routine's browser_control go_to replay under
  confirm-risky PAUSES at the step with a pending open_app_or_url
  entry and zero launcher calls — including for a step recorded
  while its launch was held;
- an untrusted routine's code_helper write replay under
  confirm-risky PAUSES with a pending write_file entry and zero
  writer calls;
- in safe mode a browser_control go_to step is SKIPPED for trusted
  AND untrusted routines (open_app_or_url is safe-mode-blocked),
  with zero launcher calls;
- controls: a trusted routine still replays its launch in auto
  mode with safe mode off, an untrusted routine still launches in
  auto mode (auto skips approvals by design), and non-effect
  wrapper steps (code_helper explain) are NOT treated as gated
  effects and replay to completion.

Run per-file (own process), like every suite in this repo.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_wrapper_')
os.environ['HOME'] = _TMPHOME
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
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

approval = importlib.import_module('aria.approval')
config = importlib.import_module('aria.config')
dispatch = importlib.import_module('aria.tools.dispatch')
gap = importlib.import_module('aria.tools.gaptools')
routines = importlib.import_module('aria.routines')

CODE = "print('generated')"
gap._provider_text = lambda s, p, role="code": f"```python\n{CODE}\n```"

_LAUNCHES = []
_WRITES = []
_ORIG_LAUNCHER = dispatch._REGISTRY.get('open_app_or_url')
_ORIG_WRITE = dispatch._REGISTRY.get('write_file')


def _stub_launcher(args):
    _LAUNCHES.append(dict(args or {}))
    return 'STUB_LAUNCHED'


def _stub_write(args):
    args = dict(args or {})
    _WRITES.append(args)
    return (f"Successfully wrote {len(str(args.get('content', '')))} "
            f"characters to {args.get('filename', '')}.")


dispatch._REGISTRY['open_app_or_url'] = _stub_launcher
dispatch._REGISTRY['write_file'] = _stub_write


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _LAUNCHES.clear()
    _WRITES.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    if _ORIG_LAUNCHER is not None:
        dispatch._REGISTRY['open_app_or_url'] = _ORIG_LAUNCHER
    else:
        dispatch._REGISTRY.pop('open_app_or_url', None)
    if _ORIG_WRITE is not None:
        dispatch._REGISTRY['write_file'] = _ORIG_WRITE
    else:
        dispatch._REGISTRY.pop('write_file', None)
    shutil.rmtree(_TMPHOME, ignore_errors=True)


def _save_routine(name, steps, trusted=False):
    d = routines._routines_dir()
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump({"name": name, "created": "2026-10-30T00:00:00+00:00",
                   "trusted": trusted, "steps": steps}, f, indent=2)


def _pending(tool):
    return [e for e in approval._PENDING.values() if e['tool'] == tool]


def test_held_launch_recorded_then_replayed_untrusted_stays_held():
    # The exploit chain, end to end: record a go_to while its launch
    # is held under confirm-risky (capture records the outer call
    # even though nothing launched), then replay the untrusted
    # routine — the nested launch must pause again, not fire.
    _fresh()
    approval.tool_set_approval_mode('confirm-risky')
    started = routines.tool_routine_record_start('wrap_held')
    assert started.startswith('Recording routine'), started
    res = dispatch.execute_tool(
        'browser_control', {'action': 'go_to', 'url': 'https://example.com'})[0]
    assert res.startswith('[AWAITING_APPROVAL'), res
    assert _LAUNCHES == [], 'held launch executed during recording'
    out = routines.tool_routine_record_stop()
    assert out.startswith('Saved routine'), out
    # New turn: clear the turn cache and the original pending entry.
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    replay = routines.tool_run_routine('wrap_held', '{}')
    assert 'PAUSED at step 1' in replay, replay
    assert _LAUNCHES == [], f'untrusted replay launched: {_LAUNCHES}'
    held = _pending('open_app_or_url')
    assert held and held[0]['args'] == {'target': 'https://example.com'}, \
        f'pending entry is not the nested launch: {held}'


def test_untrusted_code_helper_write_replay_is_held():
    _fresh()
    _save_routine('wrap_write', [{'tool': 'code_helper',
                                  'args': {'action': 'write',
                                           'description': 'say hi',
                                           'output_path': 'gen_out.py'}}])
    approval.tool_set_approval_mode('confirm-risky')
    dispatch._TURN_CALLS.clear()
    out = routines.tool_run_routine('wrap_write', '{}')
    assert 'PAUSED at step 1' in out, out
    assert _WRITES == [], f'untrusted replay wrote: {_WRITES}'
    held = _pending('write_file')
    assert held and held[0]['args']['filename'] == 'gen_out.py', \
        f'pending entry is not the nested write: {held}'


def test_safe_mode_skips_nested_launch_trusted_and_untrusted():
    _fresh()
    steps = [{'tool': 'browser_control',
              'args': {'action': 'go_to', 'url': 'https://example.com'}}]
    _save_routine('wrap_safe_t', steps, trusted=True)
    _save_routine('wrap_safe_u', steps, trusted=False)
    os.environ['ARIA_SAFE_MODE'] = '1'
    approval.tool_set_approval_mode('auto')
    out_t = routines.tool_run_routine('wrap_safe_t', '{}')
    dispatch._TURN_CALLS.clear()
    out_u = routines.tool_run_routine('wrap_safe_u', '{}')
    assert "Safe mode: 'open_app_or_url' is disabled" in out_t, out_t
    assert "Safe mode: 'open_app_or_url' is disabled" in out_u, out_u
    assert _LAUNCHES == [], 'safe-mode nested launch reached the launcher'


def test_trusted_and_auto_replay_controls_unchanged():
    _fresh()
    # Distinct URLs per replay: reusing one URL would trip the
    # dispatcher's stale-target guard on the second launch (same
    # target as the previous open, no user message mentioning it),
    # which returns before the launcher — that guard, not this fix,
    # would then explain a missing second launch.
    _save_routine('wrap_ctrl_t', [{'tool': 'browser_control',
                                  'args': {'action': 'go_to',
                                           'url': 'https://example.com/trusted'}}],
                  trusted=True)
    _save_routine('wrap_ctrl_u', [{'tool': 'browser_control',
                                  'args': {'action': 'go_to',
                                           'url': 'https://example.com/untrusted'}}],
                  trusted=False)
    approval.tool_set_approval_mode('auto')
    out_t = routines.tool_run_routine('wrap_ctrl_t', '{}')
    assert 'replayed' in out_t, out_t
    dispatch.reset_turn_state()
    out_u = routines.tool_run_routine('wrap_ctrl_u', '{}')
    assert 'replayed' in out_u, out_u
    assert len(_LAUNCHES) == 2, _LAUNCHES


def test_non_effect_wrapper_step_is_not_a_gated_effect():
    _fresh()
    assert routines._wrapper_nested_tool(
        'browser_control', {'action': 'get_text'}) is None
    assert routines._wrapper_nested_tool(
        'code_helper', {'action': 'explain'}) is None
    assert routines._wrapper_nested_tool(
        'browser_control', {'action': 'go_to'}) == 'open_app_or_url'
    assert routines._wrapper_nested_tool(
        'code_helper', {'action': 'write'}) == 'write_file'
    assert routines._wrapper_nested_tool('dev_agent', {}) == 'write_file'
    _save_routine('wrap_explain', [{'tool': 'code_helper',
                                    'args': {'action': 'explain',
                                             'code': 'print(1)'}}])
    approval.tool_set_approval_mode('confirm-risky')
    dispatch._TURN_CALLS.clear()
    out = routines.tool_run_routine('wrap_explain', '{}')
    assert 'replayed' in out and 'PAUSED' not in out, out
    assert _WRITES == [], 'explain step wrote anything'
