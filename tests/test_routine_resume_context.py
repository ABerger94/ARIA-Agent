"""Paused routines resume under the policy they paused under
(pass 26 B1#2).

After one parked routine step was confirmed, the remaining steps
resumed under a bare runtime.execution("routine-resume") with no
budget_scope, spend_cap, or envelope — the mission cap and tool
envelope the routine paused under were dropped, so resumed steps
ran unenveloped and uncapped. The pause record now carries the
pausing execution's policy as resume_context, and both resume
paths reopen the execution under those values.

Pinned here:
- pause a routine step under a capped, enveloped execution,
  approve, and the resumed steps observe the SAME cap, scope,
  and envelope (the envelope is the discriminator: the approval
  entry never captured it, so the old code resumed with none);
- a paused record written before resume_context existed (no
  resume_context key) still resumes exactly as before.
"""
import importlib
import json
import os
import re
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_resume_ctx_')
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

approval = importlib.import_module('aria.approval')
runtime = importlib.import_module('aria.runtime')
routines = importlib.import_module('aria.routines')
dispatch = importlib.import_module('aria.tools.dispatch')

ENV = {"allowed_tools": ["test_destructive", "test_observe"],
       "pre_approved_irreversible": [],
       "spend_cap": 3.0,
       "mission_id": 99,
       "goal": "resume-context proof"}

dispatch._REGISTRY['test_echo'] = lambda a: 'echo:' + str(a.get('text', ''))
dispatch._REGISTRY['test_destructive'] = \
    lambda a: 'DESTROYED:' + str(a.get('target', ''))


def _observe(args):
    ctx = runtime.current()
    env = ctx.envelope or {}
    return (f"OBSERVED cap={ctx.spend_cap} scope={ctx.budget_scope} "
            f"env_mission={env.get('mission_id')}")


dispatch._REGISTRY['test_observe'] = _observe
approval.DESTRUCTIVE_TOOLS = frozenset(
    set(approval.DESTRUCTIVE_TOOLS) | {'test_destructive'})


def _fresh(mode='confirm-risky'):
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode(mode)


def _token(awaiting_str):
    m = re.search(r'token=([0-9a-f]{8})', awaiting_str)
    assert m, f'no token in: {awaiting_str[:200]}'
    return m.group(1)


def _write_routine(name, steps):
    path = routines._routine_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"name": name, "trusted": False, "steps": steps}, f)


def test_approve_resumes_under_pausing_context():
    _fresh()
    _write_routine('ctx_resume', [
        {"tool": "test_destructive", "args": {"target": "X"}},
        {"tool": "test_observe", "args": {}},
    ])
    with runtime.execution('mission', budget_scope='mission:99',
                           spend_cap=3.0, envelope=dict(ENV)):
        out = routines.tool_run_routine('ctx_resume')
    assert 'PAUSED at step 1' in out, out
    assert len(routines._PAUSED) == 1
    record = next(iter(routines._PAUSED.values()))
    ctx = record.get('resume_context') or {}
    assert ctx.get('spend_cap') == 3.0, record
    assert ctx.get('budget_scope') == 'mission:99', record
    assert (ctx.get('envelope') or {}).get('mission_id') == 99, record
    approved = approval.approve_token(_token(out))
    assert 'DESTROYED:X' in approved, approved
    assert 'OBSERVED cap=3.0 scope=mission:99 env_mission=99' in approved, approved


def test_old_format_paused_record_still_resumes():
    _fresh('auto')
    _write_routine('legacy_pause', [
        {"tool": "test_echo", "args": {"text": "hi"}},
        {"tool": "test_observe", "args": {}},
    ])
    data, _ = routines._load_routine('legacy_pause')
    routines._load_paused()
    routines._PAUSED['deadbeef'] = {
        "token": "deadbeef",
        "name": "legacy_pause",
        "paused_at": 1,
        "next_step": 1,
        "params": {},
        "revision": routines._routine_revision(data),
        "project": None,
        "paused_ts": 0,
        # No resume_context key: a record written before pass 26.
    }
    out = routines.maybe_resume_for_token('deadbeef')
    assert 'echo:hi' in out, out
    assert 'OBSERVED' in out, out
