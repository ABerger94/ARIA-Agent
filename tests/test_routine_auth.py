"""Regression tests (2026-10-29, ChatGPT x ARIA loop finding #1):
routine replay was a blanket authorization bypass. trust_routine
was an ordinary model-callable tool that flipped a routine's
`trusted` flag with no human confirmation, and run_routine_impl
replayed trusted steps with preauthorized=True — which skips
BOTH the dispatcher's approval gate and its safe-mode check.
Worse, the auto-mode extension: an untrusted routine also
replayed preauthorized in auto mode (needs_approval() is False
there), so safe mode was skipped for ANY routine replay in auto.

Fix contract pinned here, through the REAL dispatcher:
- trust_routine is in DESTRUCTIVE_TOOLS, so under confirm-risky
  a model call pauses for the user's banner tap (zero trust
  effect until then) and the approve-token replay executes
  exactly that grant; untrust_routine stays ungated (revocation
  only restores per-step approval), and the other routine tools
  stay ungated;
- safe mode is a hard block on replays too: a step whose tool is
  in SAFE_MODE_BLOCKED_TOOLS is skipped while safe mode is on,
  for trusted routines AND for untrusted routines in auto mode,
  with zero handler calls;
- a step that pauses for approval stops the routine: later
  steps do NOT run before the user confirms the earlier one;
- control: a trusted routine still replays fully in auto mode
  with safe mode off (auto skips approvals by design).

Run per-file (own process), like every suite in this repo.
"""
import importlib
import os
import shutil
import sys
import tempfile
import types

_TMPHOME = tempfile.mkdtemp(prefix='aria_routine_auth_')
os.environ['HOME'] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aria')
pkg = types.ModuleType('aria')
pkg.__path__ = [PKG]
sys.modules['aria'] = pkg
from aria import data_dir as _data_dir_v20
_data_dir_v20._ROOT = os.path.join(_TMPHOME, 'ARIA')  # V20: state stores resolve via data_dir (install root), not HOME

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
builtins = importlib.import_module('aria.tools.builtins')
routines = importlib.import_module('aria.routines')

_EMAIL_CALLS = []
_MARKER_CALLS = []
_ORIG_SEND_EMAIL = builtins.tool_send_email


def _stub_send_email(to='', subject='', body=''):
    _EMAIL_CALLS.append({'to': to, 'subject': subject, 'body': body})
    return 'STUB_EMAIL_SENT'


def _stub_marker(args):
    _MARKER_CALLS.append(dict(args or {}))
    return 'STUB_MARKER'


builtins.tool_send_email = _stub_send_email
dispatch._REGISTRY['test_marker'] = _stub_marker

EMAIL_ARGS = {'to': 'nobody@example.com', 'subject': 't', 'body': 'b'}


def _fresh():
    dispatch._TURN_CALLS.clear()
    approval._PENDING.clear()
    _EMAIL_CALLS.clear()
    _MARKER_CALLS.clear()
    os.environ.pop('ARIA_SAFE_MODE', None)
    approval.tool_set_approval_mode('auto')


def teardown_module(module=None):
    builtins.tool_send_email = _ORIG_SEND_EMAIL
    dispatch._REGISTRY.pop('test_marker', None)
    shutil.rmtree(_TMPHOME, ignore_errors=True)


def _record_routine(name):
    """Record [send_email, test_marker] via the real capture hook."""
    _fresh()
    started = routines.tool_routine_record_start(name)
    assert started.startswith('Recording routine'), started
    dispatch.execute_tool('send_email', dict(EMAIL_ARGS))
    dispatch.execute_tool('test_marker', {})
    out = routines.tool_routine_record_stop()
    assert out.startswith('Saved routine'), out
    _EMAIL_CALLS.clear()
    _MARKER_CALLS.clear()
    dispatch._TURN_CALLS.clear()


def test_trust_routine_is_gated_and_others_are_not():
    assert 'trust_routine' in approval.DESTRUCTIVE_TOOLS
    # Scope pins: revocation only restores approval; the rest of the
    # routine surface is read-only / replay, not an authorization grant.
    for _name in ('untrust_routine', 'run_routine', 'list_routines',
                  'describe_routine', 'delete_routine'):
        assert _name not in approval.DESTRUCTIVE_TOOLS, _name


def test_trust_grant_held_until_user_confirms():
    _record_routine('auth_gate')
    approval.tool_set_approval_mode('confirm-risky')
    dispatch._TURN_CALLS.clear()
    res, _ok = dispatch.execute_tool('trust_routine', {'name': 'auth_gate'})
    assert res.startswith('[AWAITING_APPROVAL'), res
    assert 'trusted: no' in routines.tool_describe_routine('auth_gate')
    assert len(approval._PENDING) == 1
    token, entry = next(iter(approval._PENDING.items()))
    assert entry['tool'] == 'trust_routine'
    assert entry['args'] == {'name': 'auth_gate'}
    out = approval.approve_token(token)
    assert 'now trusted' in out, out
    assert 'trusted: yes' in routines.tool_describe_routine('auth_gate')


def test_safe_mode_blocks_replay_steps_trusted_and_auto_untrusted():
    _record_routine('auth_safe_t')
    _record_routine('auth_safe_u')
    assert 'now trusted' in routines.tool_trust_routine('auth_safe_t')
    os.environ['ARIA_SAFE_MODE'] = '1'
    approval.tool_set_approval_mode('auto')
    out_t = routines.tool_run_routine('auth_safe_t', '{}')
    out_u = routines.tool_run_routine('auth_safe_u', '{}')
    assert "Safe mode: 'send_email' is disabled" in out_t, out_t
    assert "Safe mode: 'send_email' is disabled" in out_u, out_u
    assert _EMAIL_CALLS == [], 'safe-mode replay executed the email step'


def test_routine_pauses_on_held_step():
    _record_routine('auth_pause')
    approval.tool_set_approval_mode('confirm-risky')
    dispatch._TURN_CALLS.clear()
    out = routines.tool_run_routine('auth_pause', '{}')
    assert 'AWAITING_APPROVAL' in out, out
    assert 'paused at step 1' in out.lower(), out
    assert _MARKER_CALLS == [], 'later step ran before the held step resolved'
    assert len(approval._PENDING) == 1
    assert next(iter(approval._PENDING.values()))['tool'] == 'send_email'


def test_auto_trusted_replay_unchanged_control():
    _record_routine('auth_ctrl')
    assert 'now trusted' in routines.tool_trust_routine('auth_ctrl')
    approval.tool_set_approval_mode('auto')
    dispatch._TURN_CALLS.clear()
    out = routines.tool_run_routine('auth_ctrl', '{}')
    assert 'replayed' in out, out
    assert len(_EMAIL_CALLS) == 1, _EMAIL_CALLS
    assert len(_MARKER_CALLS) == 1, _MARKER_CALLS
