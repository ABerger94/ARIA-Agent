"""Standalone headless tests for ARIA ULTIMATE Worker A (Modules 1+4):
routines (aria/routines.py) and the approval layer (aria/approval.py).

Covers spec test-plan items 1+2:
  1. routine record -> JSON on disk -> run with {{param}} substitution ->
     list/describe/delete
  2. approval mode=confirm-all -> AWAITING_APPROVAL+token -> approve executes /
     deny drops / expired token rejected / mode=auto executes directly;
     trusted-routine destructive step bypasses approval, untrusted does not.

All storage is redirected to a temp HOME; the real ~/ARIA is never touched.

Run:  python tests/test_ultimate_a.py
"""
import os
import sys
import types
import json
import re
import time
import shutil
import tempfile
import traceback
_TMPHOME = tempfile.mkdtemp(prefix='aria_ultimate_a_')
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
for _name in ('aria.scheduler', 'aria.vision', 'aria.hardware', 'aria.spotify', 'aria.hud', 'aria.speech'):
    sys.modules[_name] = types.ModuleType(_name)
import importlib
config = importlib.import_module('aria.config')
approval = importlib.import_module('aria.approval')
routines = importlib.import_module('aria.routines')
dispatch = importlib.import_module('aria.tools.dispatch')
dispatch._REGISTRY['test_echo'] = lambda a: 'echo:' + str(a.get('text', ''))
dispatch._REGISTRY['test_destructive'] = lambda a: 'DESTROYED:' + str(a.get('target', ''))
approval.DESTRUCTIVE_TOOLS = frozenset(set(approval.DESTRUCTIVE_TOOLS) | {'test_destructive'})

def fresh_turn():
    """Clear per-turn duplicate-call blocking so replays aren't suppressed."""
    dispatch._TURN_CALLS.clear()

def extract_token(awaiting_str):
    m = re.search('token=([0-9a-f]{8})', awaiting_str)
    assert m, f'no token in: {awaiting_str[:200]}'
    return m.group(1)

def test_record_run_param_substitution():
    fresh_turn()
    approval.tool_set_approval_mode('auto')
    out = routines.tool_routine_record_start('Test Routine!')
    assert "Recording routine 'test_routine'" in out, out
    dup = routines.tool_routine_record_start('other')
    assert 'Already recording' in dup, dup
    r1, _ = dispatch.execute_tool('test_echo', {'text': 'hello {{who}}'})
    assert 'echo:hello {{who}}' in r1, r1
    r2, _ = dispatch.execute_tool('test_echo', {'text': 'bye'})
    assert 'echo:bye' in r2, r2
    summary = routines.tool_routine_record_stop()
    assert '2 step' in summary and 'test_routine' in summary, summary
    assert 'Not recording' in routines.tool_routine_record_stop()
    path = os.path.join(_TMPHOME, 'ARIA', 'routines', 'test_routine.json')
    assert os.path.exists(path), path
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    assert data['name'] == 'test_routine', data
    assert data['trusted'] is False, data
    assert len(data['steps']) == 2, data
    assert data['steps'][0] == {'tool': 'test_echo', 'args': {'text': 'hello {{who}}'}}, data
    fresh_turn()
    out = routines.tool_run_routine('test_routine', '{"who": "alek"}')
    assert 'echo:hello alek' in out, out
    assert 'echo:bye' in out, out
    assert '{{who}}' not in out, out
    fresh_turn()
    out2 = routines.tool_run_routine('test_routine', '{}')
    assert 'echo:hello {{who}}' in out2, out2
    assert 'Invalid params_json' in routines.tool_run_routine('test_routine', '{nope')
    assert 'not found' in routines.tool_run_routine('no_such_routine_xyz').lower()

def test_exclusions_not_captured():
    fresh_turn()
    approval.tool_set_approval_mode('auto')
    routines.tool_routine_record_start('excl_test')
    assert routines.maybe_capture('save_memory', {'k': 'v'}) is False
    assert routines.maybe_capture('approve', {'token': 'x'}) is False
    assert routines.maybe_capture('deny', {'token': 'x'}) is False
    assert routines.maybe_capture('set_approval_mode', {'mode': 'auto'}) is False
    assert routines.maybe_capture('routine_record_start', {'name': 'z'}) is False
    assert routines.maybe_capture('test_echo', {'text': 'kept'}) is True
    summary = routines.tool_routine_record_stop()
    assert '1 step' in summary, summary
    routines.tool_delete_routine('excl_test')

def test_list_describe_delete():
    fresh_turn()
    approval.tool_set_approval_mode('auto')
    routines.tool_routine_record_start('temp_one')
    dispatch.execute_tool('test_echo', {'text': 'one'})
    routines.tool_routine_record_stop()
    listing = routines.tool_list_routines()
    assert 'temp_one' in listing and '1 step' in listing, listing
    desc = routines.tool_describe_routine('temp_one')
    assert 'temp_one' in desc and 'test_echo' in desc and ('one' in desc), desc
    assert 'trusted: no' in desc, desc
    assert 'not found' in routines.tool_describe_routine('missing_xyz').lower()
    assert 'not found' in routines.tool_delete_routine('missing_xyz').lower()
    assert "Deleted routine 'temp_one'" in routines.tool_delete_routine('temp_one')
    assert 'temp_one' not in routines.tool_list_routines()

def test_name_sanitization():
    assert routines.sanitize_name('Hello World!') == 'hello_world', routines.sanitize_name('Hello World!')
    assert routines.sanitize_name('a' * 100) == 'a' * 40
    assert routines.sanitize_name('!!!') == ''
    assert 'no valid characters' in routines.tool_routine_record_start('!!!').lower()

def test_confirm_all_flow():
    fresh_turn()
    approval.tool_set_approval_mode('confirm-all')
    res, _ = dispatch.execute_tool('test_destructive', {'target': 'X'})
    assert '[AWAITING_APPROVAL token=' in res, res
    assert 'DESTROYED' not in res, res
    token = extract_token(res)
    pending = approval.tool_list_pending_approvals()
    assert token in pending and 'test_destructive' in pending, pending
    out = approval.tool_approve(token)
    assert 'DESTROYED:X' in out, out
    again = approval.tool_approve(token)
    assert 'unknown or expired' in again, again
    fresh_turn()
    res2, _ = dispatch.execute_tool('test_destructive', {'target': 'Y'})
    token2 = extract_token(res2)
    denied = approval.tool_deny(token2)
    assert 'Denied and dropped' in denied, denied
    assert 'DESTROYED:Y' not in denied, denied
    assert 'unknown or expired' in approval.tool_approve(token2)
    assert token2 not in approval.tool_list_pending_approvals()

def test_expired_token_rejected():
    fresh_turn()
    approval.tool_set_approval_mode('confirm-all')
    res, _ = dispatch.execute_tool('test_destructive', {'target': 'old'})
    token = extract_token(res)
    approval._PENDING[token]['created_ts'] = time.time() - 700
    out = approval.tool_approve(token)
    assert 'unknown or expired' in out, out
    assert token not in approval.tool_list_pending_approvals()
    res2, _ = dispatch.execute_tool('test_destructive', {'target': 'old2'})
    token2 = extract_token(res2)
    approval._PENDING[token2]['created_ts'] = time.time() - 700
    assert token2 not in approval.tool_list_pending_approvals()

def test_auto_mode_executes_directly():
    fresh_turn()
    approval.tool_set_approval_mode('auto')
    res, _ = dispatch.execute_tool('test_destructive', {'target': 'Z'})
    assert 'DESTROYED:Z' in res, res
    assert 'AWAITING_APPROVAL' not in res, res

def test_confirm_risky_selective():
    fresh_turn()
    approval.tool_set_approval_mode('confirm-risky')
    res, _ = dispatch.execute_tool('test_destructive', {'target': 'R'})
    assert '[AWAITING_APPROVAL token=' in res, res
    approval.tool_deny(extract_token(res))
    res2, _ = dispatch.execute_tool('test_echo', {'text': 'safe'})
    assert 'echo:safe' in res2 and 'AWAITING_APPROVAL' not in res2, res2
    assert approval.needs_approval('file_organize', {'dry_run': True}) is False
    assert approval.needs_approval('file_organize', {}) is False
    assert approval.needs_approval('file_organize', {'dry_run': False}) is True
    assert approval.needs_approval('file_organize', {'dry_run': 'false'}) is True
    assert approval.needs_approval('approve', {}) is False
    assert approval.needs_approval('deny', {}) is False
    assert approval.needs_approval('set_approval_mode', {}) is False

def test_mode_persistence_and_validation():
    out = approval.tool_set_approval_mode('confirm-risky')
    assert 'confirm-risky' in out, out
    path = os.path.join(_TMPHOME, 'ARIA', 'approval.json')
    with open(path, encoding='utf-8') as f:
        assert json.load(f)['mode'] == 'confirm-risky'
    assert approval.get_mode() == 'confirm-risky'
    assert 'Approval mode: confirm-risky' in approval.tool_get_approval_mode()
    bad = approval.tool_set_approval_mode('bogus')
    assert 'must be one of' in bad, bad
    assert approval.get_mode() == 'confirm-risky'
    approval.tool_set_approval_mode('auto')

def test_trusted_routine_bypass_vs_untrusted():
    fresh_turn()
    approval.tool_set_approval_mode('auto')
    routines.tool_routine_record_start('risky_run')
    dispatch.execute_tool('test_destructive', {'target': 'boom'})
    routines.tool_routine_record_stop()
    fresh_turn()
    approval.tool_set_approval_mode('confirm-risky')
    out = routines.tool_run_routine('risky_run', '{}')
    assert 'AWAITING_APPROVAL' in out, out
    assert 'DESTROYED' not in out, out
    assert 'now trusted' in routines.tool_trust_routine('risky_run')
    fresh_turn()
    out2 = routines.tool_run_routine('risky_run', '{}')
    assert 'DESTROYED:boom' in out2, out2
    assert 'AWAITING_APPROVAL' not in out2, out2
    assert 'now untrusted' in routines.tool_untrust_routine('risky_run')
    fresh_turn()
    out3 = routines.tool_run_routine('risky_run', '{}')
    assert 'AWAITING_APPROVAL' in out3, out3
    routines.tool_delete_routine('risky_run')
    approval.tool_set_approval_mode('auto')

def test_hook_snippets_documented():
    src_dir = PKG
    with open(os.path.join(src_dir, 'approval.py'), encoding='utf-8') as f:
        a_src = f.read()
    with open(os.path.join(src_dir, 'routines.py'), encoding='utf-8') as f:
        r_src = f.read()
    assert 'from aria import approval as _approval_mod' in a_src, 'approval hook snippet missing'
    assert '_approval_mod.needs_approval(fn_name, args or {})' in a_src
    assert 'return _approval_mod.request_approval(fn_name, args or {}), False' in a_src
    assert 'from aria import routines as _routines_mod' in r_src, 'capture hook snippet missing'
    assert '_routines_mod.maybe_capture(fn_name, args or {})' in r_src
    for label, src in (('approval', a_src), ('routines', r_src)):
        for line in src.splitlines():
            if line.startswith('import ') or line.startswith('from '):
                assert 'dispatch' not in line and 'brain' not in line, f'{label}: bad module-level import: {line}'

def test_real_aria_untouched():
    real = os.path.expanduser('~')
    assert real == _TMPHOME, real
    assert not os.path.exists('/root/ARIA'), 'wrote to real /root/ARIA!'
try:
    shutil.rmtree(_TMPHOME, ignore_errors=True)
except Exception:
    pass
